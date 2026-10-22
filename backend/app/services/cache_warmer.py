"""缓存预热器 (cache warmer) - 提前/空闲期把高热度看板数据装载进 Redis

背景:
  今日/周运营看板访问热度高, 首次访问(冷缓存)要跑多个重聚合 SQL(today_staff
  含 DISTINCT ON 关联 CTE、complaint_detail 三源合并), 用户感知慢。本模块在三个
  时机主动填充缓存, 让常规访问命中热缓存:

    1. 同步完成后 (sync_task_manager 失效缓存后) → 立即预热今日视图(数据刚更新)
    2. 服务启动后 → 延迟预热今日视图, 让首个用户命中热缓存
    3. 调度器空闲期 (白天每分钟自检, 非同步中) → 预热今日+周视图, 保持热缓存常驻

设计要点:
  - WARM_TTL=180s > 路由层 60/90s: 单次预热覆盖 3min 热窗口。
  - 节流: 仅当 key 剩余 TTL ≤ 60s(或不存在)才重算并刷新, 否则跳过 →
    白天每分钟自检, 实际约每 2min 重算一次, 数据始终 ≤2min 新鲜且永不冷加载。
  - 直接调用 service 函数 (analytics A.*) 与 complaint 路由的 __wrapped__ 原函数,
    用 cache_set_json 填充缓存; key 字符串与 analytics.py / complaints.py 路由层
    逐字符对齐 —— 改路由 key 时务必同步改本文件 _warm_analytics / _warm_complaints。
  - 全程 try/except: 单接口失败不影响其余; 预热本身失败不影响业务。
  - Redis 不可用 → 跳过 (无意义); 同步进行中 → 跳过 (避免与写入抢 DB)。
  - 进程级单飞锁防并发预热重复计算。
"""

import logging
import threading
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Callable

from app.core.database import SessionLocal
from app.core.redis_client import cache_invalidate_pattern, cache_set_json, get_redis

logger = logging.getLogger(__name__)

TZ_CN = timezone(timedelta(hours=8))

# 预热条目 TTL(秒): > 路由层 60/90s, 让单次预热覆盖更长热窗口
WARM_TTL = 180
# 重算节流阈值: key 剩余 TTL > 此值时跳过重算(仍新鲜)
FRESH_TTL_THRESHOLD = 60
# 仅白天预热(夜间无人访问, 省计算); 0~7 点跳过
DAYTIME_HOURS = range(8, 24)


# 进程级单飞锁: 同一时刻只允许一个预热在跑
_warm_lock = threading.Lock()


def _today_str() -> str:
    return datetime.now(TZ_CN).strftime("%Y-%m-%d")


def _days_ago_str(n: int) -> str:
    return (datetime.now(TZ_CN) - timedelta(days=n)).strftime("%Y-%m-%d")


def _dt(s: str | None):
    """与 analytics._parse_dt 一致: ISO 字符串(YYYY-MM-DD) → naive datetime"""
    return datetime.fromisoformat(s) if s else None


def _week_range() -> tuple[str, str]:
    """本周一~周日 (YYYY-MM-DD), 与前端 getWeekRange 一致的自然周"""
    now = datetime.now(TZ_CN)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    sunday = monday + timedelta(days=6)
    return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")


def _can_warm() -> bool:
    """Redis 可用 且 当前无同步任务在跑(避免与写入抢 DB)"""
    if get_redis() is None:
        return False
    try:
        from app.services.sync_task_manager import sync_task_manager
        if sync_task_manager.is_busy():
            return False
    except Exception:
        pass
    return True


def _warm_one(key: str, fn, args: tuple, kwargs: dict, name: str, *, force: bool = False) -> bool:
    """预热单个 key。返回 True=已重算并写入缓存, False=跳过(仍新鲜)或失败。

    - force=True: 无条件重算并写缓存(同步后数据已变, 必须刷新)
    - force=False: 仅当 key 剩余 TTL ≤ FRESH_TTL_THRESHOLD(或不存在)才重算,
      仍新鲜则跳过 → 节流, 避免每分钟都重算

    ── #92 可观测性 (2026-09-14) ──────────────────────────────────────
    ⚠️ 本函数的 `except` 把预热异常降级为**一条 WARNING** —— 这在运维上是
       "静默失败"：生产曾连续 5 天 `complaint:subject` 预热全败，而**没有任何
       指标**暴露它，最终表现为「接口变慢但不报错」（实测那两个投诉接口
       115 次调用 **0 次缓存命中**、耗时 0.19~0.25s，而对照组的
       `analytics/today/kpi` 因命中缓存耗时 0.000s）。

    为此增加**结构化结果记录**：每次预热把 (name, outcome) 记入线程局部
    累加器，由 `_run` 在收尾时汇总上报（见 `_warm_stats_report`）。
    outcome ∈ {"warmed", "fresh", "failed"}。
    """
    r = get_redis()
    if r is not None and not force:
        try:
            ttl = r.ttl(key)  # -2=不存在, -1=无过期, 否则剩余秒
            if isinstance(ttl, int) and ttl > FRESH_TTL_THRESHOLD:
                _record_warm_outcome(name, "fresh")
                return False  # 仍新鲜, 跳过重算
        except Exception:
            pass  # ttl 查询失败 → 兜底重算
    try:
        result = fn(*args, **kwargs)
        if result is not None:
            # 返回 cache_set_json 的实际写入结果(True=已写入缓存),
            # Redis 不可用时 cache_set_json 返回 False → warmed 计数准确
            ok = cache_set_json(key, result, WARM_TTL)
            _record_warm_outcome(name, "warmed" if ok else "failed")
            return ok
        _record_warm_outcome(name, "failed")
        return False
    except Exception as e:  # noqa: BLE001  单接口失败不影响其余
        logger.warning("预热 %s 失败(不影响业务): %s", name, e)
        _record_warm_outcome(name, "failed", err=e)
        return False


# ── 预热结果累加器 (#92) ────────────────────────────────────────────
# 为什么用模块级 dict 而非「函数返回值层层透传」：
#   `_warm_*` 系列函数已返回「成功条数」且被多处复用（今日/本周/月报视图），
#   改动签名会波及全部调用方。用**单进程累加器**最小侵入：
#   `_run()` 在开始前 reset、结束时 report（单 worker 部署，见 AGENTS.md）。
_WARM_STATS: dict[str, list] = {"warmed": [], "fresh": [], "failed": []}


def _record_warm_outcome(name: str, outcome: str, err: Exception | None = None) -> None:
    """记录单次预热结果（供 `_warm_stats_report` 汇总）。

    仅记录**失败项名称与异常类型**，不记录异常详情 —— 避免日志膨胀
    （这正是 #92 的另一个教训：6792 条同型告警占了 app.log 的 28.9% 字节）。
    """
    if outcome == "failed":
        label = f"{name}({type(err).__name__})" if err is not None else name
        _WARM_STATS["failed"].append(label)
    else:
        _WARM_STATS[outcome].append(name)


def _warm_stats_report(label: str) -> None:
    """预热收尾汇总上报 (#92)。

    把 N 条孤立 WARNING 聚合成**一条** INFO/WARNING：
      · 有失败 → WARNING（含失败清单，便于一眼定位是哪个 key 长期不命中）
      · 全成功 → INFO（含 warmed/fresh 数量，用于确认节流是否生效）
    这是「预热失败必须可见」的落地 —— 不再依赖人工翻日志发现。
    """
    warmed = len(_WARM_STATS["warmed"])
    fresh = len(_WARM_STATS["fresh"])
    failed = len(_WARM_STATS["failed"])
    total = warmed + fresh + failed
    if total == 0:
        return
    if failed:
        # 失败清单去重并限长，避免日志被撑爆（同型失败只留一个样本）
        uniq = sorted(set(_WARM_STATS["failed"]))
        shown = uniq[:10]
        more = f" 等 {len(uniq)} 项" if len(uniq) > 10 else ""
        logger.warning(
            "预热汇总(%s): 共 %d 项 → 写入 %d / 仍新鲜 %d / **失败 %d** ← 失败项: %s%s",
            label, total, warmed, fresh, failed, ", ".join(shown), more,
        )
    else:
        logger.info("预热汇总(%s): 共 %d 项 → 写入 %d / 仍新鲜 %d / 失败 0",
                    label, total, warmed, fresh)


def _warm_analytics(db, start: str, end: str, grain: str, label: str, *, force: bool = False) -> int:
    """预热今日看板 7 个 analytics 接口。返回成功预热条数。

    today 视图: start=end=今日, grain=hour
    week 视图: start=周一, end=周日, grain=day

    key 与 analytics.py 路由层逐字符对齐 (date 参数前端不传 → None)。
    """
    from app.services import analytics as A

    specs = [
        # (name, key, fn, args, kwargs)
        ("today:kpi",
         f"today:kpi:None:{start}:{end}",
         A.today_kpi, (db, None, start, end), {}),
        ("today:hourly",
         f"today:hourly:None:{start}:{end}:{grain}",
         A.today_hourly_trend, (db, None, start, end), {"trend_grain": grain}),
        ("today:reception",
         f"today:reception:None:{start}:{end}",
         A.today_reception_breakdown, (db, None, start, end), {}),
        ("today:refund",
         f"today:refund:None:{start}:{end}",
         A.today_refund_breakdown, (db, None, start, end), {}),
        ("today:queue",
         f"today:queue:None:{start}:{end}",
         A.today_queue, (db, None, start, end), {}),
        ("today:staff",
         f"today:staff:None:{start}:{end}:200:0:person:None:None:None",
         A.today_staff_efficiency, (db, None, start, end, 200, False, "person"),
         {"department": None, "group_id": None, "specialty_id": None}),
        ("today:rr",
         f"today:rr:None:{start}:{end}",
         A.refund_recovery_analysis, (db, None, start, end), {}),
    ]
    return sum(
        _warm_one(key, fn, args, kw, f"{label}:{name}", force=force)
        for name, key, fn, args, kw in specs
    )


def _warm_complaints(
    db, start: str, end: str, detail_limit: int, label: str,
    *, backlog_start: str | None = None, force: bool = False,
) -> int:
    """预热今日看板 complaint 接口 (summary/detail/subject-analysis/backlog)。返回成功预热条数。

    调用路由函数的 __wrapped__ 原函数(cached_json 装饰前的本体)计算,
    再用 cache_set_json 写 WARM_TTL 缓存 —— 绕开装饰器内置 TTL 以获得更长热窗口。
    key 与 complaints.py 路由层逐字符对齐。
    """
    from app.api.v1.complaints import (
        complaint_analysis,
        complaint_backlog,
        complaint_detail,
        complaint_health_metrics_endpoint,
        complaint_subject_analysis,
        complaint_summary,
    )

    n = _warm_one(
        f"complaint:summary:{start}:{end}",
        complaint_summary.__wrapped__, (db, None), {"start": start, "end": end},
        f"{label}:complaint:summary", force=force,
    )
    # ★ #87 投诉健康度指标（活跃SN/投诉率三维/三岗位负荷/重复投诉率）
    #   ⚠️ key 必须与 `complaints.complaint_health_metrics_endpoint` 的
    #      `f"complaint:health:{date}:{start}:{end}"` **逐字符对齐**
    #      （本模块注释开头已声明该约定）。
    n += _warm_one(
        f"complaint:health:None:{start}:{end}",
        complaint_health_metrics_endpoint.__wrapped__, (db, None),
        {"date": None, "start": start, "end": end},
        f"{label}:complaint:health", force=force,
    )
    n += _warm_one(
        # ⚠️ key 形状须与路由 `complaint:detail:{start}:{end}:{source_type}:{subject}:{limit}:{bool}`
        #    对齐 —— 曾漏 `subject`（None）段 → 预热写进的 key 与路由读的 key
        #    不一致，**缓存永不命中**（且不报错）。由
        #    `tests/test_cache_warmer_alignment.py` 守护。
        f"complaint:detail:{start}:{end}:None:None:{detail_limit}:1",
        complaint_detail.__wrapped__, (db, None),
        {"start": start, "end": end, "source_type": None, "subject": None,
         "limit": detail_limit, "include_supplementary": True},
        f"{label}:complaint:detail", force=force,
    )
    # 被投诉主体分析 + 投诉决策分析(看板高频, 历史无缓存缺口): 与 summary 同窗口
    # ⚠️ #67 P2 (2026-09-11) 给 `complaint_subject_analysis` 加了 4 个查询参数
    #    (`source_type`/`sort_by`/`top_n`/`compare`)，但**本处未同步更新** →
    #    缺省值变成 `Query(...)` 对象 → 内部比较时报
    #    `'>' not supported between instances of 'Query' and 'int'` →
    #    预热**每次都静默失败**（被 `_warm_one` 的 try/except 吞掉）。
    #    2026-09-14 修复：key 与 kwargs 都补齐默认值，形状与路由 `cached_json` 对齐。
    #    ⚠️ 后续再给该接口加参数时，**两处必须同步改**。
    n += _warm_one(
        f"complaint:subject:{start}:{end}:all:total:0:none",
        complaint_subject_analysis.__wrapped__, (db, None),
        {"start": start, "end": end,
         "source_type": "all", "sort_by": "total", "top_n": 0, "compare": "none"},
        f"{label}:complaint:subject", force=force,
    )
    n += _warm_one(
        f"complaint:analysis:{start}:{end}",
        complaint_analysis.__wrapped__, (db, None), {"start": start, "end": end},
        f"{label}:complaint:analysis", force=force,
    )
    # backlog 固定近30天窗口(与视图无关), 只需预热一次 → 由调用方传 backlog_start
    if backlog_start:
        n += _warm_one(
            f"complaint:backlog:{backlog_start}:{end}:None:None:None:None:1:50:aging_asc",
            complaint_backlog.__wrapped__, (db, None),
            {
                "start": backlog_start, "end": end, "source_type": None, "status": None,
                "category": None, "has_handler": None, "page": 1, "page_size": 50, "sort": "aging_asc",
            },
            f"{label}:complaint:backlog", force=force,
        )
    return n


def _warm_today_view(db, *, force: bool = False) -> int:
    """今日视图: 7 analytics + 3 complaint(backlog 固定近30天)。返回成功预热条数"""
    t = _today_str()
    t30 = _days_ago_str(30)
    return _warm_analytics(db, t, t, "hour", "今日", force=force) + \
        _warm_complaints(db, t, t, 500, "今日", backlog_start=t30, force=force)


def _warm_week_view(db, *, force: bool = False) -> int:
    """本周视图: 7 analytics + 2 complaint(detail 限1000; backlog 已在今日视图预热)。返回成功预热条数"""
    ws, we = _week_range()
    return _warm_analytics(db, ws, we, "day", "本周", force=force) + \
        _warm_complaints(db, ws, we, 1000, "本周", force=force)


def _warm_reports(db, *, force: bool = False) -> int:
    """预热运营/现金流/退款分类/人员操作/投诉决策看板(月报场景)。返回成功预热条数。

    含 静态(无窗口, 任意查看都命中) + 近30天窗口(月报常用) 两类端点。
    由 warm_after_sync 调用(每小时刷新一次, 数据同步后保持月报新鲜)。
    key 与 analytics.py / complaints.py 路由层逐字符对齐; 窗口端点 service 取 datetime(_dt)。
    """
    from app.api.v1.complaints import complaint_analysis, complaint_subject_analysis
    from app.services import analytics as A

    # #342: 三个页面(Operation/Cash/Operator)默认 `dayjs().subtract(30,'day')` ~ 今天,
    # 此前用 _days_ago_str(29)(差 1 天) → 预热 key 永不命中, 现对齐到 subtract(30)。
    s = _days_ago_str(30)   # 近30天窗口 = 前端默认(含端点共 31 天)
    e = _today_str()
    sd, ed = _dt(s), _dt(e)
    specs = [
        # 静态(无窗口, 任意查看都命中同一 key)
        # ⚠️ 2026-09-14 (#86): 已移除 `("summary", ...A.dashboard_summary)` ——
        #   该接口已删除(废弃 + 错误口径 + 前端零调用)，继续预热是纯浪费。
        ("op:status",      "op:status",        A.operation_status_dist,    (db,), {}),
        ("op:method",      "op:method",        A.operation_method_dist,    (db,), {}),
        ("op:reason:10",   "op:reason:10",     A.operation_reason_top,     (db, 10), {}),
        ("cash:breakdown", "cash:breakdown",   A.cash_out_breakdown,        (db,), {}),
        ("cash:merchant",  "cash:merchant",     A.merchant_cash_summary,     (db,), {}),
        ("cash:balance:20","cash:balance:20",  A.wallet_balance_top,        (db, 20), {}),
        ("cat:service",    "cat:service",       A.category_by_service_item, (db,), {}),
        ("cat:merchant:20","cat:merchant:20",  A.category_by_merchant,     (db, 20), {}),
        ("cat:package:20", "cat:package:20",    A.category_by_package,     (db, 20), {}),
        # 近30天窗口(月报常用)
        ("op:trend",        f"op:trend:{s}:{e}",          A.operation_trend,        (db, sd, ed), {}),
        ("cash:trend",      f"cash:trend:{s}:{e}",         A.cash_out_trend,         (db, sd, ed), {}),
        # #342: cash:wallet 两个调用方(RefundWithdrawal/Cash)都用 T 格式日期, 且
        # 前者由 _warm_rw_deep 用同窗口预热 → 这里不再预热 plain 日期形态(永不命中)。
        ("operator:ticket", f"operator:ticket:{s}:{e}",    A.operator_ticket_stats,  (db, sd, ed), {}),
        ("operator:audit",  f"operator:audit:{s}:{e}",    A.operator_audit_stats,   (db, sd, ed), {}),
        ("operator:refund", f"operator:refund:{s}:{e}",   A.operator_refund_stats,  (db, sd, ed), {}),
        # 投诉决策/被投诉主体看板(ComplaintAnalysis.vue 默认近30天: subtract(30)~today)
        ("complaint:analysis", f"complaint:analysis:{s}:{e}",
         complaint_analysis.__wrapped__, (db, None), {"start": s, "end": e}),
        ("complaint:subject",  f"complaint:subject:{s}:{e}:all:total:0:none",
         complaint_subject_analysis.__wrapped__, (db, None),
         {"start": s, "end": e,
          "source_type": "all", "sort_by": "total", "top_n": 0, "compare": "none"}),
    ]
    return sum(
        _warm_one(key, fn, args, kw, f"月报:{name}", force=force)
        for name, key, fn, args, kw in specs
    )


def _warm_reception(db, start: str, end: str, label: str, *,
                    group_start: str | None = None, group_end: str | None = None,
                    force: bool = False) -> int:
    """预热客服接待看板 4 个接口 (analytics.reception_*)。

    返回成功预热条数。key 与 analytics.py 路由层 ``f"reception:{name}:{start}:{end}"``
    逐字符对齐(缓存规范 §5.2 铁律)。

    - start/end: 接待看板大窗口(默认近 90 天, 与 ReceptionDashboard.vue 默认对齐)
    - group_start/group_end: group-detail 接口用近 7 天窗口
      (与 ReceptionDashboard.vue:710 「group-detail 用最近 7 天」约定对齐;
       大窗口下行数过多, 前端不展示全部)

    时间语义已确认无 8h 偏移(工单 #316 验收项):
      service 内部统一走 ``_date_range``(_core.py:191), naive 入参按北京时间
      解释转 UTC aware, 不存在 SCRM naive 当 UTC 的偏移风险。
    """
    from app.services import analytics as A

    # group-detail 用单独窗口(默认回落到主窗口)
    gs = group_start or start
    ge = group_end or end
    sd, ed = _dt(start), _dt(end)
    gsd, ged = _dt(gs), _dt(ge)
    specs = [
        # (name, key, fn, args, kwargs)
        ("reception:overview",
         f"reception:overview:{start}:{end}",
         A.reception_overview_trend, (db, sd, ed), {}),
        ("reception:catsum",
         f"reception:catsum:{start}:{end}",
         A.reception_category_summary, (db, sd, ed), {}),
        ("reception:percapita",
         f"reception:percapita:{start}:{end}",
         A.reception_per_capita, (db, sd, ed), {}),
        ("reception:group",
         f"reception:group:{gs}:{ge}",
         A.reception_group_detail, (db, gsd, ged), {}),
    ]
    return sum(
        _warm_one(key, fn, args, kw, f"{label}:{name}", force=force)
        for name, key, fn, args, kw in specs
    )


def _warm_refund_deep(db, start: str, end: str, label: str, *,
                      force: bool = False) -> int:
    """预热退款深度分析看板 8 个核心接口 (analytics.refund_*)。

    返回成功预热条数。key 与 analytics.py 路由层逐字符对齐(缓存规范 §5.2 铁律)。
    覆盖 RefundDashboard 月报场景默认入口(近 30 天); 单日/区间模式由路由层
    cached_call 按需回填, 不在预热范围。

    ⚠️ key 中的 ``None`` 是 Python None 经 f-string 转字符串 "None", 必须与
       路由层 ``f"refund:pa:{start}:{end}:{department}:..."`` 在 start/department 等
       为 None 时的输出**逐字符对齐** —— 工单 #316 验收项「grep 核对」。
    ⚠️ service 函数签名变更时, 本处 kwargs 必须同步(参见 test_cache_warmer_alignment.py)。
    """
    from app.services import analytics as A

    specs = [
        # (name, key, fn, args, kwargs)
        # refund:do (decision-overview) - 默认 grain=day
        ("refund:do",
         f"refund:do:{start}:{end}:day",
         A.refund_decision_overview, (db, start, end), {"grain": "day"}),
        # refund:gc (group-comparison) - 无额外参数
        ("refund:gc",
         f"refund:gc:{start}:{end}",
         A.refund_group_comparison, (db, start, end), {}),
        # refund:pa (personnel-analysis) - 默认无筛选
        ("refund:pa",
         f"refund:pa:{start}:{end}:None:None:None:None",
         A.refund_personnel_analysis, (db, start, end),
         {"department": None, "group": None, "specialty": None, "staff_type": None}),
        # refund:prm (person-reason-matrix) - top=15 默认
        ("refund:prm",
         f"refund:prm:{start}:{end}:15",
         A.refund_person_reason_matrix, (db, start, end), {"top": 15}),
        # refund:ptd (problem-type-detail) - top=10 默认
        ("refund:ptd",
         f"refund:ptd:{start}:{end}:10",
         A.refund_problem_type_detail, (db, start, end), {"top": 10}),
        # refund:tt (type-trend) - grain=day, dimension=reason 默认
        ("refund:tt",
         f"refund:tt:{start}:{end}:day:reason",
         A.refund_type_trend, (db, start, end), {"grain": "day", "dimension": "reason"}),
        # refund:da (device-analysis) - 默认参数(limit=20)
        ("refund:da",
         f"refund:da:{start}:{end}:None:None:None:None:20",
         A.refund_device_analysis, (db, start, end),
         {"reason": None, "amount_min": None, "amount_max": None,
          "reception_count_min": None, "limit": 20}),
        # refund:pt (personnel-trend) - grain=day, operators=None 默认
        ("refund:pt",
         f"refund:pt:{start}:{end}:None:day",
         A.refund_personnel_trend, (db, start, end), {"operators": None, "grain": "day"}),
    ]
    return sum(
        _warm_one(key, fn, args, kw, f"{label}:{name}", force=force)
        for name, key, fn, args, kw in specs
    )


def _warm_rw_deep(db, start: str, end: str, label: str, *,
                 force: bool = False) -> int:
    """#341 预热提现全链路看板核心接口 (analytics.refund_withdrawal_*)。

    返回成功预热条数。key 与 analytics.py 路由层逐字符对齐(缓存规范 §5.2 铁律)。

    ⚠️ #342 教训：**必须按前端实际请求形态生成 key**，否则预热写入的条目永不被命中
    （生产曾连续空转：预热写 plain 日期，而 RefundWithdrawal.vue 的 `rangeParams()`
    固定拼 `T00:00:00` / `T23:59:59`，两者 key 不同 → 每 2 分钟白算 4~35s）：
      · overview / daily-split / sleeping / cash:wallet → 带时间后缀的窗口
      · detail → `sort=d1_desc` + 第一页 + 8 个空筛选位（DecisionOverview.vue 默认值）
      · facets → DecisionOverview 用 plain 日期请求（无时间后缀）
      · time-distribution / dimensions → 前端零调用（仅 api 定义无使用），不预热
    其他分页/排序/筛选组合由路由层按需回填，不在预热范围（避免空间浪费）。
    """
    from app.services import analytics as A

    st = f"{start}T00:00:00"
    et = f"{end}T23:59:59"
    specs = [
        ("rw:overview", f"rw:overview:{st}:{et}",
         A.refund_withdrawal_overview, (db, st, et), {}),
        ("rw:dailysplit", f"rw:dailysplit:{st}:{et}",
         A.refund_withdrawal_daily_split, (db, st, et), {}),
        ("rw:sleeping", f"rw:sleeping:{st}:{et}:1:50",
         A.refund_withdrawal_sleeping, (db, st, et),
         {"page": 1, "page_size": 50}),
        # 同一页面 loadAll 并发请求, 窗口与 rw 域一致(T 格式), 一并预热
        # ⚠️ wallet_vs_withdraw 的签名收 datetime(路由层用 _parse_dt 转换), 传字符串会 AttributeError
        ("cash:wallet", f"cash:wallet:{st}:{et}",
         A.wallet_vs_withdraw, (db, _dt(st), _dt(et)), {}),
        ("rw:detail",
         f"rw:detail:{st}:{et}:d1_desc:1:50:0:ALL:all::::::::",
         A.refund_withdrawal_detail, (db, st, et),
         {"sort": "d1_desc", "page": 1, "page_size": 50, "slow_only": False,
          "path": "ALL", "comp": "all", "operator": "", "reason": "",
          "device_type": "", "combo": "", "merchant": "",
          "active_start": "", "active_end": "", "active_age": ""}),
        # facets 由 DecisionOverview.vue 以 plain 日期(YYYY-MM-DD)请求
        ("rw:facets", f"rw:facets:{start}:{end}",
         A.refund_withdrawal_facets, (db, start, end), {}),
    ]
    return sum(
        _warm_one(key, fn, args, kw, f"{label}:{name}", force=force)
        for name, key, fn, args, kw in specs
    )


def _run(label: str, do_warm) -> None:
    """单飞执行一次预热(进程级锁防并发重复计算)

    do_warm: 接收 db 参数的闭包, 内部自行决定是否强制重算(force)。
    _can_warm() 始终检查(Redis 可用 + 非同步中) —— 不能在同步进行中抢 DB。

    #92: 收尾时输出**汇总**（成功/跳过/失败计数 + 失败清单），
         让「预热静默失败」不可能再无人知晓。
    """
    if not _can_warm():
        logger.debug("预热跳过(%s): Redis 不可用或同步进行中", label)
        return
    if not _warm_lock.acquire(blocking=False):
        logger.debug("预热跳过(%s): 已有预热在跑", label)
        return
    started = datetime.now(TZ_CN)
    # 复位累加器（本次预热的统计口径仅覆盖本轮）
    _WARM_STATS["warmed"].clear()
    _WARM_STATS["fresh"].clear()
    _WARM_STATS["failed"].clear()
    db = SessionLocal()
    try:
        do_warm(db)
        _warm_stats_report(label)   # #92: 先报明细汇总（有失败时升 WARNING）
        logger.info("预热完成(%s): 耗时 %.1fs", label, (datetime.now(TZ_CN) - started).total_seconds())
    except Exception as e:  # noqa: BLE001  预热本身失败不影响业务
        logger.warning("预热异常(%s, 不影响业务): %s", label, e)
    finally:
        db.close()
        _warm_lock.release()


def warm_after_sync() -> None:
    """同步完成后预热: 今日视图 + 月报看板 + 提现全链路 (数据同步后强制刷新)

    今日视图: 同步主要影响当日数据, 强制刷新。
    月报看板: 运营/现金流/退款分类/人员操作(近30天+静态), 同步后保持月报新鲜。
    #341: 提现全链路(rw:*)读 BMS 多表, 同步后数据变动必须强制刷新
          (rw 域冷加载 5-13s, 不刷新的话下次访问必然白屏)。
    """
    t = _today_str()
    s29 = _days_ago_str(29)   # 提现全链路默认窗口 = 前端 last30(today-29 ~ today), 30 个自然日

    def _do(db):
        _warm_today_view(db, force=True)
        _warm_reports(db, force=True)
        # #341: rw 域同步后强制刷新(近30天窗口, 与月报同口径)
        _warm_rw_deep(db, s29, t, "同步后", force=True)
    _run("同步后", _do)


def warm_on_startup() -> None:
    """服务启动后预热: 今日视图 (让首个用户命中热缓存)"""
    _run("启动", lambda db: _warm_today_view(db, force=True))


def warm_idle() -> None:
    """空闲期预热: 今日 + 本周视图 + 接待/退款深度看板 (白天, 非同步中, 节流刷新)

    由 bms_scheduler 主循环每分钟调用; 白天/空闲/缓存将过期时才真正重算,
    其余情况快速跳过(ttl 检查 + 单飞锁)。

    #316: 接待/退款深度看板(reception:*/refund:*)原先仅路由层 60s TTL 按需回填,
    首次访问冷加载 2-4s。现纳入节流预热(白天每分钟自检, TTL≤60 才重算),
    实测无 8h 偏移后可考虑进 warm_after_sync(本变更仅先纳 warm_idle 降风险)。
    """
    if datetime.now(TZ_CN).hour not in DAYTIME_HOURS:
        return  # 夜间无人访问, 省计算

    # 接待看板默认近 90 天(与 ReceptionDashboard.vue rangeMode='90' 对齐);
    # group-detail 单独用近 7 天(与前端 :710 约定对齐)
    rec_s, rec_e = _days_ago_str(89), _today_str()
    grp_s, grp_e = _days_ago_str(6), _today_str()
    # 退款深度看板默认近 30 天(月报场景, 与 _warm_reports 同窗口)
    rfd_s, rfd_e = _days_ago_str(29), _today_str()

    def _do(db):
        _warm_today_view(db)   # 节流: 仍新鲜则跳过
        _warm_week_view(db)
        _warm_reception(db, rec_s, rec_e, "空闲", group_start=grp_s, group_end=grp_e)
        _warm_refund_deep(db, rfd_s, rfd_e, "空闲")
        # #341: 提现全链路看板(慢查询 5-13s) 纳入空闲预热
        _warm_rw_deep(db, rfd_s, rfd_e, "空闲")

    _run("空闲", _do)


def start_warm_loop():
    """独立启动缓存预热后台循环(不依赖 BMS 调度器)。

    背景(2026-08-25): 此前 warm_on_startup / warm_idle 挂在 bms_scheduler.start()
    与调度器主循环里, 当 BMS_SCHEDULE_AUTOSTART=False(调度器不启动)时永不执行 →
    看板缓存只能靠路由层 60s TTL 按需回填, 过期后再访问即冷加载重查询, 负载高时易超时。
    本函数在 lifespan 中直接启动: 延迟 8s 做启动预热(force), 之后每分钟空闲预热(白天/非同步/ttl节流)。
    若调度器也启动, 两边都会调 warm_idle, 由单飞锁 _warm_lock 互斥, 无重复重算。
    """
    import asyncio

    async def _loop():
        try:
            await asyncio.sleep(8)
            await asyncio.to_thread(warm_on_startup)
            # #123 AI 报告预生成: 启动延迟触发(白天 8-23 才跑, 夜间省计算)
            try:
                from app.services.ai_report_worker import warm_on_startup as ai_warm_startup
                await asyncio.to_thread(ai_warm_startup)
            except Exception:  # noqa: BLE001
                pass
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001  预热失败不影响服务
            pass
        while True:
            try:
                await asyncio.sleep(60)
                await asyncio.to_thread(warm_idle)
                # #123 AI 报告预生成: 每分钟扫描当前时刻匹配的 daily/weekly/monthly 模板
                try:
                    from app.services.ai_report_worker import warm_idle as ai_warm_idle
                    await asyncio.to_thread(ai_warm_idle)
                except Exception:  # noqa: BLE001
                    pass
                # #153 AI 提示词健康报告: 每日一次(当日未生成且白天), 落 system_events
                try:
                    from app.services.ai_feedback_service import (
                        check_daily_health_report_idle,
                    )
                    await asyncio.to_thread(check_daily_health_report_idle)
                except Exception:  # noqa: BLE001
                    pass
                # #163 数据治理体检: 每日低峰(0-5点)自动一次, 落 findings + system_events
                #   刻意避开 8-24 点同步窗口(设计 §九「不占同步窗口」)
                try:
                    from app.services.governance_scan_service import governance_idle
                    await asyncio.to_thread(governance_idle)
                except Exception:  # noqa: BLE001
                    pass
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001
                pass

    try:
        return asyncio.get_running_loop().create_task(_loop())
    except Exception:  # noqa: BLE001
        return None


def is_warming() -> bool:
    """当前是否有预热在跑(供状态查询)"""
    return _warm_lock.locked()


# ── 管理员强制重建缓存 ────────────────────────────────────────────────
# scope → (要失效的 pattern 列表, 预热函数); 预热函数为 None 表示仅失效+组合预热
SCOPE_PATTERNS: dict[str, tuple[list[str], Callable | None]] = {
    "today":   (["today:*", "complaint:*"], _warm_today_view),
    "week":    (["today:*", "complaint:*"], _warm_week_view),
    "reports": (["op:*", "cash:*", "cat:*", "operator:*", "summary*"], _warm_reports),
    # #341: 提现全链路(rw:*)纳入全部缓存重建
    "all":     (
        ["today:*", "refund:*", "rw:*", "complaint:*",
         "op:*", "cash:*", "cat:*", "operator:*", "summary*"],
        None,  # all 在 rebuild_cache 内组合预热 today+week+reports
    ),
}
AVAILABLE_SCOPES = list(SCOPE_PATTERNS.keys())

SCOPE_LABELS = {
    "today": "今日看板(今日视图)",
    "week": "今日看板(本周视图)",
    "reports": "月报看板(运营/现金流/退款分类/人员操作)",
    "all": "全部看板缓存",
}


def rebuild_cache(scope: str, *, invalidate_only: bool = False) -> dict:
    """管理员强制重建缓存。返回统计 dict。

    流程: 先按 scope 失效对应 pattern, 再强制重算预热(force=True 绕开 ttl 节流)。
    - 不走空闲预热的 _can_warm 拦截(管理员明确要重建), 但保留单飞锁防与预热并发
      重复计算; 同步进行中返回提示(同步中重建会读到不一致中间态)。
    - invalidate_only=True: 仅失效不预热, 下次访问自然回填(路由层 cached_call)。
    """
    if scope not in SCOPE_PATTERNS:
        return {"ok": False, "error": f"未知范围: {scope}", "available": AVAILABLE_SCOPES}
    patterns, warm_fn = SCOPE_PATTERNS[scope]

    # 同步进行中: 不建议重建(读到不一致中间态)
    try:
        from app.services.sync_task_manager import sync_task_manager
        if sync_task_manager.is_busy():
            return {"ok": False, "busy": True, "error": "数据同步进行中, 请稍后再重建缓存"}
    except Exception:
        pass

    if not _warm_lock.acquire(blocking=False):
        return {"ok": False, "busy": True, "error": "另一个缓存预热/重建任务正在执行, 请稍后重试"}

    redis_ok = get_redis() is not None
    t0 = monotonic()
    invalidated = sum(cache_invalidate_pattern(p) for p in patterns)
    warmed = 0
    db = SessionLocal()
    try:
        if not invalidate_only:
            if scope == "all":
                # #342: 失效列表含 rw:*, 但此前 never 重算 → 重建后 rw 必然冷加载 5~9s
                warmed = (
                    _warm_today_view(db, force=True)
                    + _warm_week_view(db, force=True)
                    + _warm_reports(db, force=True)
                    + _warm_rw_deep(db, _days_ago_str(29), _today_str(), "重建", force=True)
                )
            elif warm_fn is not None:
                warmed = warm_fn(db, force=True)
    finally:
        db.close()
        _warm_lock.release()
    return {
        "ok": True,
        "scope": scope,
        "scope_label": SCOPE_LABELS.get(scope, scope),
        "invalidate_only": invalidate_only,
        "invalidated": invalidated,
        "warmed": warmed,
        "elapsed_ms": int((monotonic() - t0) * 1000),
        "redis_available": redis_ok,
    }

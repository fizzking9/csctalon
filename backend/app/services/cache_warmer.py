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
    """
    r = get_redis()
    if r is not None and not force:
        try:
            ttl = r.ttl(key)  # -2=不存在, -1=无过期, 否则剩余秒
            if isinstance(ttl, int) and ttl > FRESH_TTL_THRESHOLD:
                return False  # 仍新鲜, 跳过重算
        except Exception:
            pass  # ttl 查询失败 → 兜底重算
    try:
        result = fn(*args, **kwargs)
        if result is not None:
            # 返回 cache_set_json 的实际写入结果(True=已写入缓存),
            # Redis 不可用时 cache_set_json 返回 False → warmed 计数准确
            return cache_set_json(key, result, WARM_TTL)
        return False
    except Exception as e:  # noqa: BLE001  单接口失败不影响其余
        logger.warning("预热 %s 失败(不影响业务): %s", name, e)
        return False


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

    s = _days_ago_str(29)   # 近30天 = 月报窗口
    e = _today_str()
    s30 = _days_ago_str(30)  # 投诉决策看板默认窗口: 前端 dayjs().subtract(30,'day') ~ 今天
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
        ("cash:wallet",     f"cash:wallet:{s}:{e}",        A.wallet_vs_withdraw,     (db, sd, ed), {}),
        ("operator:ticket", f"operator:ticket:{s}:{e}",    A.operator_ticket_stats,  (db, sd, ed), {}),
        ("operator:audit",  f"operator:audit:{s}:{e}",    A.operator_audit_stats,   (db, sd, ed), {}),
        ("operator:refund", f"operator:refund:{s}:{e}",   A.operator_refund_stats,  (db, sd, ed), {}),
        # 投诉决策/被投诉主体看板(ComplaintAnalysis.vue 默认近30天: subtract(30)~today)
        ("complaint:analysis", f"complaint:analysis:{s30}:{e}",
         complaint_analysis.__wrapped__, (db, None), {"start": s30, "end": e}),
        ("complaint:subject",  f"complaint:subject:{s30}:{e}:all:total:0:none",
         complaint_subject_analysis.__wrapped__, (db, None),
         {"start": s30, "end": e,
          "source_type": "all", "sort_by": "total", "top_n": 0, "compare": "none"}),
    ]
    return sum(
        _warm_one(key, fn, args, kw, f"月报:{name}", force=force)
        for name, key, fn, args, kw in specs
    )


def _run(label: str, do_warm) -> None:
    """单飞执行一次预热(进程级锁防并发重复计算)

    do_warm: 接收 db 参数的闭包, 内部自行决定是否强制重算(force)。
    _can_warm() 始终检查(Redis 可用 + 非同步中) —— 不能在同步进行中抢 DB。
    """
    if not _can_warm():
        logger.debug("预热跳过(%s): Redis 不可用或同步进行中", label)
        return
    if not _warm_lock.acquire(blocking=False):
        logger.debug("预热跳过(%s): 已有预热在跑", label)
        return
    started = datetime.now(TZ_CN)
    db = SessionLocal()
    try:
        do_warm(db)
        logger.info("预热完成(%s): 耗时 %.1fs", label, (datetime.now(TZ_CN) - started).total_seconds())
    except Exception as e:  # noqa: BLE001  预热本身失败不影响业务
        logger.warning("预热异常(%s, 不影响业务): %s", label, e)
    finally:
        db.close()
        _warm_lock.release()


def warm_after_sync() -> None:
    """同步完成后预热: 今日视图 + 月报看板 (数据同步后强制刷新)

    今日视图: 同步主要影响当日数据, 强制刷新。
    月报看板: 运营/现金流/退款分类/人员操作(近30天+静态), 同步后保持月报新鲜。
    """
    def _do(db):
        _warm_today_view(db, force=True)
        _warm_reports(db, force=True)
    _run("同步后", _do)


def warm_on_startup() -> None:
    """服务启动后预热: 今日视图 (让首个用户命中热缓存)"""
    _run("启动", lambda db: _warm_today_view(db, force=True))


def warm_idle() -> None:
    """空闲期预热: 今日 + 本周视图 (白天, 非同步中, 节流刷新)

    由 bms_scheduler 主循环每分钟调用; 白天/空闲/缓存将过期时才真正重算,
    其余情况快速跳过(ttl 检查 + 单飞锁)。
    """
    if datetime.now(TZ_CN).hour not in DAYTIME_HOURS:
        return  # 夜间无人访问, 省计算

    def _do(db):
        _warm_today_view(db)   # 节流: 仍新鲜则跳过
        _warm_week_view(db)

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
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001  预热失败不影响服务
            pass
        while True:
            try:
                await asyncio.sleep(60)
                await asyncio.to_thread(warm_idle)
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
    "reports": (["op:*", "cash:*", "cat:*", "operator:*", "summary"], _warm_reports),
    "all":     (
        ["today:*", "refund:*", "complaint:*", "op:*", "cash:*", "cat:*", "operator:*", "summary"],
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
                warmed = (
                    _warm_today_view(db, force=True)
                    + _warm_week_view(db, force=True)
                    + _warm_reports(db, force=True)
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

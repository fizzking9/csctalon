"""AI 报告预生成 worker (工单 #121)

设计:
- 复用 cache_warmer 三时机设计, 但 LLM 调用与缓存路由独立
- 三时机:
  ① 同步完成后(sync_task_manager._run_background finally) 触发 refresh_on_sync 模板
  ② 服务启动后延迟 8s(start_warm_loop) **只补「当前该有的报告」**: refresh_on_sync
     模板(日报类)直接生成, 其余(周报/月报)仅当 trigger_at 到点才生成(#166 修复)
  ③ 空闲期(bms_scheduler._run_loop 每分钟) 扫描 daily/weekly/monthly + trigger_at 匹配
- 进程级单飞锁 _worker_lock 防并发(单 worker, 保证互斥锁有效)
- 失败处理: 仅 log + 通知中心写 system_event(category=ai, status=failed), 不阻塞
- 参数分级: 后台预生成 timeout_s=600(#131 122b 慢模型放宽; 容得下 5000 token 自洽
  ~363s), max_tokens=template.max_tokens 按 period 分档(日报 2500/周 4000/月 5000,
  R-P1-4 / R3 长报告 ≥4000; 见 default_max_tokens_for_period)
- 落库到 ai_reports + is_public=True + template_id + snapshot_date

#121 不接 sync_task_manager / bms_scheduler(留待 #123 调度仲裁层);
本工单只实现 worker 函数本身, 暴露 schedule_due_templates / generate_for_template / warm_*
供后续工单调用。

#123 将在 llm_scheduler.py 实现优先级队列, 与本 worker 解耦。
"""

import asyncio
import datetime as _dt
import json
import logging
import threading

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.ai_report_template import AiReportTemplate
from app.services import llm_client
from app.services.ai_feed import build_messages
from app.services.ai_pregen_freshness import (
    evaluate_pregen_skip,
    record_pregen_skipped_event,
)
from app.services.ai_report_service import save_report
from app.services.ai_report_validator import validate_pregen_report
from app.services.ai_report_window import compute_pregen_window
from app.services.system_event_service import record_system_event

logger = logging.getLogger(__name__)

# 时区(AGENTS.md 铁律: 时区换算走 Asia/Shanghai)
_TZ_CN = _dt.timezone(_dt.timedelta(hours=8))

# 白天 8-23 才预热(与 cache_warmer.DAYTIME_HOURS 同步, 夜间无人访问省计算)
_DAYTIME_HOURS = set(range(8, 24))

# #129 日报随高频同步刷新的最小间隔保护(分钟):
# 高频同步窗口 8-24 点每 30min 一轮, 设 20min 略短于同步周期 —— 正常每轮同步
# 都会触发刷新; 仅当手动频繁同步(同步自身有 5min 冷却)或投诉推送恰好同时完成时,
# 用此间隔挡掉过近的重复 LLM 调用。仅作用于"同步触发 daily"路径; sync-triggered
# 模板不受限, warm_idle 09:00 定时兜底也走另一条路径不受此约束。
_DAILY_SYNC_MIN_INTERVAL_MIN = 20

# 采集后 AI 分类的最小间隔（分钟）：同步每 30min 一轮，设 30min 保证每轮最多跑一次
_CLASSIFY_MIN_INTERVAL_MIN = 30

# #166 启动预热的最小间隔保护(分钟): 服务重启会触发 warm_on_startup, 若短时间多次
# 重启(如连续部署/异常恢复)会把「当日日报」重复重算。设 20min 与同步节流同量级:
# 正常一次部署不受影响(距上次生成通常远超 20min), 但连续部署风暴被挡住。
# ⚠️ 仅作用于「启动预热」路径; warm_idle 按 trigger_at 到点触发不走此约束。
_STARTUP_MIN_INTERVAL_MIN = 20

# 进程级单飞锁(防并发: 同步完成 + 启动 + 空闲期可能同时触发)
_worker_lock = threading.Lock()

# ── #189 (2026-09-24) feed 字符预算分级 ────────────────────────────────────
# 原实现所有路径共用 settings.AI_ANALYZE_MAX_CHARS(=3500), 导致预生成 feed 被按字符
# 前缀截断, 业务段(四路/涉诉/三源/E 对账)必被切掉(生产实测: 模板 1 完整 6522 字符,
# 模板 10 完整 10344 字符, 都只下发 3514)。
# 现按「实测全量 + 余量」设定, 使当前数据量下**无任何段被省略**(数据增长后由
# ai_feed._plan_feed_sections 的优先级裁剪兜底, 业务段恒必保):
#   daily      : 实测 6.6k(接待+退款+投诉+口径+时间序列) → 7000
#   weekly     : 实测 5.4k                                → 6500
#   rolling_7d : 实测 6.1k                                → 6500
#   monthly    : 实测 9.1k(其中 daily_trend 4.2k)         → 9500
#   rolling_30d: 实测 10.3k(其中 daily_trend 5.4k)        → 10500
# 实时问答不传该值(保持 AI_ANALYZE_MAX_CHARS=3500: 用户在前端等 60s, 输入越长 prefill 越慢)。
# 推理侧上下文已确认充足: ollama OLLAMA_CONTEXT_LENGTH=65536, 10k 字符 prompt 无压力。
_FEED_LIMIT_BY_PERIOD = {
    "daily": 7000,
    "weekly": 6500,
    "monthly": 9500,
    "rolling_7d": 6500,
    "rolling_30d": 10500,
}


def _now_cn() -> _dt.datetime:
    """当前北京时间(AGENTS.md 铁律: 时区换算)。"""
    return _dt.datetime.now(_TZ_CN)


def schedule_due_templates(db: Session) -> list[AiReportTemplate]:
    """扫描 ai_report_templates, 返回当前时刻应当触发的模板列表。

    判定逻辑(#126 P0-3 修正 trigger_at 格式; #306 放宽 weekly/monthly 容差):
    - frequency=sync-triggered: 不在此扫描(由 warm_on_sync_complete 主动触发)
    - frequency=daily + trigger_at=HH:MM: 当前北京时间 HH:MM 在 ±1 分钟内
    - frequency=weekly + trigger_at=wday-HH:MM: 当天是周 wday 且时刻已达 HH:MM
      (#306: 由 ±1 分钟点匹配放宽为触发日当天; 当日已有成功快照则去重, 实现漏跑当补)
    - frequency=monthly + trigger_at=day-HH:MM: 当天是当月 day 号且时刻已达 HH:MM
      (同 #306 放宽 + 快照去重)

    格式说明(#126 P0-3):
    - 新格式(推荐): 用 `-` 分隔前缀与 HH:MM, 如 "1-09:00" (周一9点 / 每月1号9点)
    - 旧格式(兼容): 用 `:` 三段, 如 "1:09:00", 解析为 wday/day + HH:MM
    - daily 永远是 "HH:MM" (不含 `-`)

    返回按 priority 升序的模板列表(小先执行)。

    #236 (B2-1, 2026-10-09): 跳过 `period=daily` 模板 —— daily 报告改实时生成
    (走 L2 优先级, 用户等几秒), 不再走 warm_idle 定时预生成。weekly/monthly 不受影响。
    #306 (2026-10-12): rolling_7d/rolling_30d 模板停用(is_active=false), 自然不会
    被查出来; weekly/monthly 增加「触发日当天漏跑当补 + 当日快照幂等去重」。
    """
    now = _now_cn()
    now_wday = now.isoweekday()  # 1=周一, 7=周日
    now_day = now.day
    now_hm = now.strftime("%H:%M")

    rows = db.query(AiReportTemplate).filter(
        AiReportTemplate.is_active.is_(True),
        AiReportTemplate.frequency.in_(["daily", "weekly", "monthly"]),
    ).all()

    due: list[AiReportTemplate] = []
    for t in rows:
        if not t.trigger_at:
            continue
        # #236 B2-1: daily 报告改实时, 跳过 period=daily 的预生成触发
        if (t.period or "") == "daily":
            continue
        try:
            if t.frequency == "daily":
                # trigger_at = "HH:MM" (不含 `-`, 与 weekly/monthly 区分)
                if _hm_match(t.trigger_at, now_hm):
                    due.append(t)
            elif t.frequency == "weekly":
                # 新格式: "wday-HH:MM" (如 "1-02:00" = 周一凌晨2点)
                # 旧格式: "wday:HH:MM" (如 "1:09:00", #124 遗留, 兼容)
                # #306: 触发日当天时刻已达即 due(不再限 ±1 分钟), 漏跑当补
                wday, hm = _parse_wday_hhmm(t.trigger_at)
                if wday is not None and wday == now_wday and _trigger_time_reached(now, hm):
                    due.append(t)
            elif t.frequency == "monthly":
                # 新格式: "day-HH:MM" (如 "1-02:00" = 每月1号凌晨2点)
                # 旧格式: "day:HH:MM" (如 "1:09:00", #124 遗留, 兼容)
                # #306: 同 weekly, 触发日(每月 day 号)当天时刻已达即 due
                day, hm = _parse_wday_hhmm(t.trigger_at)
                if day is not None and day == now_day and _trigger_time_reached(now, hm):
                    due.append(t)
        except (ValueError, IndexError):
            logger.warning("ai_report_template(id=%s code=%s) trigger_at=%s 解析失败, 跳过",
                           t.id, t.code, t.trigger_at)
            continue

    # #306 漏跑补跑幂等去重: weekly/monthly 触发日当天若已存在成功公共快照
    # (02:00 正常跑过或早些时候已补过), 不再重复生成; 无快照才留在 due 里补跑。
    today = now.strftime("%Y-%m-%d")
    due = [
        t for t in due
        if not _has_public_snapshot_today(
            db, t.id, snapshot_period=t.period, snapshot_date=today)
    ]

    # 按 priority 升序(小先执行)
    due.sort(key=lambda x: x.priority)
    return due


def _parse_wday_hhmm(trigger_at: str) -> tuple[int | None, str]:
    """解析 weekly/monthly 的 trigger_at, 返回 (wday_or_day, HH:MM)。

    支持两种格式(#126 P0-3 兼容):
    - 新格式 "1-09:00" → (1, "09:00")  (用 `-` 分隔前缀与 HH:MM)
    - 旧格式 "1:09:00" → (1, "09:00")  (用 `:` 三段, #124 遗留)
    """
    if not trigger_at:
        return None, ""
    # 新格式: 含 `-` 且 `-` 在 HH:MM 之前
    if "-" in trigger_at:
        prefix, _, hm = trigger_at.partition("-")
        if ":" in hm:
            try:
                return int(prefix), hm
            except ValueError:
                return None, ""
    # 旧格式: 三段以 `:` 分隔 (如 "1:09:00")
    parts = trigger_at.split(":", 2)
    if len(parts) == 3:
        try:
            return int(parts[0]), f"{parts[1]}:{parts[2]}"
        except ValueError:
            return None, ""
    return None, ""


def _hm_match(trigger_hm: str, now_hm: str) -> bool:
    """HH:MM 精确点匹配(±1 分钟容差)。

    例: trigger="09:00", now="09:00" / "08:59" / "09:01" 都视为匹配
    """
    try:
        th, tm = map(int, trigger_hm.split(":"))
        nh, nm = map(int, now_hm.split(":"))
    except (ValueError, AttributeError):
        return False
    trigger_min = th * 60 + tm
    now_min = nh * 60 + nm
    return abs(now_min - trigger_min) <= 1


def _trigger_time_reached(now: _dt.datetime, trigger_hm: str) -> bool:
    """触发日当天, 当前时刻是否已达 trigger 的 HH:MM(下界保留 1 分钟容差)。

    #306 (2026-10-12): weekly/monthly 判定由 _hm_match 的「±1 分钟点匹配」放宽为
    「触发日当天到点即匹配」。配合 `_has_public_snapshot_today` 当日快照去重, 实现
    漏跑补生成: trigger_at 时刻因发布重启/服务停摆错过后, 当天 warm_idle 每分钟
    扫描都会重新命中, 直到该期报告成功落库; 已落库则当天不再重复生成(幂等)。

    为什么宽限只到「触发日当天」: compute_pregen_window 的退化逻辑保证, weekly 仅在
    周一、monthly 仅在 1 号当天生成时窗口才是「完整上周/上月」; 跨日补跑窗口会漂移
    成「本周一~昨天」, 与问句窗口(上周/上月)不再重叠, 命中层取不到。

    例: trigger="02:00", 同日 01:59(下界容差)/02:00/10:30 → True; 01:58 → False。
    """
    try:
        th, tm = map(int, trigger_hm.split(":"))
    except (ValueError, AttributeError):
        return False
    trigger_min = th * 60 + tm
    now_min = now.hour * 60 + now.minute
    return now_min >= trigger_min - 1


def _has_public_snapshot_today(db: Session, template_id: int, *,
                               snapshot_period: str, snapshot_date: str) -> bool:
    """#306: (模板, 周期, 快照日) 三元组下是否已存在成功的公共预生成报告。

    预生成仅在 success 时落库且 is_public=TRUE(见 generate_for_template),
    失败/被抢占/校验不过均不落库 —— 故「无快照」即「这期还没成功生成过」,
    作为漏跑补跑的幂等判据: 补成功一次后当天 warm_idle/warm_on_startup 不再重复。

    查询失败 fail-open 返回 False(宁可重复补一次, 也不因监控故障漏报)。
    """
    try:
        exists = db.execute(
            text("""SELECT EXISTS(
                      SELECT 1 FROM ai_reports
                      WHERE template_id = :tid AND is_public = TRUE
                        AND snapshot_period = :sp AND snapshot_date = :sd)"""),
            {"tid": template_id, "sp": snapshot_period, "sd": snapshot_date},
        ).scalar()
    except Exception as e:  # noqa: BLE001
        logger.warning("当日快照存在性查询失败(template_id=%s), 按未生成处理: %s",
                       template_id, e)
        return False
    return bool(exists)


async def _pregen_llm(messages: list[dict], db: Session, max_tokens: int, *,
                      source: str, level: int, provider_code: str | None = None) -> str:
    """#127 后台/手动预生成经优先门闸发起 LLM 调用。

    level 遵循 #127 工单优先级定义: 0=后台定时预生成(最高), 1=admin 手动触发。
    provider_code(#127 分流): 模板指定的 llm_provider_code, 进入该 provider 独立门闸;
    None 走 default 门闸。不同 provider 间并行不互斥。
    在**调用方线程自己的事件循环**上执行(保持 db 不跨线程); 被更高优先级抢占时抛
    CancelledError, 由 generate_for_template 捕获置"可重试跳过"。
    """
    from app.services.llm_scheduler import acquire, release
    handle = await acquire(level, name=source, provider_code=provider_code)
    try:
        result = await llm_client.chat_completion(
            messages,
            max_tokens=max_tokens,
            timeout_s=600.0,  # #131 后台预生成放宽到 600s(122b MoE 在 APU 上较慢)
            db=db,
            source=source,
            provider_code=provider_code,  # #131 指定专用慢模型(如 qwen3.5:122b)置顶+常规链兜底
        )
        return str(result)
    finally:
        await release(handle)


def _invoke_pregen_once(messages: list[dict], *, db: Session, template: AiReportTemplate,
                        source: str, level: int, provider_code: str | None) -> str | None:
    """单次预生成 LLM 调用的同步包装(R-P1-1 抽出, 供写库前校验失败时重试)。

    - 成功: 返回 answer 字符串。
    - 被更高优先级抢占: 抛 asyncio.CancelledError(调用方据此"本次跳过", **不重试**)。
    - 已在事件循环中(RuntimeError): 换新 loop 执行(原 generate_for_template 的兼容分支)。
    - 其余 LLM 异常: log + 写 pregen_llm_failed 事件, 返回 None(调用方放弃本次)。
    """
    async def _run() -> str:
        return await _pregen_llm(
            messages, db, template.max_tokens,
            source=source, level=level, provider_code=provider_code,
        )

    try:
        try:
            # asyncio.run 包装(chat_completion 是 async, worker 同步入口)。
            return asyncio.run(_run())
        except RuntimeError as e:
            # 已在事件循环中调用(如 bms_scheduler 直接 async 上下文): 换新 loop 跑。
            logger.warning("ai_report_worker: already in event loop, create new loop: %s", e)
            return asyncio.new_event_loop().run_until_complete(_run())
    except asyncio.CancelledError:
        # 后台/手动预生成被抢占不写失败事件, 仅 log(属正常抢占, 可重试跳过); 向上抛交调用方。
        logger.info("ai_report_worker 预生成被更高优先级抢占, 本次跳过(template=%s level=%s)",
                    template.code, level)
        raise
    except Exception as e:  # noqa: BLE001  LLM 调用失败不阻塞 worker
        logger.error("ai_report_worker LLM call failed (template=%s): %s", template.code, e)
        record_system_event(
            db, category="ai", action="pregen_llm_failed",
            title=f"AI 报告预生成 LLM 失败: {template.name_cn}",
            summary=f"template={template.code} err={e!s}",
            status="failed", level="error",
            detail=json.dumps({"template_id": template.id, "code": template.code, "error": str(e)},
                              ensure_ascii=False),
            dedup_minutes=30,
        )
        return None


def generate_for_template(db: Session, template: AiReportTemplate,
                          *, level: int = 2) -> dict | None:
    """为指定模板生成一份 AI 报告, 落库 is_public=True。

    level(#127 优先级): 0=后台定时预生成(最高, 可抢占 L1/L2), 1=admin 手动触发,
                         2=用户实时(默认出于兼容, 调用点显式传 0/1)。
    返回 {report_id, total_ms, llm_ms, total_tokens} 或 None(失败/被抢占跳过)。
    失败时: 仅 log + 写 system_event(category=ai, status=failed), 不抛异常。
    """
    t0 = _dt.datetime.now()
    now_cn = _now_cn()
    snapshot_date = now_cn.strftime("%Y-%m-%d")

    # 窗口变量(供 prompt 模板 {{date}} {{period}} 替换; 当前实现: period 直接用模板 period)
    period = template.period
    # question 用模板的 question_template(可含变量; 当前实现: 直接作为问句传入 build_messages)
    question = template.question_template

    # #132 批次 A: 周报/月报预生成窗口截止昨天整天(不含当天不完整数据)
    #   - daily: 保持 date=None 实时数据(refresh_on_sync 仍生效)
    #   - weekly/monthly: compute_pregen_window 算 start/end(昨日截止), build_messages
    #     走 period="range" + start/end 模式, _window 在 end<today 时返回整天 T23:59:59
    #     退化: 今日周一(weekly)/今日1号(monthly)→取上周/上月整天数据
    pregen_win = compute_pregen_window(period)
    if pregen_win is not None:
        # weekly/monthly: 走 start/end 模式(昨日截止)
        pregen_start = pregen_win["start"]
        pregen_end = pregen_win["end"]
        pregen_label = pregen_win["label"]
        build_period = "range"  # _window 走 start/end 任意区间分支
        build_date = None
    else:
        # daily: 保持现行 date=None 实时数据
        pregen_start = None
        pregen_end = None
        pregen_label = f"{period}(当日实时)"
        build_period = period
        build_date = None

    # 构造 messages(三层 Prompt: System/口径卡/Data+Question)
    try:
        messages = build_messages(
            db, period=build_period, date=build_date, question=question,
            start=pregen_start, end=pregen_end,
            window_label=(
                f"预生成(template={template.code}, snapshot={snapshot_date}, "
                f"{pregen_label})"
            ),
            window_source="template",
            # #152 预生成按模板 required_dimensions 维度全覆盖生成(加维度只改配置)
            required_dimensions=template.required_dimensions,
            # #181 专项分桶: 模板 scope/format 显式传入 → feed 按专项切片 + 体裁要求
            scope=template.scope,
            format=template.format,
            # #189 feed 预算按窗口粒度放宽(模板 period; 月/周报需容下 daily_trend 与业务段)
            feed_limit=_FEED_LIMIT_BY_PERIOD.get(template.period),
        )
    except Exception as e:  # noqa: BLE001  build_messages 失败不阻塞 worker
        logger.error("ai_report_worker build_messages failed (template=%s): %s", template.code, e)
        record_system_event(
            db, category="ai", action="pregen_build_messages_failed",
            title=f"AI 报告预生成 build_messages 失败: {template.name_cn}",
            summary=f"template={template.code} err={e!s}",
            status="failed", level="error",
            detail=json.dumps({"template_id": template.id, "code": template.code, "error": str(e)},
                              ensure_ascii=False),
            dedup_minutes=30,
        )
        return None

    # 调用 LLM(后台预生成: timeout_s=600 #131, max_tokens=template.max_tokens)
    # #127 经优先门闸发起(level=0/1); 抢占(CancelledError)由 _invoke_pregen_once 向上抛。
    llm_model_str = template.llm_provider_code or "auto"
    source = f"ai_pregen:{template.code}"
    # #127 多 provider 分流: 模板指定的 llm_provider_code 进入该 provider 独立门闸
    provider_code = template.llm_provider_code or None

    # R-P1-1 (#238) 写库前 schema/数值校验闸门: 首发 + 校验失败最多重试 1 次,
    # 对齐 governance_judge / onboarding_draft 的「validate→重试→仍失败不落库」。
    answer_md: str | None = None
    last_errors: list[str] = []
    for _attempt in (1, 2):
        try:
            _md = _invoke_pregen_once(
                messages, db=db, template=template, source=source,
                level=level, provider_code=provider_code,
            )
        except asyncio.CancelledError:
            # 被更高优先级抢占: 本次跳过, 不重试(抢占语义优先于校验重试)。
            return None
        if _md is None:
            # LLM 调用失败; pregen_llm_failed 事件已在 helper 内记录。
            return None
        _ok, _errs = validate_pregen_report(_md, template)
        if _ok:
            answer_md = _md
            break
        last_errors = _errs
        logger.warning("ai_report_worker 预生成校验未过(attempt=%s/2, template=%s): %s",
                       _attempt, template.code, _errs)
        if _attempt == 1:
            record_system_event(
                db, category="ai", action="pregen_validation_retry",
                title=f"AI 报告预生成校验未过, 重试一次: {template.name_cn}",
                summary=(f"template={template.code} errors={_errs!s}")[:500],
                status="failed", level="warning",
                detail=json.dumps({"template_id": template.id, "code": template.code,
                                   "attempt": 1, "errors": _errs}, ensure_ascii=False),
                dedup_minutes=30,
            )

    if answer_md is None:
        # 首发 + 重试均未过校验: 宁可缺这一份, 也不把残缺/缺维度/占位符报告公开落库。
        logger.error("ai_report_worker 预生成两次校验均失败, 放弃落库(template=%s): %s",
                     template.code, last_errors)
        record_system_event(
            db, category="ai", action="pregen_validation_failed",
            title=f"AI 报告预生成两次校验失败, 未落库: {template.name_cn}",
            summary=(f"template={template.code} errors={last_errors!s}")[:500],
            status="failed", level="error",
            detail=json.dumps({"template_id": template.id, "code": template.code,
                               "errors": last_errors}, ensure_ascii=False),
            dedup_minutes=30,
        )
        return None

    total_ms = int((_dt.datetime.now() - t0).total_seconds() * 1000)
    # _pregen_llm 返回 str(不含 usage 对象); 沿用现状 tokens 计账为 None
    # (超时/token 口径统一另见 B2-4/B2-5)。
    usage = None

    # 落库(is_public=True, template_id, snapshot_date)
    # user_id=1 表示系统生成的"管理员"报告(admin user); is_public=True 让所有人可见
    # #132 批次 A: weekly/monthly 落库 start/end(昨日截止), daily 保持 start/end=None
    report = save_report(
        db,
        task_id=None,  # 预生成无 ai_task_manager 任务 id
        user_id=1,  # 系统生成, 归属 admin user_id=1; 通过 is_public=True 让所有人可见
        question=question,
        period=period,
        date=snapshot_date,
        start=pregen_start, end=pregen_end,
        window_source="template",
        window_label=f"预生成({template.code}, {pregen_label})",
        answer_md=answer_md,
        llm_model=llm_model_str,
        llm_ms=total_ms,  # 简化: 全程算 llm_ms(后续可细分 build_ms + llm_ms)
        total_ms=total_ms,
        tokens=usage,
        template_id=template.id,
        snapshot_date=snapshot_date,
        snapshot_period=template.period,
        is_public=True,
    )

    if report is None:
        logger.error("ai_report_worker: save_report returned None (template=%s)", template.code)
        record_system_event(
            db, category="ai", action="pregen_save_failed",
            title=f"AI 报告预生成落库失败: {template.name_cn}",
            summary=f"template={template.code} save_report=None",
            status="failed", level="error",
            detail=json.dumps({"template_id": template.id, "code": template.code},
                              ensure_ascii=False),
            dedup_minutes=30,
        )
        return None

    logger.info("ai_report_worker: pregen success template=%s report_id=%s total_ms=%s",
                template.code, report.id, total_ms)
    record_system_event(
        db, category="ai", action="pregen_success",
        title=f"AI 报告预生成完成: {template.name_cn}",
        summary=f"template={template.code} report_id={report.id} total_ms={total_ms}ms",
        status="success", level="info",
        detail=json.dumps({"template_id": template.id, "report_id": report.id, "total_ms": total_ms},
                          ensure_ascii=False),
    )
    return {
        "report_id": report.id,
        "total_ms": total_ms,
        "llm_ms": total_ms,
        "total_tokens": usage.get("total_tokens") if usage else None,
    }


def _window_generated_recently(db: Session, template_id: int, *, snapshot_period: str,
                               snapshot_date: str, min_minutes: int) -> bool:
    """#166 按「快照窗口」节流的通用判据(daily/weekly/monthly 一致生效)。

    判据 = `(template_id, snapshot_period, snapshot_date)` 三元组 —— 即「同一份报告」。
    注意 `snapshot_date` 是**快照日**(generate_for_template 落库的当日日期), 不是报告
    窗口起算日; 报告窗口起止另存 `ai_reports.start/end`。

    调用方必须显式传入「本次即将生成的那份报告」的窗口:
      - 日报:      snapshot_period="daily",  snapshot_date=今天
      - 周报/月报: snapshot_period=模板 period, snapshot_date=今天(生成当日)

    #166 起替代原 `_daily_generated_recently`(该函数把判据写死为「snapshot_date=今天」
    且不看 period, 只服务日报同步刷新路径; 两个近似函数并存必然漂移, 故统一收敛)。

    created_at 为 tz-aware UTC(timestamptz), 与当前 UTC 比较; 对 naive 值补 UTC。
    查询失败 fail-open(返回 False=未节流), 宁可多刷一次也不漏。
    """
    try:
        latest = db.execute(
            text("""SELECT MAX(created_at) FROM ai_reports
                    WHERE template_id = :tid AND is_public = TRUE
                      AND snapshot_period = :sp AND snapshot_date = :sd"""),
            {"tid": template_id, "sp": snapshot_period, "sd": snapshot_date},
        ).scalar()
    except Exception as e:  # noqa: BLE001
        logger.warning("快照窗口节流查询失败(template_id=%s), 按未节流处理: %s", template_id, e)
        return False
    if latest is None:
        return False
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=_dt.timezone.utc)
    elapsed_s = (_dt.datetime.now(_dt.timezone.utc) - latest).total_seconds()
    return elapsed_s < min_minutes * 60


def _template_due_now(t: AiReportTemplate, now: _dt.datetime) -> bool:
    """#166 单模板「当前时刻是否到点」判定（供启动预热用，与 schedule_due_templates 同口径）。

    与 `schedule_due_templates` 的区别：后者扫描全表返回列表，本函数判单个模板，
    供 `warm_on_startup` 复用同一套 trigger_at 语义（避免两处判定漂移）。

    - frequency=sync-triggered: 启动预热**不触发**（由 warm_on_sync_complete 负责）
    - daily + "HH:MM": ±1 分钟点匹配(daily 模板在 warm_on_startup 已被 B2-1 前置跳过)
    - weekly + "wday-HH:MM" / monthly + "day-HH:MM":
      #306 起为「触发日当天时刻已达」(与 schedule_due_templates 同口径),
      配合 warm_on_startup 内当日快照去重实现漏跑当补。
    """
    if t.frequency == "sync-triggered" or not t.trigger_at:
        return False
    now_hm = now.strftime("%H:%M")
    try:
        if t.frequency == "daily":
            return _hm_match(t.trigger_at, now_hm)
        if t.frequency == "weekly":
            wday, hm = _parse_wday_hhmm(t.trigger_at)
            return (wday is not None and wday == now.isoweekday()
                    and _trigger_time_reached(now, hm))
        if t.frequency == "monthly":
            day, hm = _parse_wday_hhmm(t.trigger_at)
            return (day is not None and day == now.day
                    and _trigger_time_reached(now, hm))
    except (ValueError, IndexError):
        logger.warning("ai_report_template(id=%s code=%s) trigger_at=%s 解析失败",
                       t.id, t.code, t.trigger_at)
    return False


def warm_on_sync_complete() -> None:
    """同步完成触发入口(#129/#130): 刷新所有「随数据同步刷新」的 active 模板。

    #130 起候选不再按 frequency 硬编码, 而是读模板级开关 `refresh_on_sync`:
      - True(日报类, 时效性强): 每次高频同步成功后刷新, 加 20min 最小间隔保护
        (同步窗口 8-24 点每 30min 一轮, 正常每轮触发; 挡手动频繁同步/推送撞车)
      - False(周报/月报): 不在此触发, 维持 warm_idle 按 trigger_at 每日定时一次
    自动识别/手动选择在 admin 建模板时落库(见 admin_ai._default_refresh_on_sync)。

    调用点:
      - sync_task_manager._run_background finally(BMS 高频同步 success/partial)
      - complaint_sync 投诉推送入库后(约 20min/次)
    进程级单飞锁: 重叠触发直接跳过; 失败仅 log + 通知, 不影响同步主流程。
    """
    if not _worker_lock.acquire(blocking=False):
        logger.info("ai_report_worker warm_on_sync_complete: 已有 worker 在跑, 跳过")
        return
    db = SessionLocal()
    try:
        rows = db.query(AiReportTemplate).filter(
            AiReportTemplate.is_active.is_(True),
            AiReportTemplate.refresh_on_sync.is_(True),
        ).order_by(AiReportTemplate.priority).all()
        if not rows:
            return
        logger.info("ai_report_worker warm_on_sync_complete: %s 个 refresh_on_sync 模板待评估",
                    len(rows))
        # 先取标量快照: 循环内 generate_for_template 会 commit(expire_on_commit),
        # 之后再访问 ORM 对象属性会 lazy reload, 提前取出避免 detached/expired 隐患。
        snapshot = [(t.id, t.code) for t in rows]
        today = _now_cn().strftime("%Y-%m-%d")
        for tid, tcode in snapshot:
            try:
                # 需要完整模板对象构造 messages; id 稳定, 按快照 id 取并生成
                tmpl = db.query(AiReportTemplate).get(tid)
                if tmpl is None or not tmpl.is_active:
                    continue
                # #236 B2-1: daily 报告改实时生成, 跳过 period=daily 模板。
                # 20min 节流逻辑保留(供未来非 daily 的 refresh_on_sync 模板使用),
                # 但当前所有 refresh_on_sync=True 模板均为 daily → 此路径实质不再预生成。
                if (tmpl.period or "") == "daily":
                    logger.info(
                        "warm_on_sync_complete: 模板 %s period=daily, B2-1 跳过(改实时生成)",
                        tcode)
                    continue
                # 统一 20min 节流: 随同步刷新的模板都按同步节奏(30min/轮),
                # 距上次生成不足阈值则跳过, 避免推送与同步撞车时重复调 LLM。
                # #166: 按快照窗口判(period + 当日), 而非只看当日。
                if _window_generated_recently(
                        db, tid, snapshot_period=tmpl.period, snapshot_date=today,
                        min_minutes=_DAILY_SYNC_MIN_INTERVAL_MIN):
                    logger.info(
                        "warm_on_sync_complete: 模板 %s 在 %smin 内已生成, 节流跳过",
                        tcode, _DAILY_SYNC_MIN_INTERVAL_MIN)
                    # #193/#204: 节流跳过同样留痕(reason=throttled_20min), 供管理端
                    # 「跳过次数」列区分两类跳过(设计文档 §4.5)。info 级不进角标。
                    record_system_event(
                        db, category="ai", action="pregen_skipped",
                        title=f"AI 预生成节流跳过: {tcode}",
                        summary=f"距上次生成不足 {_DAILY_SYNC_MIN_INTERVAL_MIN}min",
                        status="info",
                        detail=json.dumps({
                            "template": tcode, "template_id": tid,
                            "reason": "throttled_20min",
                        }, ensure_ascii=False),
                    )
                    continue
                # ── #193/#204 新鲜度判定(三档: off/dry_run/enforce) ──────────
                # 保底③(手动触发必生成)由构造保证: 本路径恒 level=0, admin 手动
                # 触发走 admin 接口直接 generate_for_template(level=1), 不经本判定。
                decision = evaluate_pregen_skip(
                    db, template_id=tid, template_code=tcode, scope=tmpl.scope,
                    snapshot_period=tmpl.period, snapshot_date=today, trigger_level=0)
                if decision["skip"]:
                    # dry_run 与 enforce 都留痕, 由 detail.enforced 区分
                    record_pregen_skipped_event(
                        db, template_id=tid, template_code=tcode, scope=tmpl.scope,
                        decision=decision, enforced=decision["mode"] == "enforce")
                    if decision["mode"] == "enforce":
                        logger.info(
                            "warm_on_sync_complete: 模板 %s 自上次快照零新增, enforce 跳过",
                            tcode)
                        continue
                    # dry_run(影子期): 留痕后照常生成, 行为与改造前一致
                    logger.info(
                        "warm_on_sync_complete: 模板 %s 影子判定可跳过(dry_run 照常生成)",
                        tcode)
                # #127: 同步触发的后台预生成挂 L0(最高优先, 可抢占 L1/L2)
                generate_for_template(db, tmpl, level=0)
            except Exception as e:  # noqa: BLE001  单个模板失败不影响其他
                logger.error("ai_report_worker: template=%s 生成失败: %s", tcode, e)
    except Exception as e:  # noqa: BLE001  worker 失败不影响业务
        logger.error("ai_report_worker warm_on_sync_complete 异常(不影响业务): %s", e)
    finally:
        db.close()
        _worker_lock.release()

    # 同步后 AI 分类（非阻塞，不影响报告主流程）
    try:
        classify_after_sync()
    except Exception as e:  # noqa: BLE001
        logger.warning("classify_after_sync 异常(不影响业务): %s", e)


def classify_after_sync() -> None:
    """采集后 AI 分类/打标：同步完成后对未分类记录做 AI 辅助分类。

    设计：
    - 同步每 30min 一轮，每轮取 10 条「普通退款」（预设规则未命中的）记录
    - 调 LLM（经 talon provider 链，不出域）判断是否有误分类
    - 发现误分类 → 产 governance finding（detector=semantic_anomaly）待人工确认
    - 30min 节流防过频调用 LLM
    - 失败仅 log，不影响同步主流程

    与 D7（凌晨完整体检）的区别：
    - D7 取 20 条、做完整语义检查（原因矛盾/金额异常/空值）
    - 本函数取 10 条、只检查「普通退款是否应归为客诉」这一个维度
    - D7 落 governance_findings 待研判；本函数同样落 findings
    """
    if not _classify_lock.acquire(blocking=False):
        return
    db = SessionLocal()
    try:
        from app.services.governance_detector import (
            persist_findings,
            scan_semantic_anomaly,
        )
        from app.services.system_event_service import record_system_event

        # 节流：30min 内已跑过则跳过
        last = db.execute(text(
            "SELECT max(created_at) FROM system_events "
            "WHERE category='ai_governance' AND action='classify_after_sync'"
        )).scalar()
        if last:
            now_cn = _dt.datetime.now(_TZ_CN)
            last_cn = last.astimezone(_TZ_CN) if last.tzinfo else last.replace(tzinfo=_TZ_CN)
            if (now_cn - last_cn).total_seconds() < _CLASSIFY_MIN_INTERVAL_MIN * 60:
                return

        # 调 D7 探测器（经 talon provider 链，取 20 条样本）
        findings = scan_semantic_anomaly(db)
        if findings:
            persisted = persist_findings(db, findings)
            record_system_event(
                db, category="ai_governance", action="classify_after_sync",
                title=f"采集后 AI 分类: 发现 {len(persisted)} 条疑似误分类",
                summary=f"检查 {findings[0]['metrics']['scanned']} 条, "
                        f"标记 {findings[0]['metrics']['flagged']} 条",
                status="success",
                detail=json.dumps(
                    {"flagged_ids": findings[0]["metrics"]["flagged_ids"]},
                    ensure_ascii=False),
                commit=True,
            )
            logger.info("classify_after_sync: 标记 %s 条疑似误分类",
                        findings[0]["metrics"]["flagged"])
        else:
            record_system_event(
                db, category="ai_governance", action="classify_after_sync",
                title="采集后 AI 分类: 无异常",
                summary="检查完成, 未发现误分类",
                status="success", detail="{}", commit=True,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("classify_after_sync 异常(不影响业务): %s", e)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()
        _classify_lock.release()


# 采集后 AI 分类单飞锁
_classify_lock = threading.Lock()


def warm_on_startup(force: bool = False) -> None:
    """服务启动后预热: **只补「当前该有的报告」**, 不再无差别重算全部 active 模板。

    #166 修复(用户反馈: 周报/月报每天被生成 7~21 次):
      原实现遍历**全部** active 模板直接生成 → 每次服务重启就把周报/月报重算一遍,
      与 trigger_at(如 1-02:00)完全无关。生产实证: 报告生成时刻与 `upgrade.sh`
      部署重启时刻逐一吻合(当天 5 次部署 → 5 份重复周报/月报)。

    现在的分流规则:
      - `refresh_on_sync=True`(日报类, 时效性强): **保持原行为**, 启动即生成
        (保证重启后当日报告仍新鲜), 但加快照窗口节流防重启风暴;
      - 其余(周报/月报, refresh_on_sync=False): **只在 trigger_at 到点时生成**
        (复用 `_template_due_now`, 与 schedule_due_templates 同口径),
        未到点则跳过 —— 到点的那次由 warm_idle 正常触发, 不依赖重启。
      - #306(2026-10-12): 周报/月报的「到点」判定放宽为触发日当天时刻已达; 且当日
        已有成功公共快照则跳过 —— 发布重启错过 02:00 时, 触发日当天启动/warm_idle
        自动补跑一次, 已成功则永不重算(与 schedule_due_templates 同口径幂等)。

    节流(#166 用户指定「按快照窗口判」): 统一走 `_window_generated_recently`,
    按 `(template_id, snapshot_period, snapshot_date=今日)` 判「同一份报告」,
    在 `_STARTUP_MIN_INTERVAL_MIN` 分钟内已生成则跳过 —— 连续部署/异常重启风暴被挡住,
    而正常一次部署(距上次生成远超 20min)不受影响。

    force=True 时无视白天限制(启动时强制); 否则只在白天 8-23 触发。
    """
    if not force and _now_cn().hour not in _DAYTIME_HOURS:
        return
    if not _worker_lock.acquire(blocking=False):
        logger.info("ai_report_worker warm_on_startup: 已有 worker 在跑, 跳过")
        return
    db = SessionLocal()
    try:
        rows = db.query(AiReportTemplate).filter(
            AiReportTemplate.is_active.is_(True),
        ).order_by(AiReportTemplate.priority).all()
        if not rows:
            return
        now = _now_cn()
        today = now.strftime("%Y-%m-%d")
        logger.info("ai_report_worker warm_on_startup(force=%s): %s 个 active 模板待评估",
                    force, len(rows))
        # 先取标量快照: 循环内 generate_for_template 会 commit(expire_on_commit),
        # 之后再访问 ORM 对象属性会 lazy reload, 提前取出避免 detached/expired 隐患。
        snapshot = [(t.id, t.code, t.refresh_on_sync) for t in rows]
        for tid, tcode, refresh_on_sync in snapshot:
            try:
                # 需要完整模板对象: ① 判 trigger_at 是否到点 ② 取 period 做窗口节流
                tmpl = db.query(AiReportTemplate).get(tid)
                if tmpl is None or not tmpl.is_active:
                    continue
                # #236 B2-1: daily 报告改实时生成, 启动预热也跳过 period=daily 模板
                if (tmpl.period or "") == "daily":
                    continue
                # ① 周报/月报(refresh_on_sync=False): 只有到点才生成
                #    未到点 → 启动不生成(#166 核心修复), 等 warm_idle 按 trigger_at 触发
                if not refresh_on_sync and not _template_due_now(tmpl, now):
                    continue
                # ①.5 #306 漏跑当补幂等去重: 触发日当天该期报告已成功落库(02:00 正常
                #     跑过或 warm_idle 已补过) → 重启不再重算; 无快照则本次启动即补跑。
                if _has_public_snapshot_today(
                        db, tid, snapshot_period=tmpl.period, snapshot_date=today):
                    logger.info(
                        "warm_on_startup: 模板 %s 触发日当日快照已存在, 跳过(幂等去重)",
                        tcode)
                    continue
                # ② 节流: 同一份报告(模板 + 快照窗口)刚生成过则跳过(防重启风暴)
                if _window_generated_recently(
                        db, tid, snapshot_period=tmpl.period, snapshot_date=today,
                        min_minutes=_STARTUP_MIN_INTERVAL_MIN):
                    logger.info(
                        "warm_on_startup: 模板 %s 快照窗口 %s/%s 在 %smin 内已生成, 节流跳过",
                        tcode, tmpl.period, today, _STARTUP_MIN_INTERVAL_MIN)
                    continue
                # #127: 启动预热(active 模板)挂 L0(最高优先)
                generate_for_template(db, tmpl, level=0)
            except Exception as e:  # noqa: BLE001
                logger.error("ai_report_worker: template=%s 生成失败: %s", tcode, e)
    except Exception as e:  # noqa: BLE001
        logger.error("ai_report_worker warm_on_startup 异常(不影响业务): %s", e)
    finally:
        db.close()
        _worker_lock.release()


def warm_idle() -> None:
    """空闲期每分钟扫描: 触发当前时刻匹配的 daily/weekly/monthly 模板。

    由 bms_scheduler._run_loop 每分钟调用(待 #123 接入)。

    #133: 移除白天 8-23 时段限制——周报/月报 trigger_at 调到凌晨(如 1-02:00)
    后, 必须让 warm_idle 在凌晨也能扫描到才能触发。daily 模板虽不调凌晨, 但其
    trigger_at=09:00 仍走此路径, 时段放开不影响它(只在 9 点匹配)。
    白天限制仅保留在 warm_on_startup(避免服务凌晨重启时大量并发预生成)。
    """
    if not _worker_lock.acquire(blocking=False):
        logger.info("ai_report_worker warm_idle: 已有 worker 在跑, 跳过")
        return
    db = SessionLocal()
    try:
        due = schedule_due_templates(db)
        if not due:
            return
        logger.info("ai_report_worker warm_idle: %s 个模板到期", len(due))
        for t in due:
            try:
                # #127: 空闲期触发的 daily/weekly/monthly 也属后台预生成, 挂 L0
                generate_for_template(db, t, level=0)
            except Exception as e:  # noqa: BLE001
                logger.error("ai_report_worker: template=%s 生成失败: %s", t.code, e)
    except Exception as e:  # noqa: BLE001
        logger.error("ai_report_worker warm_idle 异常(不影响业务): %s", e)
    finally:
        db.close()
        _worker_lock.release()

"""AI 报告预生成 worker (工单 #121)

设计:
- 复用 cache_warmer 三时机设计, 但 LLM 调用与缓存路由独立
- 三时机:
  ① 同步完成后(sync_task_manager._run_background finally) 触发 sync-triggered 模板
  ② 服务启动后延迟 8s(start_warm_loop) 触发 active 模板
  ③ 空闲期(bms_scheduler._run_loop 每分钟) 扫描 daily/weekly/monthly + trigger_at 匹配
- 进程级单飞锁 _worker_lock 防并发(单 worker, 保证互斥锁有效)
- 失败处理: 仅 log + 通知中心写 system_event(category=ai, status=failed), 不阻塞
- 参数分级: 后台预生成 timeout_s=180, max_tokens=template.max_tokens(2000)
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

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.ai_report_template import AiReportTemplate
from app.services import llm_client
from app.services.ai_feed import build_messages
from app.services.ai_report_service import save_report
from app.services.system_event_service import record_system_event

logger = logging.getLogger(__name__)

# 时区(AGENTS.md 铁律: 时区换算走 Asia/Shanghai)
_TZ_CN = _dt.timezone(_dt.timedelta(hours=8))

# 白天 8-23 才预热(与 cache_warmer.DAYTIME_HOURS 同步, 夜间无人访问省计算)
_DAYTIME_HOURS = set(range(8, 24))

# 进程级单飞锁(防并发: 同步完成 + 启动 + 空闲期可能同时触发)
_worker_lock = threading.Lock()


def _now_cn() -> _dt.datetime:
    """当前北京时间(AGENTS.md 铁律: 时区换算)。"""
    return _dt.datetime.now(_TZ_CN)


def schedule_due_templates(db: Session) -> list[AiReportTemplate]:
    """扫描 ai_report_templates, 返回当前时刻应当触发的模板列表。

    判定逻辑(#126 P0-3 修正 trigger_at 格式):
    - frequency=sync-triggered: 不在此扫描(由 warm_on_sync_complete 主动触发)
    - frequency=daily + trigger_at=HH:MM: 当前北京时间 HH:MM 在 ±1 分钟内
    - frequency=weekly + trigger_at=wday-HH:MM: 当前北京时间 wday-HH:MM 在 ±1 分钟内
    - frequency=monthly + trigger_at=day-HH:MM: 当前北京时间 day-HH:MM 在 ±1 分钟内

    格式说明(#126 P0-3):
    - 新格式(推荐): 用 `-` 分隔前缀与 HH:MM, 如 "1-09:00" (周一9点 / 每月1号9点)
    - 旧格式(兼容): 用 `:` 三段, 如 "1:09:00", 解析为 wday/day + HH:MM
    - daily 永远是 "HH:MM" (不含 `-`)

    返回按 priority 升序的模板列表(小先执行)。
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
        try:
            if t.frequency == "daily":
                # trigger_at = "HH:MM" (不含 `-`, 与 weekly/monthly 区分)
                if _hm_match(t.trigger_at, now_hm):
                    due.append(t)
            elif t.frequency == "weekly":
                # 新格式: "wday-HH:MM" (如 "1-09:00" = 周一9点)
                # 旧格式: "wday:HH:MM" (如 "1:09:00", #124 遗留, 兼容)
                wday, hm = _parse_wday_hhmm(t.trigger_at)
                if wday is not None and wday == now_wday and _hm_match(hm, now_hm):
                    due.append(t)
            elif t.frequency == "monthly":
                # 新格式: "day-HH:MM" (如 "1-09:00" = 每月1号9点)
                # 旧格式: "day:HH:MM" (如 "1:09:00", #124 遗留, 兼容)
                day, hm = _parse_wday_hhmm(t.trigger_at)
                if day is not None and day == now_day and _hm_match(hm, now_hm):
                    due.append(t)
        except (ValueError, IndexError):
            logger.warning("ai_report_template(id=%s code=%s) trigger_at=%s 解析失败, 跳过",
                           t.id, t.code, t.trigger_at)
            continue

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
    """HH:MM 匹配(±1 分钟容差)。

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


async def _pregen_llm(messages: list[dict], db: Session, max_tokens: int, *,
                      source: str, level: int) -> str:
    """#127 后台/手动预生成经优先门闸发起 LLM 调用。

    level 遵循 #127 工单优先级定义: 0=后台定时预生成(最高), 1=admin 手动触发。
    在**调用方线程自己的事件循环**上执行(保持 db 不跨线程); 被更高优先级抢占时抛
    CancelledError, 由 generate_for_template 捕获置"可重试跳过"。
    """
    from app.services.llm_scheduler import acquire, release
    handle = await acquire(level, name=source)
    try:
        result = await llm_client.chat_completion(
            messages,
            max_tokens=max_tokens,
            timeout_s=180.0,  # 后台预生成允许长任务
            db=db,
            source=source,
        )
        return str(result)
    finally:
        await release(handle)


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

    # 构造 messages(三层 Prompt: System/口径卡/Data+Question)
    try:
        messages = build_messages(
            db, period=period, date=None, question=question,
            start=None, end=None,
            window_label=f"预生成(template={template.code}, snapshot={snapshot_date})",
            window_source="template",
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
        )
        return None

    # 调用 LLM(后台预生成: timeout_s=180, max_tokens=template.max_tokens)
    # #127 经优先门闸发起(level=0/1); 被更高优先级抢占时捕获 CancelledError, 本次跳过。
    llm_model_str = template.llm_provider_code or "auto"
    source = f"ai_pregen:{template.code}"
    try:
        llm_result = _pregen_llm(messages, db, template.max_tokens,
                                 source=source, level=level)
        # asyncio.run 包装(chat_completion 是 async, worker 同步入口)。
        # 单个模板单次调用, 用 asyncio.run 串行跑协程(worker 在非 asyncio 上下文调用)。
        llm_result = asyncio.run(llm_result)
    except asyncio.CancelledError:
        # #127 被抢占: 后台/手动预生成不写失败事件, 仅 log(属正常抢占, 可重试跳过)
        logger.info("ai_report_worker 预生成被更高优先级抢占, 本次跳过(template=%s level=%s)",
                    template.code, level)
        return None
    except RuntimeError as e:
        # 已在事件循环中调用(如 bms_scheduler 直接 async 上下文): 换新 loop 跑
        logger.warning("ai_report_worker: already in event loop, create new loop: %s", e)
        try:
            llm_result = asyncio.new_event_loop().run_until_complete(
                _pregen_llm(messages, db, template.max_tokens, source=source, level=level)
            )
        except asyncio.CancelledError:
            logger.info("ai_report_worker 预生成被抢占(新 loop), 本次跳过(template=%s)", template.code)
            return None
    except Exception as e:  # noqa: BLE001  LLM 调用失败不阻塞 worker
        logger.error("ai_report_worker LLM call failed (template=%s): %s", template.code, e)
        record_system_event(
            db, category="ai", action="pregen_llm_failed",
            title=f"AI 报告预生成 LLM 失败: {template.name_cn}",
            summary=f"template={template.code} err={e!s}",
            status="failed", level="error",
            detail=json.dumps({"template_id": template.id, "code": template.code, "error": str(e)},
                              ensure_ascii=False),
        )
        return None

    total_ms = int((_dt.datetime.now() - t0).total_seconds() * 1000)
    usage = llm_result.usage if hasattr(llm_result, "usage") else None
    answer_md = str(llm_result)

    # 落库(is_public=True, template_id, snapshot_date)
    # user_id=1 表示系统生成的"管理员"报告(admin user); is_public=True 让所有人可见
    report = save_report(
        db,
        task_id=None,  # 预生成无 ai_task_manager 任务 id
        user_id=1,  # 系统生成, 归属 admin user_id=1; 通过 is_public=True 让所有人可见
        question=question,
        period=period,
        date=snapshot_date,
        start=None, end=None,
        window_source="template",
        window_label=f"预生成({template.code})",
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


def warm_on_sync_complete() -> None:
    """同步完成触发入口: 扫描 sync-triggered 模板, 依次生成。

    由 sync_task_manager._run_background finally 调用(待 #123 接入)。
    失败仅 log + 通知中心, 不抛异常(不影响同步流程)。
    """
    if not _worker_lock.acquire(blocking=False):
        logger.info("ai_report_worker warm_on_sync_complete: 已有 worker 在跑, 跳过")
        return
    db = SessionLocal()
    try:
        rows = db.query(AiReportTemplate).filter(
            AiReportTemplate.is_active.is_(True),
            AiReportTemplate.frequency == "sync-triggered",
        ).order_by(AiReportTemplate.priority).all()
        if not rows:
            return
        logger.info("ai_report_worker warm_on_sync_complete: %s 个 sync-triggered 模板待生成", len(rows))
        for t in rows:
            try:
                # #127: 后台定时预生成(sync-triggered)挂 L0(最高优先, 可抢占 L1/L2)
                generate_for_template(db, t, level=0)
            except Exception as e:  # noqa: BLE001  单个模板失败不影响其他
                logger.error("ai_report_worker: template=%s 生成失败: %s", t.code, e)
    except Exception as e:  # noqa: BLE001  worker 失败不影响业务
        logger.error("ai_report_worker warm_on_sync_complete 异常(不影响业务): %s", e)
    finally:
        db.close()
        _worker_lock.release()


def warm_on_startup(force: bool = False) -> None:
    """服务启动后预热: 触发 active 模板(无 trigger_at 匹配, 全部 active)。

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
        logger.info("ai_report_worker warm_on_startup(force=%s): %s 个 active 模板待生成",
                    force, len(rows))
        for t in rows:
            try:
                # #127: 启动预热(active 模板)挂 L0(最高优先)
                generate_for_template(db, t, level=0)
            except Exception as e:  # noqa: BLE001
                logger.error("ai_report_worker: template=%s 生成失败: %s", t.code, e)
    except Exception as e:  # noqa: BLE001
        logger.error("ai_report_worker warm_on_startup 异常(不影响业务): %s", e)
    finally:
        db.close()
        _worker_lock.release()


def warm_idle() -> None:
    """空闲期每分钟扫描: 触发当前时刻匹配的 daily/weekly/monthly 模板。

    由 bms_scheduler._run_loop 每分钟调用(待 #123 接入)。
    白天 8-23 才扫描, 夜间无人访问省计算。
    """
    if _now_cn().hour not in _DAYTIME_HOURS:
        return
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

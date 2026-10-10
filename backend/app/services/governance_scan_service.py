"""AI 数据治理 · 体检编排服务 (工单 #163, 设计 §四/§九；重扫收敛 + 自动研判 = 工单 #167)

把 D1~D5 探测器串成一次「数据体检」：
  1. 批量跑探测器（各自产 findings，不落库）
  2. 统一落 `governance_findings`（幂等去重：同 detector+表+目标 未处置时刷新证据）
  3. **重扫收敛**（#167）：本次未再命中的 open/judged finding → `auto_closed`
     （只限本次跑通的探测器，避免探测失败误关历史 finding）
  4. 记 `system_events(category=ai_governance, action=governance_scan)` 留痕
     （探测是 L0 只读动作，无副作用，自动执行；detail 含 auto_closed 明细）

调度（设计 §九）：
  - 定时：每日低峰自动一次（避开同步高峰）→ 挂 cache_warmer 空闲循环，见 `governance_idle`
    · 体检后自动**研判**一次（`judge_idle`，#167）：规则命中时零 LLM 成本，
      解决「体检持续报新 open finding 而无人研判」的闭环缺口
  - 手动：管理页按钮触发（工单 #165 提供接口，本工单暴露 `run_full_scan` 供其调用）
  - 同步后触发：**不做**（每次同步都扫会浪费，且数据问题不要求分钟级发现）

留痕规范（设计 §3.3）：
  category=ai_governance, action=governance_scan, summary 含各探测器扫到的 finding 数。

单飞锁：进程级 `_scan_lock` 防「定时 + 手动」重叠（单 worker，保证互斥锁有效）。
"""

import asyncio
import datetime as _dt
import json
import logging
import threading

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.governance_finding import (
    DETECTOR_FILLABLE_GAP,
    DETECTOR_FK_ORPHAN,
    DETECTOR_IMPOSSIBLE_DATE,
    DETECTOR_ROW_COUNT_JUMP,
    DETECTOR_SEMANTIC_ANOMALY,
    DETECTOR_TEXT_ANOMALY,
    DETECTOR_UNIQUE_INDEX_DUP,
)
from app.services.governance_detector import (
    close_stale_findings,
    persist_findings,
    scan_fillable_gaps,
    scan_fk_orphans,
    scan_impossible_dates,
    scan_row_count_jump,
    scan_semantic_anomaly,
    scan_text_anomaly,
    scan_unique_index_dups,
)
from app.services.governance_judge_service import EVENT_JUDGE, judge_findings
from app.services.system_event_service import record_system_event, update_system_event

logger = logging.getLogger(__name__)

# 进程级单飞锁（防并发体检）
_scan_lock = threading.Lock()

# system_events 留痕分类/动作（设计 §3.3）
EVENT_CATEGORY = "ai_governance"
EVENT_SCAN = "governance_scan"
EVENT_SCAN_LIGHT = "governance_scan_light"  # 午间轻量体检（D1+D2 only）


def _now_cn() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8)))


def gather_findings(db: Session) -> tuple[list[dict], set[str]]:
    """批量跑 D1~D6，返回 (全部 findings, 本次**跑通**的探测器集合)。

    各探测器内部已做单表容错（失败跳过该表、不阻塞）。编排层再包一层：
    整体失败不阻塞留痕（能找到多少算多少）。

    ⚠️ 第二个返回值是**重扫收敛的前提**（#167）：只有跑通的探测器才允许收敛其历史
    finding —— 否则一次探测失败会把该探测器下的全部历史 finding 误判成「不再命中」。
    """
    results: list[dict] = []
    ok: set[str] = set()
    for detector, fn in (
        (DETECTOR_UNIQUE_INDEX_DUP, scan_unique_index_dups),
        (DETECTOR_ROW_COUNT_JUMP, scan_row_count_jump),
        (DETECTOR_FK_ORPHAN, scan_fk_orphans),
        (DETECTOR_TEXT_ANOMALY, scan_text_anomaly),
        (DETECTOR_IMPOSSIBLE_DATE, scan_impossible_dates),
        (DETECTOR_FILLABLE_GAP, scan_fillable_gaps),
        (DETECTOR_SEMANTIC_ANOMALY, scan_semantic_anomaly),
    ):
        try:
            results += fn(db)
            ok.add(detector)
        except Exception as e:  # noqa: BLE001  单个探测器失败不影响其它（也不参与收敛）
            logger.error("governance: %s 探测失败(不影响其它, 本探测器不参与收敛): %s", detector, e)
    return results, ok


def _scan_summary(findings: list[dict]) -> dict:
    """按 detector 汇总 finding 数，构成 system_events summary。"""
    by = {}
    for f in findings:
        by[f["detector"]] = by.get(f["detector"], 0) + 1
    text_ = ", ".join(f"{k}={v}" for k, v in sorted(by.items())) or "无"
    return {"detectors": by, "finding_count": len(findings), "detail": text_}


def run_full_scan(db: Session | None = None, *, commit: bool = True,
                  event_id: int | None = None) -> dict:
    """完整体检：探测 → 落 findings → **重扫收敛** → system_events 留痕。

    返回 {category, action, status, found, persisted, auto_closed, by_detector, findings}。
    失败返回 status='partial'（已探测到的尽力落库 + 留痕），不抛异常。

    `event_id` 非空（手动触发路径，工单 #165）：**回填**该占位事件（running → 终态），
    不再新建一行 —— 202 + 轮询模式下一行事件贯穿全程，避免重复留痕（同 #166 教训）。
    """
    status = "success"
    own_db = db is None
    session = db if not own_db else SessionLocal()
    try:
        findings, ok_detectors = gather_findings(session)
        persisted = persist_findings(session, findings)
        # 重扫收敛（#167）：本次未再命中的 open/judged → auto_closed（只限跑通的探测器）
        seen = {(f["detector"], f["table_name"], f["target"]) for f in findings}
        closed = close_stale_findings(session, detectors=ok_detectors, seen_keys=seen)
        summary = _scan_summary(findings)
        detail = _json_detail({**summary, "auto_closed": {
            "count": len(closed), "ids": [f.id for f in closed],
        }})
        title = f"数据体检: 发现 {len(findings)} 个结构性可疑, 收敛 {len(closed)} 个"
        if event_id is not None:
            update_system_event(session, event_id, status=status, title=title,
                                summary=summary["detail"], detail=detail, commit=False)
        else:
            record_system_event(
                session, category=EVENT_CATEGORY, action=EVENT_SCAN,
                title=title, summary=summary["detail"], status=status,
                detail=detail, commit=False,
            )
        if commit:
            session.commit()
        return {
            "category": EVENT_CATEGORY,
            "action": EVENT_SCAN,
            "status": status,
            "found": len(findings),
            "persisted": len(persisted),
            "auto_closed": [f.id for f in closed],
            "by_detector": summary["detectors"],
            "findings": findings,
        }
    except Exception as e:  # noqa: BLE001  体检失败不影响业务主流程
        logger.error("governance: 体检失败(不影响业务): %s", e)
        try:
            session.rollback()
            if event_id is not None:
                update_system_event(session, event_id, status="failed",
                                    summary=f"体检失败: {e}"[:512],
                                    detail=json.dumps({"error": str(e)}, ensure_ascii=False),
                                    commit=True)
        except Exception:  # noqa: BLE001  失败留痕本身失败不再抛
            pass
        return {"category": EVENT_CATEGORY, "action": EVENT_SCAN,
                "status": "failed", "found": 0, "persisted": 0, "auto_closed": [],
                "by_detector": {}, "findings": [], "error": str(e)}
    finally:
        if own_db:
            session.close()


def start_scan_task(event_id: int) -> None:
    """后台跑一次完整体检并回填占位事件（202 + 轮询，工单 #165）。

    为什么用 to_thread 而非直接在事件循环里跑：体检要扫全库（D1 全量索引 + D2 逐表
    按日 count），是**秒级到分钟级**的同步 DB 活；直接在 loop 里跑会卡住整个 API。
    session 在 worker 线程内创建，不跨线程使用。
    """
    def _worker() -> None:
        db = SessionLocal()
        try:
            run_full_scan(db, commit=True, event_id=event_id)
        except Exception as e:  # noqa: BLE001  后台任务失败只留痕
            logger.error("governance 后台体检异常: %s", e)
        finally:
            db.close()

    async def _run() -> None:
        await asyncio.to_thread(_worker)

    asyncio.get_running_loop().create_task(_run())


def _json_detail(summary: dict) -> str:
    return json.dumps(summary, ensure_ascii=False, default=str)


def _event_done_today(db: Session, action: str,
                      statuses: tuple[str, ...] = ("success", "partial")) -> bool:
    """当日语义：该 action 的 ai_governance 事件今天（北京时区）是否已**成功**写过。

    为什么限定 statuses：手动触发的占位事件（status=running）不该让当日自动体检被跳过
    —— 否则一次手动触发中途失败，当天的自动体检就永远不跑了。

    ⚠️ 时区铁律：created_at 是 timestamptz，必须 AT TIME ZONE 换算到北京时区再取日期，
       否则容器 UTC 时区下 08:00 前会把「昨天」当成今天（低峰 0-5 点正好落在这个区间）。
    """
    return bool(db.execute(text(
        "SELECT count(*) FROM system_events "
        "WHERE category = :c AND action = :a AND status = ANY(:st) "
        "AND (created_at AT TIME ZONE 'Asia/Shanghai')::date "
        "    = (now() AT TIME ZONE 'Asia/Shanghai')::date"
    ), {"c": EVENT_CATEGORY, "a": action, "st": list(statuses)}).scalar())


def judge_idle(db: Session) -> dict:
    """每日低峰自动研判一次（工单 #167，设计 §九）。

    为什么需要（#164 收尾发现）：体检会持续把同一复发问题报成新的 `open` finding，
    而研判原本只能人工触发 → `open` 只增不减。规则命中时研判是**零 LLM 成本**的
    （`_judge_core` 先跑规则短路），所以低峰自动跑一次是安全的兜底。

    当日语义与体检一致（每分钟被 loop 调到也只跑一次）；无待研判项时不写事件。
    """
    if _event_done_today(db, EVENT_JUDGE):
        return {"skipped": "done_today"}
    return judge_findings(db, commit=True)


def run_light_scan(db: Session | None = None, *, commit: bool = True,
                   event_id: int | None = None) -> dict:
    """午间轻量体检：只跑 D1(唯一索引重复) + D2(行数突变)。

    为什么只跑这两个：D1/D2 是秒级快扫，且是最高频复发的结构性问题；
    D3~D6 耗时更长或更偏静态，留给凌晨完整体检即可。

    留痕 action=governance_scan_light，与完整体检分开计数。
    """
    own_db = db is None
    session = db if not own_db else SessionLocal()
    try:
        findings: list[dict] = []
        ok: set[str] = set()
        for detector, fn in (
            (DETECTOR_UNIQUE_INDEX_DUP, scan_unique_index_dups),
            (DETECTOR_ROW_COUNT_JUMP, scan_row_count_jump),
        ):
            try:
                findings += fn(session)
                ok.add(detector)
            except Exception as e:  # noqa: BLE001
                logger.error("governance light: %s 探测失败: %s", detector, e)

        persisted = persist_findings(session, findings)
        seen_keys = {(f.detector, f.table_name, f.target) for f in persisted}
        closed = close_stale_findings(session, detectors=ok, seen_keys=seen_keys)
        summary = _scan_summary(findings)
        result = {
            "category": EVENT_CATEGORY,
            "action": EVENT_SCAN_LIGHT,
            "status": "success",
            "found": len(findings),
            "persisted": len(persisted),
            "auto_closed": [f.id for f in closed],
            "by_detector": summary["detectors"],
            "findings": findings,
        }
        record_system_event(
            session, category=EVENT_CATEGORY, action=EVENT_SCAN_LIGHT,
            title=f"午间轻量体检: {summary['detail']}",
            summary=f"发现 {len(findings)} 项, 落库 {len(persisted)} 项, 收敛 {len(closed)} 项",
            status="success",
            detail=_json_detail(result), commit=commit,
        )
        return result
    except Exception as e:  # noqa: BLE001
        logger.error("governance light: 体检失败(不影响业务): %s", e)
        try:
            session.rollback()
            if event_id is not None:
                update_system_event(session, event_id, status="failed",
                                    summary=f"轻量体检失败: {e}"[:512],
                                    detail=json.dumps({"error": str(e)}, ensure_ascii=False),
                                    commit=True)
        except Exception:  # noqa: BLE001
            pass
        return {"category": EVENT_CATEGORY, "action": EVENT_SCAN_LIGHT,
                "status": "failed", "found": 0, "persisted": 0, "auto_closed": [],
                "by_detector": {}, "findings": [], "error": str(e)}
    finally:
        if own_db:
            session.close()


def governance_idle() -> None:
    """每日自动体检 + 自动研判入口（挂 cache_warmer 空闲循环）。

    两个执行窗口：
    1. 凌晨 0-5 点：完整体检（D1~D6）+ 自动研判
       —— 避开同步高峰，省计算、防干扰用户
    2. 午间 12-13 点：轻量体检（D1+D2 only）
       —— 同步窗口间隙快速扫高频复发问题，秒级完成不干扰业务

    两个窗口都用「当日语义」防重复：system_events 当日已有对应 action 则跳过。
    失败不阻塞主循环。
    """
    hour = _now_cn().hour

    # ── 窗口 1: 凌晨完整体检 ──
    if hour in range(0, 6):
        if not _scan_lock.acquire(blocking=False):
            logger.info("governance_idle: 已有体检在跑, 跳过")
            return
        db = SessionLocal()
        try:
            if not _event_done_today(db, EVENT_SCAN):
                run_full_scan(db, commit=True)
                db.expire_all()
            # 体检后自动研判（规则命中零成本；被抢占不阻塞主循环）
            try:
                judge_idle(db)
            except asyncio.CancelledError:
                logger.info("governance_idle: 自动研判被抢占, 下次低峰重试")
        except Exception as e:  # noqa: BLE001
            logger.warning("governance_idle 异常(不影响业务): %s", e)
        finally:
            db.close()
            _scan_lock.release()
        return

    # ── 窗口 2: 午间轻量体检 ──
    if hour == 12:
        if not _scan_lock.acquire(blocking=False):
            logger.info("governance_idle: 已有体检在跑, 跳过午间轻量")
            return
        db = SessionLocal()
        try:
            if not _event_done_today(db, EVENT_SCAN_LIGHT):
                run_light_scan(db, commit=True)
                logger.info("governance_idle: 午间轻量体检完成")
        except Exception as e:  # noqa: BLE001
            logger.warning("governance_idle 午间轻量异常(不影响业务): %s", e)
        finally:
            db.close()
            _scan_lock.release()

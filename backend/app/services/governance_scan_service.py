"""AI 数据治理 · 体检编排服务 (工单 #163, 设计 §四/§九；重扫收敛 + 自动研判 = 工单 #167)

把 D1/D2/D3 探测器串成一次「数据体检」：
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
    DETECTOR_FK_ORPHAN,
    DETECTOR_ROW_COUNT_JUMP,
    DETECTOR_UNIQUE_INDEX_DUP,
)
from app.services.governance_detector import (
    close_stale_findings,
    persist_findings,
    scan_fk_orphans,
    scan_row_count_jump,
    scan_unique_index_dups,
)
from app.services.governance_judge_service import EVENT_JUDGE, judge_findings
from app.services.system_event_service import record_system_event

logger = logging.getLogger(__name__)

# 进程级单飞锁（防并发体检）
_scan_lock = threading.Lock()

# system_events 留痕分类/动作（设计 §3.3）
EVENT_CATEGORY = "ai_governance"
EVENT_SCAN = "governance_scan"


def _now_cn() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8)))


def gather_findings(db: Session) -> tuple[list[dict], set[str]]:
    """批量跑 D1/D2/D3，返回 (全部 findings, 本次**跑通**的探测器集合)。

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


def run_full_scan(db: Session | None = None, *, commit: bool = True) -> dict:
    """完整体检：探测 → 落 findings → **重扫收敛** → system_events 留痕。

    返回 {category, action, status, found, persisted, auto_closed, by_detector, findings}。
    失败返回 status='partial'（已探测到的尽力落库 + 留痕），不抛异常。
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
        record_system_event(
            session,
            category=EVENT_CATEGORY,
            action=EVENT_SCAN,
            title=f"数据体检: 发现 {len(findings)} 个结构性可疑, 收敛 {len(closed)} 个",
            summary=summary["detail"],
            status=status,
            detail=_json_detail({**summary, "auto_closed": {
                "count": len(closed), "ids": [f.id for f in closed],
            }}),
            commit=False,
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
        except Exception:  # noqa: BLE001
            pass
        return {"category": EVENT_CATEGORY, "action": EVENT_SCAN,
                "status": "failed", "found": 0, "persisted": 0, "auto_closed": [],
                "by_detector": {}, "findings": [], "error": str(e)}
    finally:
        if own_db:
            session.close()


def _json_detail(summary: dict) -> str:
    return json.dumps(summary, ensure_ascii=False, default=str)


def _event_done_today(db: Session, action: str) -> bool:
    """当日语义：该 action 的 ai_governance 事件今天（北京时区）是否已写过。

    ⚠️ 时区铁律：created_at 是 timestamptz，必须 AT TIME ZONE 换算到北京时区再取日期，
       否则容器 UTC 时区下 08:00 前会把「昨天」当成今天（低峰 0-5 点正好落在这个区间）。
    """
    return bool(db.execute(text(
        "SELECT count(*) FROM system_events "
        "WHERE category = :c AND action = :a "
        "AND (created_at AT TIME ZONE 'Asia/Shanghai')::date "
        "    = (now() AT TIME ZONE 'Asia/Shanghai')::date"
    ), {"c": EVENT_CATEGORY, "a": action}).scalar())


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


def governance_idle() -> None:
    """每日低峰自动体检 + 自动研判入口（挂 cache_warmer 空闲循环）。

    设计与 ai_feedback.check_daily_health_report_idle 同款「当日语义」：
    以 system_events(category=ai_governance) 当日是否已有对应 action 为准，
    当日已跑则跳过 → 定时 loop 每分钟调本函数也只会触发一次。
    低峰时段（凌晨 0-5 点）执行，避开同步高峰；白日不自动跑（省计算、防干扰用户）。
    失败不阻塞主循环。
    """
    # 只在低峰(0-5点)自动跑
    if _now_cn().hour not in range(0, 6):
        return
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
            # llm_scheduler 抢占（更高优先级任务上位）—— 属正常，下次低峰再跑
            logger.info("governance_idle: 自动研判被抢占, 下次低峰重试")
    except Exception as e:  # noqa: BLE001  体检/研判失败不影响主循环
        logger.warning("governance_idle 异常(不影响业务): %s", e)
    finally:
        db.close()
        _scan_lock.release()

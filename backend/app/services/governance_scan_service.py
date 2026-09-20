"""AI 数据治理 · 体检编排服务 (工单 #163, 设计 §四/§九)

把 D1/D2/D3 探测器串成一次「数据体检」：
  1. 批量跑探测器（各自产 findings，不落库）
  2. 统一落 `governance_findings`（幂等去重：同 detector+表+目标 未处置时刷新证据）
  3. 记 `system_events(category=ai_governance, action=governance_scan)` 留痕
     （探测是 L0 只读动作，无副作用，自动执行）

调度（设计 §九）：
  - 定时：每日低峰自动一次（避开同步高峰）→ 挂 cache_warmer 空闲循环，见 `governance_idle`
  - 手动：管理页按钮触发（工单 #165 提供接口，本工单暴露 `run_full_scan` 供其调用）
  - 同步后触发：**不做**（每次同步都扫会浪费，且数据问题不要求分钟级发现）

留痕规范（设计 §3.3）：
  category=ai_governance, action=governance_scan, summary 含各探测器扫到的 finding 数。

单飞锁：进程级 `_scan_lock` 防「定时 + 手动」重叠（单 worker，保证互斥锁有效）。
"""

import datetime as _dt
import json
import logging
import threading

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.services.governance_detector import (
    persist_findings,
    scan_fk_orphans,
    scan_row_count_jump,
    scan_unique_index_dups,
)
from app.services.system_event_service import record_system_event

logger = logging.getLogger(__name__)

# 进程级单飞锁（防并发体检）
_scan_lock = threading.Lock()

# system_events 留痕分类/动作（设计 §3.3）
EVENT_CATEGORY = "ai_governance"
EVENT_SCAN = "governance_scan"


def _now_cn() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8)))


def gather_findings(db: Session) -> list[dict]:
    """批量跑 D1/D2/D3，返回全部 findings（聚合，不落库）。

    各探测器内部已做单表容错（失败跳过该表、不阻塞）。编排层再包一层：
    整体失败不阻塞留痕（能找到多少算多少）。
    """
    results: list[dict] = []
    try:
        results += scan_unique_index_dups(db)
    except Exception as e:  # noqa: BLE001  单个探测器失败不影响其它
        logger.error("governance: D1 探测失败(不影响其它): %s", e)
    try:
        results += scan_row_count_jump(db)
    except Exception as e:  # noqa: BLE001
        logger.error("governance: D2 探测失败(不影响其它): %s", e)
    try:
        results += scan_fk_orphans(db)
    except Exception as e:  # noqa: BLE001
        logger.error("governance: D3 探测失败(不影响其它): %s", e)
    return results


def _scan_summary(findings: list[dict]) -> dict:
    """按 detector 汇总 finding 数，构成 system_events summary。"""
    by = {}
    for f in findings:
        by[f["detector"]] = by.get(f["detector"], 0) + 1
    text_ = ", ".join(f"{k}={v}" for k, v in sorted(by.items())) or "无"
    return {"detectors": by, "finding_count": len(findings), "detail": text_}


def run_full_scan(db: Session | None = None, *, commit: bool = True) -> dict:
    """完整体检：探测 → 落 findings → system_events 留痕。供调度/手动接口调用。

    返回 {category, action, status, found, persisted, by_detector, findings}。
    失败返回 status='partial'（已探测到的尽力落库 + 留痕），不抛异常。
    """
    status = "success"
    own_db = db is None
    session = db if not own_db else SessionLocal()
    try:
        findings = gather_findings(session)
        persisted = persist_findings(session, findings)
        summary = _scan_summary(findings)
        record_system_event(
            session,
            category=EVENT_CATEGORY,
            action=EVENT_SCAN,
            title=f"数据体检: 发现 {len(findings)} 个结构性可疑",
            summary=summary["detail"],
            status=status,
            detail=_json_detail(summary),
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
                "status": "failed", "found": 0, "persisted": 0,
                "by_detector": {}, "findings": [], "error": str(e)}
    finally:
        if own_db:
            session.close()


def _json_detail(summary: dict) -> str:
    return json.dumps(summary, ensure_ascii=False, default=str)


def governance_idle() -> None:
    """每日低峰自动体检入口（挂 cache_warmer 空闲循环）。

    设计与 ai_feedback.check_daily_health_report_idle 同款「当日语义」：
    以 system_events(category=ai_governance, action=governance_scan) 当日是否存在为准，
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
        # 当日是否已体检（当日语义：loop 每分钟调也只触发一次）
        # ⚠️ 时区铁律：created_at 是 timestamptz，必须 AT TIME ZONE 换算到北京时区再取日期，
        #    否则容器 UTC 时区下 08:00 前会把「昨天」当成今天（凌晨体检正好落在这个区间）
        done = db.execute(text(
            "SELECT count(*) FROM system_events "
            "WHERE category = :c AND action = :a "
            "AND (created_at AT TIME ZONE 'Asia/Shanghai')::date "
            "    = (now() AT TIME ZONE 'Asia/Shanghai')::date"
        ), {"c": EVENT_CATEGORY, "a": EVENT_SCAN}).scalar()
        if done:
            return
        run_full_scan(db, commit=True)
        db.expire_all()
    except Exception as e:  # noqa: BLE001  体检失败不影响主循环
        logger.warning("governance_idle 异常(不影响业务): %s", e)
    finally:
        db.close()
        _scan_lock.release()

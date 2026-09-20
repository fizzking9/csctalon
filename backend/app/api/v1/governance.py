"""AI 数据治理路由（工单 #165，设计 §八 接口契约）

接口清单（设计 §八 9 个 + 1 个补充）：
  POST   /governance/scans                 手动触发体检（202 + scan_id，异步）
  GET    /governance/scans/{id}            轮询体检状态 + 本批概要
  POST   /governance/judge                 手动触发研判（202 + judge_id，异步）★补充
  GET    /governance/findings              发现列表（detector/table/status/verdict 筛选 + 分页）
  GET    /governance/findings/{id}         单条详情（证据 + 研判 + 建议 + 复现 SQL + 执行凭证）
  POST   /governance/findings/{id}/confirm 人工确认（L2 执行前置）
  POST   /governance/findings/{id}/execute 执行建议动作（L2 未确认 → 400）
  POST   /governance/findings/{id}/dismiss 判定「业务允许」并沉淀规则
  GET    /governance/rules                 规则列表
  POST   /governance/rules                 新建规则
  PATCH  /governance/rules/{id}            改规则（启用/禁用/判定/动作）
  GET    /governance/summary               治理概览（open 数 / 按探测器分布 / 近期执行）

★ `/judge` 是设计 §八 之外的补充：设计只列了体检入口，但「体检会持续报新 open finding
  而研判无入口」会让 finding 只增不减（#167 已暴露）；低峰自动研判之外必须给管理页一个
  手动按钮，故补一个与 `/scans` 同构的异步入口。已在设计文档 §八 登记。

权限（设计 §八）：查看 = admin，执行 = admin（confirm/dismiss 同）→ 全部端点 AdminUser。

响应遵循 error-code 规范（{code:0,data,message}；错误一律 HTTPException）。
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import AdminUser, DBSession
from app.models.governance_finding import (
    DETECTOR_FILLABLE_GAP,
    DETECTOR_FK_ORPHAN,
    DETECTOR_IMPOSSIBLE_DATE,
    DETECTOR_ROW_COUNT_JUMP,
    DETECTOR_TEXT_ANOMALY,
    DETECTOR_UNIQUE_INDEX_DUP,
    GovernanceFinding,
)
from app.models.governance_rule import (
    AUTO_LEVEL_READONLY,
    DISPOSITION_LEVELS,
    GovernanceRule,
    disposition_level,
)
from app.models.system_event import SystemEvent
from app.schemas.common import R
from app.services.governance_execute_service import (
    EVENT_CATEGORY,
    EVENT_EXECUTE,
    ExecuteError,
    confirm_finding,
    execute_finding,
)
from app.services.governance_judge_service import (
    EVENT_JUDGE,
    EVENT_RULE_SINK,
    JudgeError,
    dismiss_finding,
    start_judge_task,
)
from app.services.governance_scan_service import EVENT_SCAN, start_scan_task
from app.services.system_event_service import record_system_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/governance", tags=["AI 数据治理"])

DETECTORS = (DETECTOR_UNIQUE_INDEX_DUP, DETECTOR_ROW_COUNT_JUMP, DETECTOR_FK_ORPHAN,
             DETECTOR_TEXT_ANOMALY, DETECTOR_IMPOSSIBLE_DATE, DETECTOR_FILLABLE_GAP)
_MAX_PAGE_SIZE = 200


def _finding_dict(f: GovernanceFinding) -> dict:
    """finding → 接口返回 dict（含证据、研判产物、执行凭证）。

    `auto_level` / `requires_confirm` 由服务端统一算（前端不重复定义级别映射，
    避免「前端以为 L1、后端按 L2 拦」这类漂移）。
    """
    action = (f.suggestion or {}).get("action")
    level = disposition_level(action)
    return {
        "id": f.id,
        "detector": f.detector,
        "table_name": f.table_name,
        "target": f.target,
        "severity_raw": f.severity_raw,
        "metrics": f.metrics,
        "samples": f.samples,
        "repro_sql": f.repro_sql,
        "detected_at": f.detected_at.isoformat() if f.detected_at else None,
        "status": f.status,
        "verdict": f.verdict,
        "verdict_reason": f.verdict_reason,
        "suggestion": f.suggestion,
        "confidence": f.confidence,
        "judged_at": f.judged_at.isoformat() if f.judged_at else None,
        "rule_id": f.rule_id,
        "confirmed_by": f.confirmed_by,
        "confirmed_at": f.confirmed_at.isoformat() if f.confirmed_at else None,
        "executed_at": f.executed_at.isoformat() if f.executed_at else None,
        "execute_result": f.execute_result,
        # 服务端算出的分级（前端据此决定是否显示「确认」按钮 / 是否可点「执行」）
        "auto_level": level,
        "requires_confirm": level in DISPOSITION_LEVELS.values() and level == "L2_irreversible",
        "updated_at": f.updated_at.isoformat() if f.updated_at else None,
    }


def _rule_dict(r: GovernanceRule) -> dict:
    return {
        "id": r.id,
        "detector": r.detector,
        "table_name": r.table_name,
        "target_pattern": r.target_pattern,
        "match_metrics": r.match_metrics,
        "verdict": r.verdict,
        "reason": r.reason,
        "disposition": r.disposition,
        "disposition_params": r.disposition_params,
        "auto_level": r.auto_level,
        "hit_count": r.hit_count,
        "last_hit_at": r.last_hit_at.isoformat() if r.last_hit_at else None,
        "source_finding_id": r.source_finding_id,
        "created_by": r.created_by,
        "enabled": r.enabled,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _event_dict(ev: SystemEvent) -> dict:
    """异步任务占位事件 → 轮询返回体（scan_id/judge_id 就是事件 id）。"""
    detail = None
    if ev.detail:
        try:
            detail = json.loads(ev.detail)
        except (TypeError, ValueError):
            detail = {"raw": ev.detail}
    return {
        "task_id": ev.id,
        "action": ev.action,
        "status": ev.status,
        "title": ev.title,
        "summary": ev.summary,
        "detail": detail,
        "created_at": ev.created_at.isoformat() if ev.created_at else None,
        "finished": ev.status in ("success", "partial", "failed"),
    }


def _start_placeholder_event(db, action: str, title: str) -> SystemEvent:
    """建占位事件（status=running）→ 其 id 即 202 返回的 task_id。

    设计要点：**一行事件贯穿 running→终态**（不是开始写一行、结束再写一行），
    避免重复留痕淹没真实变化（#166 教训）。后台任务用 `update_system_event` 回填。
    """
    return record_system_event(
        db, category=EVENT_CATEGORY, action=action, title=title,
        summary="已提交，执行中", status="running",
        detail=json.dumps({"submitted": True}, ensure_ascii=False), commit=True,
    )


# ═══════════════════════════════════════════════════════════════════════
# 一、体检 / 研判（异步 202 + 轮询）
# ═══════════════════════════════════════════════════════════════════════
@router.post("/scans", response_model=R, status_code=status.HTTP_202_ACCEPTED,
             summary="手动触发一次数据体检（202 + scan_id，异步）")
async def create_scan(db: DBSession, user: AdminUser):
    """提交体检 → 立即返回 scan_id；后台扫 D1/D2/D3 并落 findings。

    轮询 GET /governance/scans/{scan_id} 直到 finished=true。
    """
    ev = _start_placeholder_event(db, EVENT_SCAN, "数据体检: 已提交")
    start_scan_task(ev.id)
    logger.info("governance 手动体检已提交 scan_id=%s user=%s", ev.id, user.username)
    return R(data={"scan_id": ev.id})


@router.get("/scans/{scan_id}", response_model=R, summary="轮询体检状态 + 本批概要")
def get_scan(scan_id: int, db: DBSession, _: AdminUser):
    ev = db.get(SystemEvent, scan_id)
    if ev is None or ev.category != EVENT_CATEGORY or ev.action != EVENT_SCAN:
        raise HTTPException(404, "体检任务不存在")
    return R(data=_event_dict(ev))


@router.post("/judge", response_model=R, status_code=status.HTTP_202_ACCEPTED,
             summary="手动触发一次研判（202 + judge_id，异步，经 L1 门闸）")
async def create_judge(db: DBSession, user: AdminUser):
    """提交研判 → 返回 judge_id；后台对全部 open finding 走「规则短路 → 批量 AI」。

    规则命中的 finding **不调 LLM**（零成本），故可放心对全量 open 提交。
    """
    ev = _start_placeholder_event(db, EVENT_JUDGE, "治理研判: 已提交")
    start_judge_task(ev.id)
    logger.info("governance 手动研判已提交 judge_id=%s user=%s", ev.id, user.username)
    return R(data={"judge_id": ev.id})


@router.get("/judges/{judge_id}", response_model=R, summary="轮询研判状态")
def get_judge(judge_id: int, db: DBSession, _: AdminUser):
    ev = db.get(SystemEvent, judge_id)
    if ev is None or ev.category != EVENT_CATEGORY or ev.action != EVENT_JUDGE:
        raise HTTPException(404, "研判任务不存在")
    return R(data=_event_dict(ev))


# ═══════════════════════════════════════════════════════════════════════
# 二、findings
# ═══════════════════════════════════════════════════════════════════════
@router.get("/findings", response_model=R, summary="体检发现列表（筛选 + 分页）")
def list_findings(
    db: DBSession, _: AdminUser,
    detector: str | None = Query(None, description="探测器筛选: unique_index_dup/row_count_jump/fk_orphan"),
    table_name: str | None = Query(None, description="按目标表筛选"),
    status_value: str | None = Query(None, alias="status",
                                     description="状态筛选: open/judged/resolved/ignored/auto_closed"),
    verdict: str | None = Query(None, description="研判结论筛选: real_issue/allowed_by_business/uncertain"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=_MAX_PAGE_SIZE),
):
    """列表（最新在前）。默认不过滤 → 管理页首屏即「全部待处理」。"""
    stmt = select(GovernanceFinding)
    if detector:
        stmt = stmt.where(GovernanceFinding.detector == detector)
    if table_name:
        stmt = stmt.where(GovernanceFinding.table_name == table_name)
    if status_value:
        stmt = stmt.where(GovernanceFinding.status == status_value)
    if verdict:
        stmt = stmt.where(GovernanceFinding.verdict == verdict)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(GovernanceFinding.detected_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return R(data={"items": [_finding_dict(f) for f in rows], "total": int(total),
                   "page": page, "page_size": page_size})


@router.get("/findings/{finding_id}", response_model=R, summary="单条详情（证据+研判+建议+复现SQL）")
def get_finding(finding_id: int, db: DBSession, _: AdminUser):
    f = db.get(GovernanceFinding, finding_id)
    if f is None:
        raise HTTPException(404, "finding 不存在")
    return R(data=_finding_dict(f))


@router.post("/findings/{finding_id}/confirm", response_model=R, summary="人工确认（L2 执行前置）")
def confirm(finding_id: int, payload: dict | None = None, *, db: DBSession, user: AdminUser):
    """确认 AI 建议（设计 §六 L2 前置）。

    payload: `{note?, params?}`。`params` 允许人工**覆盖 AI 给出的动作参数**
    （如 `fix_time` 的 `updates` 清单）—— 覆盖内容记入 `execute_result`，
    **不修改 `suggestion`**（AI 产物与人工决策分离，可追溯「AI 建议了什么、人改成了什么」）。
    """
    body = payload or {}
    try:
        data = confirm_finding(db, finding_id, operator=user.username,
                               note=body.get("note"), params=body.get("params"))
    except ExecuteError as e:
        raise HTTPException(400, str(e))
    return R(data=data, message="已确认")


@router.post("/findings/{finding_id}/execute", response_model=R,
             summary="执行建议动作（L0/L1 直接执行；L2 未确认 → 400）")
def execute(finding_id: int, db: DBSession, user: AdminUser):
    """按分级执行（设计 §六）。

    L2（delete_dups/create_index/fix_time）**未人工确认一律 400**（红线）；
    执行前强制备份、执行后写凭证并重建受影响宽表 + 失效缓存。
    """
    try:
        data = execute_finding(db, finding_id, operator=user.username)
    except ExecuteError as e:
        raise HTTPException(400, str(e))
    return R(data=data, message=f"执行完成（{data['auto_level']}）")


@router.post("/findings/{finding_id}/dismiss", response_model=R,
             summary="判定「业务允许」并沉淀规则（下次同类直接命中）")
def dismiss(finding_id: int, payload: dict | None = None, *, db: DBSession, user: AdminUser):
    """payload: {reason?}；沉淀的规则 verdict=allowed_by_business、级别恒 L0 只读。"""
    try:
        data = dismiss_finding(db, finding_id, reason=(payload or {}).get("reason"),
                               created_by=user.username)
    except JudgeError as e:
        raise HTTPException(400, str(e))
    return R(data=data, message="已判定为业务允许并沉淀规则")


# ═══════════════════════════════════════════════════════════════════════
# 三、治理规则（人工可增删改；AI 沉淀的也在其中）
# ═══════════════════════════════════════════════════════════════════════
@router.get("/rules", response_model=R, summary="治理规则列表")
def list_rules(
    db: DBSession, _: AdminUser,
    detector: str | None = Query(None),
    enabled: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=_MAX_PAGE_SIZE),
):
    stmt = select(GovernanceRule)
    if detector:
        stmt = stmt.where(GovernanceRule.detector == detector)
    if enabled is not None:
        stmt = stmt.where(GovernanceRule.enabled.is_(enabled))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(GovernanceRule.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return R(data={"items": [_rule_dict(r) for r in rows], "total": int(total),
                   "page": page, "page_size": page_size})


_RULE_EDITABLE = ("table_name", "target_pattern", "match_metrics", "verdict", "reason",
                  "disposition", "disposition_params", "auto_level", "enabled")


def _validate_rule_payload(payload: dict, *, creating: bool) -> None:
    """规则入参校验（新建必填 + 级别红线）。

    红线：**不可逆级别（L2）不得作为自动执行规则** —— 规则是「命中即自动给建议」的，
    若允许 L2 规则存在，等于把「不可逆动作自动执行」写成配置（设计 §六 明确禁止）。
    """
    if creating:
        for k in ("detector", "table_name", "target_pattern", "verdict", "reason"):
            if not str(payload.get(k) or "").strip():
                raise HTTPException(400, f"新建规则缺少必填字段: {k}")
        if payload["detector"] not in DETECTORS:
            raise HTTPException(400, f"detector 非法: {payload['detector']}（可选 {list(DETECTORS)}）")
    if "auto_level" in payload and payload["auto_level"] not in (
            AUTO_LEVEL_READONLY, "L1_reversible"):
        raise HTTPException(400, "规则的 auto_level 只允许 L0_readonly / L1_reversible"
                                 "（不可逆动作不得写成自动规则，设计 §六 红线）")
    if "disposition" in payload and disposition_level(payload["disposition"]) == "L2_irreversible":
        raise HTTPException(400, "规则不得直接配置不可逆动作（delete_dups/create_index 等），"
                                 "请改为 report_only 或走人工执行")


@router.post("/rules", response_model=R, summary="新建治理规则（人工）")
def create_rule(payload: dict, db: DBSession, user: AdminUser):
    _validate_rule_payload(payload, creating=True)
    rule = GovernanceRule(
        detector=payload["detector"],
        table_name=payload["table_name"],
        target_pattern=payload["target_pattern"],
        match_metrics=payload.get("match_metrics"),
        verdict=payload["verdict"],
        reason=payload["reason"],
        disposition=payload.get("disposition") or "report_only",
        disposition_params=payload.get("disposition_params"),
        auto_level=payload.get("auto_level") or AUTO_LEVEL_READONLY,
        hit_count=0,
        source_finding_id=None,
        created_by=user.username,
        enabled=bool(payload.get("enabled", True)),
    )
    db.add(rule)
    record_system_event(
        db, category=EVENT_CATEGORY, action=EVENT_RULE_SINK,
        title=f"治理规则新建(人工): {rule.table_name}/{rule.target_pattern}",
        summary=f"detector={rule.detector} verdict={rule.verdict} by {user.username}",
        status="success",
        detail=json.dumps({"table_name": rule.table_name, "target_pattern": rule.target_pattern,
                           "verdict": rule.verdict, "created_by": user.username},
                          ensure_ascii=False),
        commit=False,
    )
    db.commit()
    db.refresh(rule)
    return R(data=_rule_dict(rule), message="规则已创建")


@router.patch("/rules/{rule_id}", response_model=R, summary="修改治理规则（启用/禁用/判定/动作）")
def update_rule(rule_id: int, payload: dict, db: DBSession, user: AdminUser):
    rule = db.get(GovernanceRule, rule_id)
    if rule is None:
        raise HTTPException(404, "规则不存在")
    _validate_rule_payload(payload, creating=False)
    for k in _RULE_EDITABLE:
        if k in payload and payload[k] is not None:
            setattr(rule, k, payload[k])
    logger.info("governance 规则更新 id=%s by=%s fields=%s",
                rule_id, user.username, [k for k in _RULE_EDITABLE if k in payload])
    db.commit()
    db.refresh(rule)
    return R(data=_rule_dict(rule), message="规则已更新")


# ═══════════════════════════════════════════════════════════════════════
# 四、概览
# ═══════════════════════════════════════════════════════════════════════
@router.get("/summary", response_model=R, summary="治理概览（open 数 / 探测器分布 / 近期执行）")
def summary(db: DBSession, _: AdminUser):
    """管理页首屏卡片的数据源。

    ⓘ 口径：`open` = 待研判；`judged` = 已研判待处置；`auto_closed` = 重扫不再命中自动收敛
    （未执行任何动作）；`resolved` = 已执行治理动作并核销。
    """
    by_status = dict(db.execute(
        select(GovernanceFinding.status, func.count()).group_by(GovernanceFinding.status)
    ).all())
    by_detector = dict(db.execute(
        select(GovernanceFinding.detector, func.count())
        .where(GovernanceFinding.status.in_(("open", "judged")))
        .group_by(GovernanceFinding.detector)
    ).all())
    by_verdict = dict(db.execute(
        select(GovernanceFinding.verdict, func.count())
        .where(GovernanceFinding.verdict.isnot(None))
        .group_by(GovernanceFinding.verdict)
    ).all())
    recent = db.execute(
        select(SystemEvent.id, SystemEvent.action, SystemEvent.title, SystemEvent.status,
               SystemEvent.summary, SystemEvent.created_at)
        .where(SystemEvent.category == EVENT_CATEGORY,
               SystemEvent.action.in_((EVENT_SCAN, EVENT_JUDGE, EVENT_EXECUTE)))
        .order_by(SystemEvent.id.desc()).limit(10)
    ).mappings().all()
    rules = db.execute(
        select(func.count(), func.coalesce(func.sum(GovernanceRule.hit_count), 0))
        .where(GovernanceRule.enabled.is_(True))
    ).one()
    return R(data={
        "by_status": {k: int(v) for k, v in by_status.items()},
        "by_detector_open": {k: int(v) for k, v in by_detector.items()},
        "by_verdict": {k: int(v) for k, v in by_verdict.items()},
        "rules_enabled": int(rules[0] or 0),
        "rules_hits": int(rules[1] or 0),
        "recent_events": [
            {**dict(r), "created_at": r["created_at"].isoformat() if r["created_at"] else None}
            for r in recent
        ],
        "disposition_levels": DISPOSITION_LEVELS,
    })

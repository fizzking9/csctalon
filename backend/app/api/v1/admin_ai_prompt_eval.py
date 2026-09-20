"""AI 提示词评估中心路由 (工单 #173, 设计: docs/design/AI提示词评估中心-20260920.md)

设计说明（Design Rationale）
- 目的：给 admin 提供「提示词评估 → 半自动迭代」的生产可见入口（评估侧），
  与「AI 管理（配置侧）」「AI 报告历史（查看侧）」并列。
- 逻辑：评估端点全部聚合读（summary/templates/anchor-check/governance-rules）；
  suggest 产建议并落库为 proposed 行（去重）；confirm/apply 走 prompt_iterations
  状态机，apply 由服务端写入模板/规则（半自动 = 人工确认 + 一键落地）。
- 取舍：不允许只记录类建议（anchor_guide/new_template）应用（无写入路径，铁律）；
  所有端点 admin-only，响应统一 {code, data, message}。
"""

import logging

from fastapi import APIRouter, HTTPException

from app.api.deps import AdminUser, DBSession
from app.schemas.common import R
from app.services import prompt_eval_service as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/ai/prompt-eval", tags=["admin-ai-prompt-eval"])


def _validate_days(days: int) -> int:
    """评估窗口合法值 1/7/30（默认 7），非法 400。"""
    if days not in (1, 7, 30):
        raise HTTPException(400, "days 仅支持 1/7/30（默认 7）")
    return days


@router.get("/summary", response_model=R, summary="提示词评估总览(admin)")
def eval_summary(db: DBSession, _: AdminUser, days: int = 7):
    """近 N 天总览：命中分布/命中率/live 占比/模板命中热度/规则健康度/高频兜底问句。"""
    return R(data=svc.build_summary(db, _validate_days(days)))


@router.get("/templates", response_model=R, summary="模板级评估(admin)")
def eval_templates(db: DBSession, _: AdminUser, days: int = 7):
    """模板命中率 + 关键词 + 缺失维度，按命中率升序（问题模板在前）。"""
    return R(data=svc.build_template_eval(db, _validate_days(days)))


@router.get("/anchor-check", response_model=R, summary="跨链路锚点一致性检查(admin)")
def eval_anchor(db: DBSession, _: AdminUser, days: int = 7):
    """生成侧(_SYSTEM_PROMPT) vs 摘取侧(H2_ANCHORS)静态比对 + 报告实际 H2 漂移。"""
    return R(data=svc.build_anchor_check(db, _validate_days(days)))


@router.get("/governance-rules", response_model=R, summary="治理规则健康度(admin)")
def eval_rules(db: DBSession, _: AdminUser):
    """规则命中 + 效果回填 + fp_ratio（>50% 预警）。"""
    return R(data=svc.build_governance_eval(db))


@router.post("/suggest", response_model=R, summary="生成半自动迭代建议并落库(admin)")
def gen_suggestions(db: DBSession, user: AdminUser, days: int = 7):
    """按执行结果产建议，落库为 proposed 行（同 target+新值去重），返回本次新增。"""
    suggestions = svc.build_suggestions(db, _validate_days(days))
    created = []
    for item in suggestions:
        row = svc.propose_iteration(db, user.id, item)
        if row is not None:
            created.append(svc._iter_dict(row))
    return R(data={"suggested": len(suggestions), "created": len(created),
                   "items": created})


@router.get("/iterations", response_model=R, summary="迭代建议列表(admin)")
def list_iterations(db: DBSession, _: AdminUser,
                    status: str | None = None, scope: str | None = None, limit: int = 100):
    """迭代建议抽屉：按 status/scope 过滤。"""
    return R(data=svc.list_iterations(db, status=status, scope=scope, limit=limit))


@router.post("/iterations/{iteration_id}/confirm", response_model=R, summary="人工拍板(admin)")
def confirm_iteration(db: DBSession, user: AdminUser, iteration_id: int, approved: bool = True):
    """approved=True → confirmed（可应用）；False → rejected（不采纳）。"""
    try:
        return R(data=svc.confirm_iteration(db, user.id, iteration_id, approved))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/iterations/{iteration_id}/apply", response_model=R, summary="应用建议(admin)")
def apply_iteration(db: DBSession, user: AdminUser, iteration_id: int):
    """服务端写入模板/规则（仅 template_keyword/template_question/rule_adjust）。"""
    try:
        return R(data=svc.apply_iteration(db, user.id, iteration_id))
    except ValueError as e:
        raise HTTPException(400, str(e))

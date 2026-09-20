"""提示词版本管理路由 (工单 #178, 设计: docs/design/AI提示词评估中心-20260920.md 演进)

设计说明（Design Rationale）
- 目的：给 admin 提供两级提示词（生成侧 gen_system / 摘取侧 extract_system）的
  「版本列表 → 新增版本 → 激活/回滚」生产入口。激活后加载侧 DB 优先，无需改代码；
  回滚 = 激活历史版本，账本完整可审计。
- 逻辑：与 #173 prompt-eval（评估侧）同前缀，挂 `/admin/ai/prompt-eval/prompt-configs`；
  全部走 prompt_config_service 状态机；激活/回滚留痕（谁/何时/旧→新版本）。
- 取舍：新增版本不自动激活（草稿，金丝雀验证 #179 通过后人工激活）；
  激活/回滚不调 LLM（避免阻塞）；所有端点 admin-only，响应统一 {code, data, message}。
"""

import logging

from fastapi import APIRouter, HTTPException

from app.api.deps import AdminUser, DBSession
from app.schemas.common import R
from app.services import prompt_config_service as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/ai/prompt-eval/prompt-configs", tags=["admin-ai-prompt-config"])


def _validate_code(code: str) -> str:
    if code not in svc.PROMPT_CODES:
        raise HTTPException(400, f"code 仅支持: {'/'.join(svc.PROMPT_CODES)}")
    return code


@router.get("", response_model=R, summary="提示词版本列表(admin)")
def list_versions(db: DBSession, _: AdminUser, code: str = "gen_system"):
    """按 code 返回版本列表（含生效标记/激活人/时间），供版本管理界面。"""
    return R(data={"code": _validate_code(code),
                   "items": svc.list_versions(db, code)})


@router.post("", response_model=R, summary="新增提示词版本(admin)")
def create_version(db: DBSession, user: AdminUser, payload: dict):
    """新增版本（version+1，不激活）；content 为空时复制当前生效版本为底。

    payload: {code: 必填, content: 可空(空=以生效版为底先改后升), name_cn: 可选}
    """
    code = _validate_code((payload or {}).get("code", ""))
    try:
        row = svc.create_new_version(
            db, code,
            content=(payload or {}).get("content"),
            user_id=user.id,
            name_cn=(payload or {}).get("name_cn"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return R(data=svc._dict(row), message=f"已新增版本 v{row.version}（未激活，需人工激活后生效）")


@router.post("/{config_id}/activate", response_model=R, summary="激活提示词版本(admin, 金丝雀冒烟通过才激活)")
def activate_version(db: DBSession, user: AdminUser, config_id: int,
                     smoke: bool = True, baseline: str | None = None):
    """激活指定版本：金丝雀冒烟（LLM 非空返回）通过才激活，同 code 旧生效自动取消。

    - smoke=True(默认): 先用候选提示词调一次 LLM；ok=False → 400 拒绝激活；
      LLM 服务不可用(degraded) → 放行（不因基建故障阻塞版本管理）
    - baseline: 未显式传时自动记「激活时命中率/live 占比(7d)」供防振荡参照
    """
    target = svc.get_version(db, config_id)
    if target is None:
        raise HTTPException(400, f"提示词版本不存在: {config_id}")
    if smoke:
        res = svc.canary_smoke(db, target["code"], target["content"])
        if res.get("ok") is False:
            raise HTTPException(400, f"金丝雀冒烟未通过，已拒绝激活: {res.get('error')}")
        if res.get("ok") is None and res.get("degraded"):
            logger.warning("金丝雀冒烟降级放行(LLM 服务不可用): code=%s err=%s",
                           target["code"], res.get("error"))
    if not baseline:
        # 防振荡基线：激活时评估命中率/live 占比（近 7 天）
        try:
            from app.services.prompt_eval_service import build_summary
            ov = build_summary(db, 7).get("overall", {})
            baseline = f"hit_rate={ov.get('hit_rate')} live_ratio={ov.get('live_ratio')} (7d)"
        except Exception as e:  # noqa: BLE001  基线取不到不阻塞激活
            logger.warning("激活 %s 自动基线计算失败(忽略): %s", target["code"], e)
    try:
        row = svc.activate_version(db, user.id, config_id, baseline=baseline)
    except ValueError as e:
        raise HTTPException(400, str(e))
    msg = f"版本 v{row['version']} 已生效"
    if smoke and res.get("ok") is None:
        msg += "（金丝雀冒烟降级放行：LLM 服务未就绪）"
    return R(data=row, message=msg)


@router.post("/{config_id}/rollback", response_model=R, summary="回滚到指定版本(admin, 金丝雀冒烟通过才回滚)")
def rollback_version(db: DBSession, user: AdminUser, config_id: int, smoke: bool = True):
    """回滚 = 激活历史版本（不删当前版本行）；同样先过金丝雀冒烟。"""
    target = svc.get_version(db, config_id)
    if target is None:
        raise HTTPException(400, f"提示词版本不存在: {config_id}")
    if smoke:
        res = svc.canary_smoke(db, target["code"], target["content"])
        if res.get("ok") is False:
            raise HTTPException(400, f"金丝雀冒烟未通过，已拒绝回滚: {res.get('error')}")
    try:
        row = svc.rollback_to_version(db, user.id, config_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return R(data=row, message=f"已回滚到版本 v{row['version']}")

"""治理研判提示词版本管理路由 (2026-09-23 提示词全量版本化)

给 admin 提供 governance_prompts(judge_system/judge_user) 的「版本列表 → 新增版本 →
激活/回滚」生产入口。激活后加载侧 DB 优先(load_effective_prompt), 无需改代码; 回滚=
激活历史版本, 账本完整可审计。

与 prompt_configs(#178) 路由同构, 但不加金丝雀冒烟(研判提示词非开放问答侧, 冒烟价值低);
admin-only, 激活/回滚不调 LLM(避免阻塞)。
"""
import logging

from fastapi import APIRouter, HTTPException

from app.api.deps import AdminUser, DBSession
from app.schemas.common import R
from app.services import governance_prompt_service as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/governance/prompts", tags=["admin-governance-prompt"])


def _validate_code(code: str) -> str:
    if code not in svc.PROMPT_CODES:
        raise HTTPException(400, f"code 仅支持: {'/'.join(svc.PROMPT_CODES)}")
    return code


@router.get("", response_model=R, summary="研判提示词版本列表(admin)")
def list_versions(db: DBSession, _: AdminUser, code: str = "judge_system"):
    """按 code 返回版本列表(含生效标记/激活人/时间/change_reason)。"""
    return R(data={"code": _validate_code(code), "items": svc.list_versions(db, code)})


@router.post("", response_model=R, summary="新增研判提示词版本(admin)")
def create_version(db: DBSession, user: AdminUser, payload: dict):
    """新增版本(version+1, 不激活); content 为空时复制当前生效版本为底。

    payload: {code: 必填, content: 可空(空=以生效版为底先改后升), name_cn: 可选,
              change_reason: 可选(解决什么问题/为何改), references: 可选(参考依据来源)}
    """
    code = _validate_code((payload or {}).get("code", ""))
    try:
        row = svc.create_new_version(
            db, code,
            content=(payload or {}).get("content"),
            user_id=user.id,
            name_cn=(payload or {}).get("name_cn"),
            change_reason=(payload or {}).get("change_reason"),
            references=(payload or {}).get("references"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return R(data=svc._dict(row), message=f"已新增版本 v{row.version}（未激活，需人工激活后生效）")


@router.post("/{config_id}/activate", response_model=R, summary="激活研判提示词版本(admin)")
def activate_version(db: DBSession, user: AdminUser, config_id: int):
    """激活指定版本: 同 code 旧生效自动取消, 账本留痕。"""
    if svc.get_version(db, config_id) is None:
        raise HTTPException(400, f"提示词版本不存在: {config_id}")
    try:
        row = svc.activate_version(db, user.id, config_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return R(data=row, message=f"版本 v{row['version']} 已生效")


@router.post("/{config_id}/rollback", response_model=R, summary="回滚到指定研判提示词版本(admin)")
def rollback_version(db: DBSession, user: AdminUser, config_id: int):
    """回滚 = 激活历史版本(不删当前版本行); 账本完整。"""
    if svc.get_version(db, config_id) is None:
        raise HTTPException(400, f"提示词版本不存在: {config_id}")
    try:
        row = svc.rollback_to_version(db, user.id, config_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return R(data=row, message=f"已回滚到版本 v{row['version']}")

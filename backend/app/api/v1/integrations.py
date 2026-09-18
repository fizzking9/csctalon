"""接入配置包模板路由 (#44, A-20260910-002 LOCKED v1.0)

- GET    /integrations/profiles               模板列表
- POST   /integrations/profiles               新建模板
- PUT    /integrations/profiles/{id}          更新模板
- DELETE /integrations/profiles/{id}          删除模板
- POST   /integrations/profiles/{id}/reset    恢复默认内容(从内置 seed 读回)
- POST   /integrations/profiles/{id}/render   渲染配置包(纯函数, 不落库)
- POST   /integrations/profiles/{id}/rollback 回滚到上一版 content_md (#47)

权限: 全部 AdminUser(改接入提示词 = 高风险, 同 AI Focus 配置)。
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import AdminUser, DBSession
from app.schemas.common import R
from app.schemas.integration_profile import (
    IntegrationProfileCreate,
    IntegrationProfileUpdate,
    ProfileRenderRequest,
    ProfileRollbackRequest,
)
from app.services import integration_profile_service as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["接入配置包"])


@router.get("/profiles", response_model=R, summary="接入配置包模板列表")
def list_profiles(db: DBSession, _: AdminUser, include_disabled: bool = False):
    return R(data=svc.list_profiles(db, include_disabled=include_disabled))


@router.post("/profiles", response_model=R, summary="新建接入配置包模板")
def create_profile(payload: IntegrationProfileCreate, db: DBSession, me: AdminUser):
    return R(data=svc.create_profile(db, payload, actor=me.username))


@router.put("/profiles/{profile_id}", response_model=R, summary="更新接入配置包模板")
def update_profile(profile_id: int, payload: IntegrationProfileUpdate, db: DBSession, me: AdminUser):
    row = svc.update_profile(db, profile_id, payload, actor=me.username)
    if row is None:
        raise HTTPException(404, "模板不存在")
    return R(data=row)


@router.delete("/profiles/{profile_id}", response_model=R, summary="删除接入配置包模板")
def delete_profile(profile_id: int, db: DBSession, _: AdminUser):
    if not svc.delete_profile(db, profile_id):
        raise HTTPException(404, "模板不存在")
    return R(data={"ok": True})


@router.post("/profiles/{profile_id}/reset", response_model=R, summary="恢复模板默认内容")
def reset_profile(profile_id: int, db: DBSession, me: AdminUser):
    """管理员误改坏模板时一键还原(从内置 seed 常量读回)。"""
    row = svc.reset_profile(db, profile_id, actor=me.username)
    if row is None:
        raise HTTPException(404, "模板不存在")
    return R(data=row)


@router.post("/profiles/{profile_id}/rollback", response_model=R, summary="回滚到上一版模板内容")
def rollback_profile(profile_id: int, payload: ProfileRollbackRequest, db: DBSession, me: AdminUser):
    """#47: 把 previous_content_md 写回 content_md(可逆回滚)。

    - 二次确认: 请求体必须显式传 `{"confirm": true}`, 否则返回 400(防误点)
    - 无历史版本(previous_content_md IS NULL) → 400 + 提示「没有可回滚的版本」
    - 回滚动作本身也是一次「保存当前为 previous」, 再次调用即可回到回滚前状态(可逆)
    """
    if not payload.confirm:
        raise HTTPException(400, "回滚需二次确认: 请传 {\"confirm\": true}")
    result = svc.rollback_profile(db, profile_id, actor=me.username)
    if result is None:
        raise HTTPException(404, "模板不存在")
    if isinstance(result, dict) and result.get("_no_previous"):
        raise HTTPException(400, "没有可回滚的历史版本(该模板从未更新过 content_md)")
    return R(data=result)


@router.post("/profiles/{profile_id}/render", response_model=R, summary="渲染配置包")
def render_profile(profile_id: int, payload: ProfileRenderRequest, db: DBSession, request: Request, _: AdminUser):
    """渲染为可直接复制的 Markdown。

    ⚠️ 纯函数渲染: 无副作用、不落库; token 位置为占位符(不返回明文)。
    """
    row = svc.get_profile(db, profile_id)
    if row is None:
        raise HTTPException(404, "模板不存在")
    values = payload.model_dump(exclude_none=True)
    base_url = (payload.base_url or str(request.base_url)).rstrip("/")
    return R(data=svc.render_profile(row, values, base_url=base_url))

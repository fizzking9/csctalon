"""系统设置 API - 同步源配置管理(admin only)

提供 BMS 同步源运行配置(URL/账号/密码/开关)的查询与更新。
凭证加密存储,GET 接口仅返回是否已配置(has_credentials),不返回明文。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import AdminUser, DBSession
from app.schemas.common import R
from app.services.sync_source_config_service import (
    list_sync_source_configs,
    update_sync_source_config,
)

router = APIRouter(prefix="/system", tags=["系统设置"])


class SyncSourceOut(BaseModel):
    data_type: str
    name: str
    base_url: str
    page_path: str
    time_labels: list | None = None
    has_credentials: bool
    default_sync: bool
    enabled: bool
    sort_order: int


class SyncSourceUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    page_path: str | None = None
    time_labels: list | None = None
    username: str | None = None  # 留空表示不改
    password: str | None = None  # 留空表示不改
    default_sync: bool | None = None
    enabled: bool | None = None


@router.get("/sync-sources", response_model=R[list[SyncSourceOut]], summary="同步源配置列表")
def list_sync_sources(db: DBSession, _: AdminUser):
    """获取全部同步源配置(凭证掩码,仅返回 has_credentials)"""
    rows = list_sync_source_configs(db)
    return R(data=[SyncSourceOut(**r) for r in rows])


@router.put("/sync-sources/{data_type}", response_model=R[dict], summary="更新同步源配置")
def update_sync_source(data_type: str, payload: SyncSourceUpdate, db: DBSession, _: AdminUser):
    """更新单个同步源配置;username/password 留空表示不改,非空则加密入库"""
    updated = update_sync_source_config(db, data_type, payload.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail=f"同步源不存在: {data_type}")
    # 返回时剥除明文凭证
    updated.pop("username", None)
    updated.pop("password", None)
    return R(data=updated)

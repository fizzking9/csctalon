"""共享文档在线采集源同步 API 路由 (#33, A-20260907-002)

- GET    /sync/shared-docs              : 列出所有共享文档采集源配置(Manager)
- POST   /sync/shared-docs              : 新建采集源配置(Admin)
- PUT    /sync/shared-docs/{id}         : 更新采集源配置(Admin)
- DELETE /sync/shared-docs/{id}         : 删除采集源配置(Admin)
- POST   /sync/shared-docs/{id}/toggle  : 启停采集源(Admin)
- POST   /sync/shared-docs/sync-now     : 手动触发同步某源(Admin)

同步日志复用既有 complaint_sync_logs / manual_refund_sync_logs 表
(与 workbuddy push 同表, sync_source='shared_doc' 区分来源)。
"""

import logging

from fastapi import APIRouter, HTTPException

from app.api.deps import AdminUser, DBSession, ManagerUser
from app.schemas.common import R
from app.services import shared_doc_sync_service as sdss

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync/shared-docs", tags=["数据同步-共享文档"])


def _validate_source_id(source_id) -> int:
    """source_id 必须是正整数"""
    if isinstance(source_id, bool) or not isinstance(source_id, int) or source_id <= 0:
        raise HTTPException(status_code=400, detail="source_id 必须是正整数")
    return source_id


@router.get("", response_model=R, summary="列出所有共享文档采集源配置")
def list_shared_docs(db: DBSession, _: ManagerUser):
    """列出全部 doc_sync_sources(含启用状态与最近同步结果)"""
    return R(data=sdss.list_doc_sources(db))


@router.post("", response_model=R, summary="新建共享文档采集源配置")
def create_shared_doc(payload: dict, db: DBSession, _: AdminUser):
    """body: {platform, doc_url, doc_name?, target_type, sheet_rules?, state_file?,
             enabled?, sync_interval_minutes?}

    返回新建的配置行(含 id)。
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    try:
        row = sdss.save_doc_source(db, payload, source_id=None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return R(data=sdss.row_to_dict(row))


@router.put("/{source_id}", response_model=R, summary="更新共享文档采集源配置")
def update_shared_doc(source_id: int, payload: dict, db: DBSession, _: AdminUser):
    """body: 同新建(可只传需修改字段; platform/doc_url 若传则需合法且不与他行冲突)"""
    source_id = _validate_source_id(source_id)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    try:
        row = sdss.save_doc_source(db, payload, source_id=source_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return R(data=sdss.row_to_dict(row))


@router.delete("/{source_id}", response_model=R, summary="删除共享文档采集源配置")
def delete_shared_doc(source_id: int, db: DBSession, _: AdminUser):
    """删除采集源(同步日志/历史数据保留在业务表)"""
    source_id = _validate_source_id(source_id)
    try:
        sdss.delete_doc_source(db, source_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return R(data={"deleted": source_id})


@router.post("/{source_id}/toggle", response_model=R, summary="启停共享文档采集源")
def toggle_shared_doc(source_id: int, payload: dict, db: DBSession, _: AdminUser):
    """body: {"enabled": bool} — 启/停定时采集(仅接受真布尔, 拒绝字符串"false")"""
    source_id = _validate_source_id(source_id)
    row = sdss.get_doc_source(db, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"采集源不存在: id={source_id}")
    if not isinstance(payload, dict) or "enabled" not in payload:
        raise HTTPException(status_code=400, detail="body 需包含 enabled(bool)")
    enabled = payload["enabled"]
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled 必须是布尔值 true/false")
    row.enabled = enabled
    db.commit()
    db.refresh(row)
    return R(data=sdss.row_to_dict(row))


@router.post("/sync-now", response_model=R, summary="手动触发同步某共享文档源")
def shared_doc_sync_now(payload: dict, db: DBSession, _: AdminUser):
    """body: {"source_id": int}

    同步结果(成功/失败)会写回采集源配置行的 last_sync_* 字段;
    同步详情日志复用 complaint_sync_logs / manual_refund_sync_logs。
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    source_id = _validate_source_id(payload.get("source_id"))

    row = sdss.get_doc_source(db, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"采集源不存在: id={source_id}")
    if not row.enabled:
        raise HTTPException(status_code=400, detail=f"采集源已停用, 请先启用: id={source_id}")

    try:
        stats = sdss.sync_one_source(db, row, trigger="manual")
        return R(data={**stats, "source": sdss.row_to_dict(row)})
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[shared_doc] 手动同步源 #{source_id} 异常: {e}")
        raise HTTPException(status_code=500, detail=f"同步失败: {e}") from e

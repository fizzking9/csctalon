"""共享文档在线采集源同步 API 路由 (#33, A-20260907-002)

- GET  /sync/shared-docs            : 列出所有共享文档采集源配置(需登录 ManagerUser)
- POST /sync/shared-docs/sync-now   : 手动触发同步某源(需 AdminUser), body {"source_id": int}

同步日志复用既有 complaint_sync_logs / manual_refund_sync_logs 表(与 workbuddy push 同表,
sync_source='shared_doc' 区分来源)。
"""

import logging

from fastapi import APIRouter, HTTPException

from app.api.deps import AdminUser, DBSession, ManagerUser
from app.schemas.common import R
from app.services import shared_doc_sync_service as sdss

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync/shared-docs", tags=["数据同步-共享文档"])


@router.get("", response_model=R, summary="列出所有共享文档采集源配置")
def list_shared_docs(db: DBSession, _: ManagerUser):
    """列出全部 doc_sync_sources(含启用状态与最近同步结果)"""
    return R(data=sdss.list_doc_sources(db))


@router.post("/sync-now", response_model=R, summary="手动触发同步某共享文档源")
def shared_doc_sync_now(payload: dict, db: DBSession, _: AdminUser):
    """body: {"source_id": int}

    同步结果(成功/失败)会写回采集源配置行的 last_sync_* 字段;
    同步详情日志复用 complaint_sync_logs / manual_refund_sync_logs。
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    source_id = payload.get("source_id")
    if not isinstance(source_id, int) or isinstance(source_id, bool):
        raise HTTPException(status_code=400, detail="source_id 必须是整数")

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

"""workbuddy 投诉数据推送同步路由

- POST /sync/complaints/push : workbuddy 将 315投诉/卡商投诉 文件明细以 JSON 推送过来
  (每 20 分钟检查文件, 文件变化时推送)。鉴权: Authorization: Bearer <WORKBUDDY_SYNC_TOKEN>
  (或 X-Sync-Token 请求头)。未配置 token 时返回 503。
- GET /sync/complaints/logs  : 内部查看最近推送日志(需登录)。
"""

import hmac
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.core.config import settings
from app.models.complaint_sync_log import ComplaintSyncLog
from app.schemas.common import PageBase, R
from app.services.complaint_sync import sync_complaints_from_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync/complaints", tags=["数据同步-投诉"])


def _verify_sync_token(
    authorization: Annotated[str | None, Header()] = None,
    x_sync_token: Annotated[str | None, Header()] = None,
) -> bool:
    """校验 workbuddy 同步 token(常量时间比较防时序攻击)"""
    expected = settings.WORKBUDDY_SYNC_TOKEN
    if not expected:
        return False
    token: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_sync_token:
        token = x_sync_token.strip()
    if not token:
        return False
    return hmac.compare_digest(token, expected)


SyncTokenVerified = Annotated[bool, Depends(_verify_sync_token)]


@router.post("/push", response_model=R, summary="workbuddy 投诉数据推送(315/卡商)")
def push_complaints(
    payload: dict[str, Any],
    db: DBSession,
    token_ok: SyncTokenVerified,
):
    """workbuddy 将文件内容以 JSON 推送入库。

    Body 示例:
    ```json
    {
      "source_file": "315投诉处理登记表.xlsx",
      "external_complaints": [
        {"投诉日期": "2026-08-01", "投诉编号": "502609010000000000000", "投诉人": "张三",
         "投诉手机号": "138****0000", "投诉设备号": "67482454178206", "投诉问题": "...", "处理明细": "..."}
      ],
      "card_complaints": [
        {"投诉日期": "2026-08-01", "投诉群": "卡商A群", "投诉人": "李四", "设备号": "67482454178206", "投诉问题": "...", "处理人": "王五"}
      ]
    }
    ```
    行内字段名兼容 xlsx 中文列名(见 docs/workbuddy数据同步接入规范.md)与英文字段名。
    服务会做: 数据清洗(SN/日期/姓名/金额) → 指纹去重(重复推送不累积) → 按SN关联服务工单。
    """
    if not token_ok:
        if not settings.WORKBUDDY_SYNC_TOKEN:
            raise HTTPException(status_code=503, detail="WORKBUDDY_SYNC_TOKEN 未配置, 同步接口未启用")
        raise HTTPException(status_code=401, detail="同步 token 无效")
    # 采集方式总开关: 被动推送关闭则拒绝接收
    from app.services.ingest_mode_service import is_ingest_mode_enabled

    if not is_ingest_mode_enabled(db, "push"):
        raise HTTPException(status_code=503, detail="被动推送采集方式已关闭(可在 数据采集→投诉接入→采集方式 中开启)")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    source_file = (payload.get("source_file") or "").strip() or None
    if not payload.get("external_complaints") and not payload.get("card_complaints"):
        raise HTTPException(status_code=400, detail="external_complaints / card_complaints 至少提供一项")
    try:
        stats = sync_complaints_from_payload(db, payload, source_file=source_file, sync_source="workbuddy")
    except Exception as e:
        logger.exception(f"投诉推送失败: {e}")
        raise HTTPException(status_code=500, detail=f"同步失败: {e}") from e
    return R(data=stats)


@router.get("/config", response_model=R, summary="投诉推送接入配置状态(内部)")
def sync_push_config(db: DBSession, _: CurrentUser):
    """被动推送模式(WorkBuddy/外部工具)接入状态: 是否已配置 token"""
    return R(data={"token_configured": bool(settings.WORKBUDDY_SYNC_TOKEN)})


@router.get("/ingest-modes", response_model=R, summary="投诉数据采集方式开关列表")
def list_ingest_modes(db: DBSession, _: CurrentUser):
    """三种采集方式(主动监测/被动推送/文件导入)的开启状态"""
    from app.services.ingest_mode_service import list_ingest_modes as _list

    return R(data=_list(db))


@router.post("/ingest-modes", response_model=R, summary="设置投诉数据采集方式开关")
def set_ingest_mode(payload: dict, db: DBSession, _: AdminUser):
    """body: {mode: "monitor"|"push"|"import", enabled: bool}"""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    from app.services.ingest_mode_service import update_ingest_mode

    ok = update_ingest_mode(db, payload.get("mode", ""), bool(payload.get("enabled")))
    if not ok:
        raise HTTPException(status_code=400, detail="mode 非法, 允许值: monitor/push/import")
    return R(data={"ok": True})


@router.get("/logs", response_model=R[PageBase], summary="投诉同步日志列表(内部)")
def list_sync_logs(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
):
    stmt = select(ComplaintSyncLog)
    if status:
        stmt = stmt.where(ComplaintSyncLog.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(stmt.order_by(ComplaintSyncLog.id.desc()).offset((page - 1) * page_size).limit(page_size))
    )
    return R(
        data=PageBase(
            items=[
                {
                    "id": log.id,
                    "source_file": log.source_file,
                    "sync_source": log.sync_source,
                    "status": log.status,
                    "external": {
                        "total": log.external_total,
                        "inserted": log.external_inserted,
                        "updated": log.external_updated,
                        "failed": log.external_failed,
                        "matched": log.external_matched,
                    },
                    "card": {
                        "total": log.card_total,
                        "inserted": log.card_inserted,
                        "updated": log.card_updated,
                        "failed": log.card_failed,
                        "matched": log.card_matched,
                    },
                    "error": log.error,
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                }
                for log in items
            ],
            total=total or 0,
            page=page,
            page_size=page_size,
        )
    )

"""workbuddy 手动退款数据推送同步路由

- POST /sync/manual-refunds/push : workbuddy 将《商户号手机退款处理登记》明细以 JSON 推送过来
  (每 20 分钟检查文件, 文件变化时推送)。鉴权: Authorization: Bearer <WORKBUDDY_SYNC_TOKEN>
  (或 X-Sync-Token 请求头)。未配置 token 时返回 503。
- GET /sync/manual-refunds/logs  : 内部查看最近推送日志(需登录)。
"""

import hmac
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DBSession
from app.core.config import settings
from app.models.manual_refund_sync_log import ManualRefundSyncLog
from app.schemas.common import PageBase, R
from app.services.manual_refund_sync import sync_manual_refunds_from_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync/manual-refunds", tags=["数据同步-手动退款"])


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


@router.post("/push", response_model=R, summary="workbuddy 手动退款数据推送")
def push_manual_refunds(
    payload: dict[str, Any],
    db: DBSession,
    token_ok: SyncTokenVerified,
):
    """workbuddy 将文件内容以 JSON 推送入库。

    Body 示例:
    ```json
    {
      "source_file": "商户号手机退款处理登记.xlsx",
      "manual_refunds": [
        {"登记日期": "2026-08-01", "设备号": "67482454178206", "订单号": "WX123456789",
         "商户ID": "1900000001", "订单金额": 1299.00, "登记人": "张三",
         "是否已退款": "是", "退款金额": 1299.00, "退款操作人": "李四", "操作时间": "2026-08-01 15:30:00"}
      ]
    }
    ```
    行内字段名兼容 xlsx 中文列名(见 docs/workbuddy手动退款数据同步接入规范.md)与英文字段名。
    服务会做: 数据清洗(时间质量校验/姓名/金额) → 按 (登记日期,订单号,退款金额) 复合键去重 upsert。
    """
    if not token_ok:
        if not settings.WORKBUDDY_SYNC_TOKEN:
            raise HTTPException(status_code=503, detail="WORKBUDDY_SYNC_TOKEN 未配置, 同步接口未启用")
        raise HTTPException(status_code=401, detail="同步 token 无效")
    # 采集方式总开关: 被动推送关闭则拒绝接收
    from app.services.ingest_mode_service import is_ingest_mode_enabled

    if not is_ingest_mode_enabled(db, "push"):
        raise HTTPException(status_code=503, detail="被动推送采集方式已关闭(可在 数据采集→采集方式开关 中开启)")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    if not isinstance(payload.get("manual_refunds"), list):
        raise HTTPException(status_code=400, detail="manual_refunds 必填且必须为数组")
    source_file = (payload.get("source_file") or "").strip() or None
    try:
        stats = sync_manual_refunds_from_payload(db, payload, source_file=source_file, sync_source="workbuddy")
    except Exception as e:
        logger.exception(f"手动退款推送失败: {e}")
        raise HTTPException(status_code=500, detail=f"同步失败: {e}") from e
    return R(data=stats)


@router.get("/config", response_model=R, summary="手动退款推送接入配置状态(内部)")
def sync_push_config(db: DBSession, _: CurrentUser):
    """被动推送模式(WorkBuddy/外部工具)接入状态: 是否已配置 token"""
    return R(data={"token_configured": bool(settings.WORKBUDDY_SYNC_TOKEN)})


@router.get("/logs", response_model=R[PageBase], summary="手动退款同步日志列表(内部)")
def list_sync_logs(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
):
    stmt = select(ManualRefundSyncLog)
    if status:
        stmt = stmt.where(ManualRefundSyncLog.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(stmt.order_by(ManualRefundSyncLog.id.desc()).offset((page - 1) * page_size).limit(page_size))
    )
    return R(
        data=PageBase(
            items=[
                {
                    "id": log.id,
                    "source_file": log.source_file,
                    "sync_source": log.sync_source,
                    "status": log.status,
                    "total": log.total,
                    "inserted": log.inserted,
                    "updated": log.updated,
                    "failed": log.failed,
                    "warnings": log.warnings,
                    "error": log.error,
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                    "finished_at": log.finished_at.isoformat() if log.finished_at else None,
                }
                for log in items
            ],
            total=total or 0,
            page=page,
            page_size=page_size,
        )
    )

"""统一采集日志查询 API

聚合所有数据采集过程的日志记录，包括：
  - 文件上传 (UploadRecord)
  - BMS 主动同步 (BmsSyncLog)
  - SCRM 主动同步 (ScrmSyncLog)
  - 投诉推送/文档同步 (ComplaintSyncLog)
  - 手动退款推送 (ManualRefundSyncLog)

所有采集过程统一按时间倒序查询，支持按来源/状态/时间范围筛选。
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession
from app.models.bms_sync_log import BmsSyncLog
from app.models.complaint_sync_log import ComplaintSyncLog
from app.models.manual_refund_sync_log import ManualRefundSyncLog
from app.models.scrm_sync_log import ScrmSyncLog
from app.models.upload import UploadRecord
from app.schemas.common import R

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/collection/logs", tags=["数据采集-日志"])


class CollectionLogItem(BaseModel):
    """统一采集日志条目"""

    id: int = Field(..., description="原表记录 ID")
    source: str = Field(..., description="来源: upload/bms/scrm/push")
    source_label: str = Field(..., description="来源显示名: 文件上传/BMS同步/SCRM同步/投诉推送")
    data_type: str | None = Field(None, description="数据类型/数据源代码")
    data_type_label: str | None = Field(None, description="数据类型显示名")
    status: str = Field(..., description="状态: success/failed/running/pending/parsing/imported")
    status_label: str = Field(..., description="状态显示名")
    total_rows: int | None = Field(None, description="总行数")
    inserted_rows: int | None = Field(None, description="新增行数")
    updated_rows: int | None = Field(None, description="更新行数")
    failed_rows: int | None = Field(None, description="失败行数")
    trigger_type: str | None = Field(None, description="触发方式: manual/scheduled/push")
    trigger_label: str | None = Field(None, description="触发方式显示名")
    triggered_by: str | None = Field(None, description="触发者/操作人")
    filename: str | None = Field(None, description="文件名")
    error: str | None = Field(None, description="错误摘要")
    created_at: str = Field(..., description="创建时间")
    finished_at: str | None = Field(None, description="完成时间")


# 来源标签映射
SOURCE_LABELS = {
    "upload": "文件上传",
    "bms": "BMS同步",
    "scrm": "SCRM同步",
    "push": "外部推送",
}

# 数据类型标签映射
DATA_TYPE_LABELS = {
    "refund": "退费单",
    "ticket": "服务工单",
    "wallet": "钱包提现",
    "device_combo": "设备套餐",
    "merchant_refund": "商户号现金流",
    "merchant": "商户",
    "shanmao_refund": "闪猫退款",
    "cs_record": "智能助手记录",
    "external_complaint": "外部投诉",
    "card_complaint": "卡商投诉",
    "manual_refund": "手动退款登记",
    "staff": "人员",
    "scrm_operator": "SCRM客服",
    "scrm_session_private": "SCRM单聊",
    "scrm_session_group": "SCRM群聊",
    "scrm_session_virtual": "SCRM虚拟会话",
}

# 状态标签映射
STATUS_LABELS = {
    "success": "成功",
    "failed": "失败",
    "running": "进行中",
    "pending": "待处理",
    "parsing": "解析中",
    "imported": "已导入",
    "partial": "部分成功",
    "reconciled_mismatch": "对账不符",
}

# 触发方式标签
TRIGGER_LABELS = {
    "manual": "手动",
    "scheduled": "定时",
    "push": "推送",
    "catch_up": "追补",
}


def _make_status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def _make_trigger_label(t: str | None) -> str | None:
    if not t:
        return None
    return TRIGGER_LABELS.get(t, t)


def _format_dt(dt) -> str:
    """格式化时间为 ISO 字符串"""
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)


def _extract_error_detail(error_detail) -> str | None:
    """从 error_detail(JSON) 中提取第一条错误文本, 兼容 dict/str/列表嵌套结构"""
    if not error_detail:
        return None
    if isinstance(error_detail, str):
        return error_detail[:500]
    if isinstance(error_detail, dict):
        errors = error_detail.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, str):
                return first[:500]
            if isinstance(first, dict):
                return str(first.get("message") or first)[:500]
        msg = error_detail.get("message")
        if msg:
            return str(msg)[:500]
    return None


@router.get("", response_model=R[list[CollectionLogItem]], summary="统一采集日志列表")
def list_collection_logs(
    db: DBSession,
    _: CurrentUser,
    source: str | None = Query(None, description="来源筛选: upload/bms/scrm/push"),
    status: str | None = Query(None, description="状态筛选: success/failed/running/pending"),
    data_type: str | None = Query(None, description="数据类型筛选"),
    limit: int = Query(200, ge=1, le=1000, description="返回条数"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """聚合查询所有采集来源的日志，按时间倒序排列，支持分页"""

    results: list[CollectionLogItem] = []

    # ── 1. 文件上传 ──
    if not source or source == "upload":
        stmt = select(UploadRecord).order_by(UploadRecord.id.desc()).limit(limit)
        if data_type:
            stmt = stmt.where(UploadRecord.data_type == data_type)
        if status:
            stmt = stmt.where(UploadRecord.status == status)
        for r in db.scalars(stmt):
            results.append(
                CollectionLogItem(
                    id=r.id,
                    source="upload",
                    source_label="文件上传",
                    data_type=r.data_type,
                    data_type_label=DATA_TYPE_LABELS.get(r.data_type, r.data_type),
                    status=r.status,
                    status_label=_make_status_label(r.status),
                    total_rows=r.total_rows,
                    inserted_rows=r.inserted_rows,
                    updated_rows=r.updated_rows,
                    failed_rows=r.failed_rows,
                    trigger_type="manual",
                    trigger_label="手动",
                    triggered_by=r.uploaded_by,
                    filename=r.filename,
                    error=_extract_error_detail(r.error_detail),
                    created_at=_format_dt(r.created_at),
                    finished_at=_format_dt(r.finished_at),
                )
            )

    # ── 2. BMS 同步 ──
    if not source or source == "bms":
        stmt = select(BmsSyncLog).order_by(BmsSyncLog.id.desc()).limit(limit)
        if data_type:
            stmt = stmt.where(BmsSyncLog.data_type == data_type)
        if status:
            stmt = stmt.where(BmsSyncLog.status == status)
        for r in db.scalars(stmt):
            results.append(
                CollectionLogItem(
                    id=r.id,
                    source="bms",
                    source_label="BMS同步",
                    data_type=r.data_type,
                    data_type_label=DATA_TYPE_LABELS.get(r.data_type, r.data_type),
                    status=r.status,
                    status_label=_make_status_label(r.status),
                    total_rows=r.total_rows,
                    inserted_rows=r.inserted_rows,
                    updated_rows=r.updated_rows,
                    failed_rows=r.failed_rows,
                    trigger_type=r.trigger_type,
                    trigger_label=_make_trigger_label(r.trigger_type),
                    triggered_by=r.triggered_by,
                    filename=r.filename,
                    error=r.error,
                    created_at=_format_dt(r.created_at),
                    finished_at=_format_dt(r.finished_at),
                )
            )

    # ── 3. SCRM 同步 ──
    if not source or source == "scrm":
        stmt = select(ScrmSyncLog).order_by(ScrmSyncLog.id.desc()).limit(limit)
        if data_type:
            stmt = stmt.where(ScrmSyncLog.data_type == data_type)
        if status:
            stmt = stmt.where(ScrmSyncLog.status == status)
        for r in db.scalars(stmt):
            results.append(
                CollectionLogItem(
                    id=r.id,
                    source="scrm",
                    source_label="SCRM同步",
                    data_type=r.data_type,
                    data_type_label=DATA_TYPE_LABELS.get(r.data_type, r.data_type),
                    status=r.status,
                    status_label=_make_status_label(r.status),
                    total_rows=r.api_total_reported,
                    inserted_rows=r.inserted_rows,
                    updated_rows=r.updated_rows,
                    failed_rows=r.failed_rows,
                    trigger_type=r.trigger_type,
                    trigger_label=_make_trigger_label(r.trigger_type),
                    triggered_by=r.triggered_by,
                    filename=None,
                    error=r.error,
                    created_at=_format_dt(r.created_at),
                    finished_at=_format_dt(r.finished_at),
                )
            )

    # ── 4. 投诉推送 ──
    if not source or source == "push":
        stmt = select(ComplaintSyncLog).order_by(ComplaintSyncLog.id.desc()).limit(limit)
        if status:
            stmt = stmt.where(ComplaintSyncLog.status == status)
        for r in db.scalars(stmt):
            # 合并外部+卡商统计
            total = (r.external_total or 0) + (r.card_total or 0)
            inserted = (r.external_inserted or 0) + (r.card_inserted or 0)
            updated = (r.external_updated or 0) + (r.card_updated or 0)
            failed = (r.external_failed or 0) + (r.card_failed or 0)
            results.append(
                CollectionLogItem(
                    id=r.id,
                    source="push",
                    source_label="外部推送",
                    data_type="complaint",
                    data_type_label="投诉数据",
                    status=r.status,
                    status_label=_make_status_label(r.status),
                    total_rows=total,
                    inserted_rows=inserted,
                    updated_rows=updated,
                    failed_rows=failed,
                    trigger_type="push",
                    trigger_label="推送",
                    triggered_by=r.sync_source,
                    filename=r.source_file,
                    error=r.error,
                    created_at=_format_dt(r.created_at),
                    finished_at=_format_dt(r.finished_at),
                )
            )

    # ── 5. 手动退款推送 ──
    if not source or source == "push":
        stmt = select(ManualRefundSyncLog).order_by(ManualRefundSyncLog.id.desc()).limit(limit)
        if status:
            stmt = stmt.where(ManualRefundSyncLog.status == status)
        for r in db.scalars(stmt):
            results.append(
                CollectionLogItem(
                    id=r.id,
                    source="push",
                    source_label="外部推送",
                    data_type="manual_refund",
                    data_type_label="手动退款登记",
                    status=r.status,
                    status_label=_make_status_label(r.status),
                    total_rows=r.total,
                    inserted_rows=r.inserted,
                    updated_rows=r.updated,
                    failed_rows=r.failed,
                    trigger_type="push",
                    trigger_label="推送",
                    triggered_by=r.sync_source,
                    filename=r.source_file,
                    error=r.error,
                    created_at=_format_dt(r.created_at),
                    finished_at=_format_dt(r.finished_at),
                )
            )

    # 按 created_at 时间倒序排序
    results.sort(key=lambda x: x.created_at, reverse=True)

    # 分页
    offset = (page - 1) * page_size
    paged = results[offset : offset + page_size]

    return R(data=paged)


@router.get("/sources", response_model=R, summary="采集日志来源选项")
def list_log_sources():
    """返回可选的来源列表"""
    return R(
        data=[
            {"value": "upload", "label": "文件上传"},
            {"value": "bms", "label": "BMS同步"},
            {"value": "scrm", "label": "SCRM同步"},
            {"value": "push", "label": "投诉推送"},
        ]
    )


@router.get("/data-types", response_model=R, summary="采集日志数据类型选项")
def list_log_data_types():
    """返回可选的类型列表"""
    return R(
        data=[
            {"value": k, "label": v}
            for k, v in sorted(DATA_TYPE_LABELS.items())
        ]
    )

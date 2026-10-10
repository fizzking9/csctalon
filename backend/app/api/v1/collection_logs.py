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
    # ── #42 abc-c: 拒收行与入库前校验(只读告警) ──
    # 语义区分: rejected=未采纳待人工判定(落拒收表); validation=校验告警(行保留)
    rejected_rows: int | None = Field(None, description="拒收行数(未采纳待人工判定)")
    validation_count: int | None = Field(None, description="入库前校验问题条数(只读告警)")
    validation_block: int | None = Field(None, description="校验问题中需优先处理(block)的条数")
    validation_warn: int | None = Field(None, description="校验问题中常规处理(warn)的条数")
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
    "shared_doc": "共享文档采集",
}

# 同步日志 sync_source → 来源归类(#33/#36): shared_doc/tencent_doc 是系统主动采集
# 共享文档(登录态+UI导出), 与 workbuddy 外部 JSON 推送是两类, 不应混标"外部推送"。
_DOC_SYNC_SOURCES = {"shared_doc", "tencent_doc"}


def _classify_sync_source(sync_source: str | None) -> tuple[str, str]:
    """按 sync_source 归类投诉/手动退款同步日志的来源。

    返回 (source, source_label):
      - shared_doc/tencent_doc → ("shared_doc", "共享文档采集")
      - 其余(workbuddy/manual/None) → ("push", "外部推送")
    """
    if (sync_source or "").strip() in _DOC_SYNC_SOURCES:
        return "shared_doc", "共享文档采集"
    return "push", "外部推送"

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
    """从同步日志 error 提取友好摘要, 兼容 dict/str/列表嵌套结构。

    2026-09-08 (#36 完善): 同步日志 error 存的是 result.errors 的 repr 字符串
    (如 "[{'row':0,'level':'warning','message':...}, ...]"), 原实现整段截断展示
    在采集日志 error 列, 一长串 JSON 看着像"大量出错"。
    现解析后收敛:
      - 有 level=error(硬失败) → "N 条错误: <首条message>…; 另 M 条清洗提示"
      - 仅 warning(清洗提示, 行保留) → "M 条清洗提示(数据已保留)"
    """
    if not error_detail:
        return None
    # 解析可能为 list repr 的字符串(如 str(all_errors))
    if isinstance(error_detail, str):
        items = _parse_error_list(error_detail)
        if items is not None:
            return _summarize_errors(items)
        return error_detail[:500]
    if isinstance(error_detail, dict):
        errors = error_detail.get("errors")
        if isinstance(errors, list) and errors:
            return _summarize_errors(errors)
        msg = error_detail.get("message")
        if msg:
            return str(msg)[:500]
        return None
    if isinstance(error_detail, list):
        return _summarize_errors(error_detail)
    return str(error_detail)[:500]


def _parse_error_list(raw: str):
    """尝试把 repr 字符串解析为 [{level,message},...]; 失败返回 None"""
    import ast
    s = raw.strip()
    if not (s.startswith("[") and s.endswith("]")):
        return None
    try:
        parsed = ast.literal_eval(s)
    except (ValueError, SyntaxError):
        return None
    if isinstance(parsed, list):
        return [x for x in parsed if isinstance(x, dict)]
    return None


def _summarize_errors(items: list[dict]) -> str:
    """按 level 收敛: error=硬失败, warning=清洗提示(行保留)"""
    errs = [x for x in items if str(x.get("level")) == "error"]
    warns = [x for x in items if str(x.get("level")) == "warning"]
    parts: list[str] = []
    if errs:
        first = str(errs[0].get("message") or errs[0])[:120]
        parts.append(f"{len(errs)} 条错误: {first}")
    if warns:
        parts.append(f"{len(warns)} 条清洗提示(行已保留)")
    if not parts:
        # 无 level 标记: 按文本取首条
        return str(items[0].get("message") or items[0])[:500] if items else None  # type: ignore[return-value]
    return "; ".join(parts)



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

    # ── 1. 文件上传(仅手动上传) ──
    if not source or source == "upload":
        stmt = select(UploadRecord).order_by(UploadRecord.id.desc()).limit(limit)
        if data_type:
            stmt = stmt.where(UploadRecord.data_type == data_type)
        if status:
            stmt = stmt.where(UploadRecord.status == status)
        for r in db.scalars(stmt):
            # ⚠️ 2026-09-08: BMS 定时同步也会经 _auto_import 建 UploadRecord(source='bms'),
            # 但同批数据已由 BmsSyncLog(BMS同步来源)展示, 这里再列"文件上传"会造成同一
            # 采集动作重复出现两行。故只展示真·手动上传(source=manual/历史null),
            # source='bms' 的自动导入记录跳过(归 BMS同步来源展示)。
            if r.source == "bms":
                continue
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

    # ── 4. 投诉同步(外部推送 / 共享文档采集, 按 sync_source 区分) ──
    if not source or source in ("push", "shared_doc"):
        stmt = select(ComplaintSyncLog).order_by(ComplaintSyncLog.id.desc()).limit(limit)
        if status:
            stmt = stmt.where(ComplaintSyncLog.status == status)
        for r in db.scalars(stmt):
            row_source, row_label = _classify_sync_source(r.sync_source)
            if source and row_source != source:  # 来源筛选(仅当指定了 push/shared_doc)
                continue
            # 合并外部+卡商统计
            total = (r.external_total or 0) + (r.card_total or 0)
            inserted = (r.external_inserted or 0) + (r.card_inserted or 0)
            updated = (r.external_updated or 0) + (r.card_updated or 0)
            failed = (r.external_failed or 0) + (r.card_failed or 0)
            results.append(
                CollectionLogItem(
                    id=r.id,
                    source=row_source,
                    source_label=row_label,
                    data_type="complaint",
                    data_type_label="投诉数据",
                    status=r.status,
                    status_label=_make_status_label(r.status),
                    total_rows=total,
                    inserted_rows=inserted,
                    updated_rows=updated,
                    failed_rows=failed,
                    rejected_rows=r.rejected or 0,
                    validation_count=r.validation_count or 0,
                    validation_block=(r.validation_severity or {}).get("block") or 0,
                    validation_warn=(r.validation_severity or {}).get("warn") or 0,
                    trigger_type="push" if row_source == "push" else "scheduled",
                    trigger_label="推送" if row_source == "push" else "采集",
                    triggered_by=r.sync_source,
                    filename=r.source_file,
                    error=_extract_error_detail(r.error),
                    created_at=_format_dt(r.created_at),
                    finished_at=_format_dt(r.finished_at),
                )
            )

    # ── 5. 手动退款同步(外部推送 / 共享文档采集, 按 sync_source 区分) ──
    if not source or source in ("push", "shared_doc"):
        stmt = select(ManualRefundSyncLog).order_by(ManualRefundSyncLog.id.desc()).limit(limit)
        if status:
            stmt = stmt.where(ManualRefundSyncLog.status == status)
        for r in db.scalars(stmt):
            row_source, row_label = _classify_sync_source(r.sync_source)
            if source and row_source != source:  # 来源筛选(仅当指定了 push/shared_doc)
                continue
            results.append(
                CollectionLogItem(
                    id=r.id,
                    source=row_source,
                    source_label=row_label,
                    data_type="manual_refund",
                    data_type_label="手动退款登记",
                    status=r.status,
                    status_label=_make_status_label(r.status),
                    total_rows=r.total,
                    inserted_rows=r.inserted,
                    updated_rows=r.updated,
                    failed_rows=r.failed,
                    rejected_rows=r.rejected or 0,
                    validation_count=r.validation_count or 0,
                    validation_block=(r.validation_severity or {}).get("block") or 0,
                    validation_warn=(r.validation_severity or {}).get("warn") or 0,
                    trigger_type="push" if row_source == "push" else "scheduled",
                    trigger_label="推送" if row_source == "push" else "采集",
                    triggered_by=r.sync_source,
                    filename=r.source_file,
                    error=_extract_error_detail(r.error),
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
            {"value": "shared_doc", "label": "共享文档采集"},
            {"value": "push", "label": "外部推送"},
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

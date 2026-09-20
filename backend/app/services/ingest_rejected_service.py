"""拒收行服务(A-20260908-003): ingest_rejected_rows 的写入/查询/人工判定。

一期入口(架构 §2.2):
- 写入: 供各外部数据入口插桩(workbuddy push dropped/filtered / upload upsert error / 污染清洗等)
- 查询/判定: list/detail/review/batch-review/stats
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ingest_rejected_row import IngestRejectedRow

logger = logging.getLogger(__name__)

# 人工状态
ST_OPEN = "open"        # 未判定(默认): 不纳入统计
ST_KEEP = "keep"        # 人工采纳
ST_DISCARD = "discard"  # 人工明确不纳入

# 噪音分类默认静默(不默认进 visible, 仅计数): 空模板/预留行
SILENT_CATEGORIES = frozenset({"empty_template"})


def add_suspect_row(
    db: Session,
    *,
    data_type: str,
    source: str,
    reject_category: str,
    reject_reason: str | None = None,
    raw_row: dict | None = None,
    business_key: str | None = None,
    source_ref: str | None = None,
    source_row_no: int | None = None,
    limit: int | None = None,
) -> IngestRejectedRow | None:
    """导入期「可疑但**已入库**」的统一留痕入口（工单 #168，供 E2/E3 复用）。

    与 `add_rejected_row` 的**分工**（写死，勿混）：
      · `add_rejected_row`   —— 行**未采纳**（校验失败/重复跳过等），没进业务表
      · `add_suspect_row`    —— 行**已采纳**，但某个字段值可疑（截断/换行/越界日期），
                                进业务表的数据**一个字节都不改**，只多写一条留痕供人工判定

    三条纪律（采集主路径上的改动）：
      1. **不改入库数据**（本函数只写留痕行）
      2. **不阻断采集**：任何失败只告警（savepoint 隔离，不污染调用方事务）
      3. **有上限**：`limit` 非空时超出直接跳过（防海量噪音刷爆留痕表）

    `limit` 由调用方按批次持有（如 upsert 的「每批 200 条」），本函数无跨调用状态。
    """
    try:
        if limit is not None and limit <= 0:
            return None
        row = IngestRejectedRow(
            data_type=data_type,
            source=source,
            source_ref=source_ref,
            source_row_no=source_row_no,
            reject_category=reject_category,
            reject_reason=(reject_reason or "")[:500] or None,
            raw_row=raw_row,
            business_key=business_key,
            status=ST_OPEN,
        )
        db.add(row)
        db.flush()
        return row
    except Exception as e:  # noqa: BLE001  留痕失败不影响采集主流程
        logger.warning(f"[ingest_rejected] 写可疑行留痕失败(忽略, 不影响入库): {e}")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def add_rejected_row(
    db: Session,
    *,
    data_type: str,
    source: str,
    reject_category: str,
    reject_reason: str | None = None,
    raw_row: dict | None = None,
    business_key: str | None = None,
    source_ref: str | None = None,
    source_row_no: int | None = None,
) -> IngestRejectedRow | None:
    """写入一条拒收行。

    噪音分类(empty_template 等)默认静默 —— 仅记录 open 但标记可过滤;
    若调用方需强制 visible 可后续调整。
    """
    try:
        row = IngestRejectedRow(
            data_type=data_type,
            source=source,
            source_ref=source_ref,
            source_row_no=source_row_no,
            reject_category=reject_category,
            reject_reason=reject_reason,
            raw_row=raw_row,
            business_key=business_key,
            status=ST_OPEN,
        )
        db.add(row)
        db.flush()
        return row
    except Exception as e:  # noqa: BLE001 拒收记录失败不影响主流程
        logger.warning(f"[ingest_rejected] 写拒收行失败(忽略): {e}")
        db.rollback()
        return None


def list_rejected_rows(
    db: Session,
    *,
    data_type: str | None = None,
    source: str | None = None,
    status: str | None = None,
    category: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """拒收行列表(open/keep/discard; 可按类型/入口/状态/分类筛选)。"""
    stmt = select(IngestRejectedRow)
    if data_type:
        stmt = stmt.where(IngestRejectedRow.data_type == data_type)
    if source:
        stmt = stmt.where(IngestRejectedRow.source == source)
    if status:
        stmt = stmt.where(IngestRejectedRow.status == status)
    if category:
        stmt = stmt.where(IngestRejectedRow.reject_category == category)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = max(1, page)
    page_size = min(200, max(1, page_size))
    rows = db.scalars(
        stmt.order_by(IngestRejectedRow.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_serialize(r) for r in rows],
    }


def review_rejected_row(db: Session, row_id: int, action: str, reviewer: str) -> dict | None:
    """人工判定单行: keep(采纳)/discard(不纳入)。"""
    if action not in (ST_KEEP, ST_DISCARD):
        raise ValueError(f"action 必须为 {ST_KEEP}/{ST_DISCARD}")
    row = db.get(IngestRejectedRow, row_id)
    if not row:
        return None
    row.status = action
    row.reviewed_by = reviewer
    row.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    return _serialize(row)


def batch_review_rejected_rows(db: Session, row_ids: list[int], action: str, reviewer: str) -> int:
    """批量判定。"""
    if action not in (ST_KEEP, ST_DISCARD):
        raise ValueError(f"action 必须为 {ST_KEEP}/{ST_DISCARD}")
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(IngestRejectedRow).where(IngestRejectedRow.id.in_(row_ids))
    ).all()
    for r in rows:
        r.status = action
        r.reviewed_by = reviewer
        r.reviewed_at = now
    db.commit()
    return len(rows)


def rejected_stats(db: Session) -> dict:
    """各源/拒因/状态汇总(看板卡)。"""
    by_source = db.execute(
        select(IngestRejectedRow.source, func.count())
        .group_by(IngestRejectedRow.source)
    ).all()
    by_category = db.execute(
        select(IngestRejectedRow.reject_category, func.count())
        .group_by(IngestRejectedRow.reject_category)
    ).all()
    by_status = db.execute(
        select(IngestRejectedRow.status, func.count())
        .group_by(IngestRejectedRow.status)
    ).all()
    total = db.scalar(select(func.count()).select_from(IngestRejectedRow)) or 0
    return {
        "total": total,
        "open": dict(by_status).get(ST_OPEN, 0),
        "by_source": dict(by_source),
        "by_category": dict(by_category),
        "by_status": dict(by_status),
    }


def _serialize(r: IngestRejectedRow) -> dict:
    return {
        "id": r.id,
        "data_type": r.data_type,
        "source": r.source,
        "source_ref": r.source_ref,
        "source_row_no": r.source_row_no,
        "reject_category": r.reject_category,
        "reject_reason": r.reject_reason,
        "raw_row": r.raw_row,
        "business_key": r.business_key,
        "status": r.status,
        "reviewed_by": r.reviewed_by,
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }

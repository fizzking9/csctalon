"""外部数据统一质量治理 - 拒收行模型(A-20260908-003 LOCKED)

承接所有外部数据入口"从未进业务表"的行(解析失败/校验不过/去重跳过/污染/删除类),
按行展示 + 人工判定(open/keep/discard)。未判定(open)/discard → 不纳入统计。
与"入库打标"(quality_status pending/ok/dup_residual, 第二层)分层, 本表管"从未入库"行。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class IngestRejectedRow(TimestampMixin, Base):
    """拒收行 - 外部数据未采纳行按行保留+人工判定"""

    __tablename__ = "ingest_rejected_rows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    data_type: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False, comment="目标业务表类型(refund/external_complaint/manual_refund/...)"
    )
    source: Mapped[str] = mapped_column(
        String(32), index=True, nullable=False,
        comment="入口标识: bms/shared_doc/tencent_doc/workbuddy_pull/upload/scrm",
    )
    source_ref: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="溯源: upload_record_id / sync_log_id / doc_sync_source_id"
    )
    source_row_no: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="源文件/批次内行号")
    reject_category: Mapped[str] = mapped_column(
        String(32), index=True, nullable=False,
        comment="拒因分类: parse_fail/validate_fail/dup_skip/polluted/empty_template/reconcile_away",
    )
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True, comment="人读原因描述")
    raw_row: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="原始整行(列名→值)")
    business_key: Mapped[str | None] = mapped_column(
        String(128), index=True, nullable=True, comment="业务键(RF单号/订单号/手机等, 尽力识别)"
    )
    # 人工状态: open(未判定,默认)/keep(采纳)/discard(明确不纳入)
    status: Mapped[str] = mapped_column(
        String(16), index=True, default="open", nullable=False, comment="人工状态: open/keep/discard"
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="判定人")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="判定时间")

"""上传记录与数据关联关系模型"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class UploadRecord(IDMixin, TimestampMixin, Base):
    """数据上传批次记录"""

    __tablename__ = "upload_records"

    data_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False, comment="数据类型:refund/wallet/ticket/merchant_refund/merchant/device")
    """refund=退费单, wallet=钱包提现, ticket=退款工单, merchant_refund=商户号现金流, merchant=商户, device=设备"""
    filename: Mapped[str] = mapped_column(String(255), nullable=False, comment="原文件名")
    stored_path: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="归档相对路径 yyyy-MM/filename（找文件用）")
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="文件大小(字节)")
    sheet_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="XLSX的sheet名")
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, comment="pending/parsing/imported/failed")
    """pending=待处理, parsing=解析中, imported=已导入, failed=失败"""
    total_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="总行数")
    inserted_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="新增行数")
    updated_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="更新行数")
    failed_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="失败行数")
    matched_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="已关联行数")
    error_detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, comment="错误明细")
    uploaded_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="上传人")
    source: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True, comment="导入来源:manual=手动上传, bms=BMS同步;历史记录为null")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="完成时间")


class DataMatchRelation(IDMixin, TimestampMixin, Base):
    """跨表数据关联关系

    记录业务表之间的匹配结果(如退费单↔工单、退费单↔钱包提现、退费单↔商户退款)
    """

    __tablename__ = "data_match_relations"

    src_table: Mapped[str] = mapped_column(String(32), index=True, nullable=False, comment="源表:refund/ticket/wallet/merchant_refund")
    src_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, comment="源表主键值")
    dst_table: Mapped[str] = mapped_column(String(32), index=True, nullable=False, comment="目标表")
    dst_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, comment="目标表主键值")
    match_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="匹配字段:order_no/device_sn/refund_no_in_remark")
    confidence: Mapped[float] = mapped_column(Integer, default=100, nullable=False, comment="置信度0-100")
    matched_by: Mapped[str] = mapped_column(String(32), default="auto", nullable=False, comment="auto自动/manual人工")
    status: Mapped[str] = mapped_column(String(32), default="matched", nullable=False, comment="matched关联/pending待人工确认/invalid失效")

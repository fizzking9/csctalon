"""发票 / 报销单模型(规划设计 §4.5)

客服快递费/开支费用: 上传电子发票(二维码/PDF/图片)→识别→记录→汇总报销→导出。
不关联业务对象(纯报销流水)。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class ExpenseInvoice(IDMixin, TimestampMixin, Base):
    """发票记录"""

    __tablename__ = "expense_invoices"

    invoice_code: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="发票代码")
    invoice_no: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="发票号码")
    invoice_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="全电/纸质/普票/专票")
    seller_name: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="销方")
    buyer_name: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="购方")
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="金额")
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="税额")
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="价税合计")
    invoice_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="开票日期")
    file_url: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="附件路径")
    file_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="qr/pdf/image")
    ocr_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="识别结果原始JSON")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True, comment="识别置信度")
    status: Mapped[str] = mapped_column(
        String(32), default="待识别", nullable=False, index=True, comment="待识别/已识别/待核对/已入账/已报销"
    )
    submitter_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, comment="提交人")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")


class Reimbursement(IDMixin, TimestampMixin, Base):
    """报销单"""

    __tablename__ = "reimbursements"

    batch_no: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False, comment="报销单号")
    title: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="标题")
    submitter_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, comment="提交人")
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="总金额")
    invoice_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="发票张数")
    status: Mapped[str] = mapped_column(
        String(32), default="草稿", nullable=False, index=True, comment="草稿/待审核/已通过/已驳回/已报销"
    )
    approved_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, comment="审核人")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="审核时间")
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="导出时间")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")


class ReimbursementItem(IDMixin, TimestampMixin, Base):
    """报销明细"""

    __tablename__ = "reimbursement_items"

    reimbursement_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reimbursements.id", ondelete="CASCADE"), index=True, nullable=False, comment="报销单"
    )
    # 2026-08-20 审计 H-2/API-02 修复: invoice_id 唯一, 防同一发票进入多张报销单(重复报销)
    invoice_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("expense_invoices.id"), unique=True, nullable=False, comment="发票(唯一, 防重复报销)"
    )
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="金额")

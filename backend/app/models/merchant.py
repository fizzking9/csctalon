"""商户主数据模型 - 来自'各商户号退款数据明细表-商户名称'

B3-1 (#246, A-20261007-002 §3.3) 演进:
- merchant_id(商户号) 升级为业务键, 部分唯一索引(空值允许);
- 代理键沿用现有 id, 不另造 merchant_pk;
- 新增 device_merchant_links: 设备→商户号 SCD Type2 建链表。
  devices 不冗余 merchant_pk 外键(归属会变、要留历史);
  当前归属由 is_current 链路解析。
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class Merchant(IDMixin, TimestampMixin, Base):
    """商户主数据(子商户/主商户/法人/管理员)"""

    __tablename__ = "merchants"

    sub_name: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False, comment="子商户名称")
    """子商户名称 - 与退费单.商户名称关联"""
    main_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="主商户名称")
    merchant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="商户号id(业务键, 部分唯一)")
    legal_person: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="法人")
    super_admin: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="超级管理员")
    status: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="状态:正常/停用/无数据")

    __table_args__ = (
        # 商户号业务键唯一; 空值不参与唯一(部分索引)
        Index(
            "ix_merchants_merchant_id_unique",
            "merchant_id",
            unique=True,
            postgresql_where=text("merchant_id IS NOT NULL"),
        ),
    )


class DeviceMerchantLink(IDMixin, TimestampMixin, Base):
    """设备→商户号 SCD Type2 建链 (#246 B3-1, A-20261007-002 §3.3)

    建链门控: 机匹先出匹配率报告, 低置信不建链; raw_merchant_name 永久保留
    原始文本快照可回溯; 不覆盖旧值、全程可回滚。
    """

    __tablename__ = "device_merchant_links"

    device_sn: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.sn"), nullable=False, index=True, comment="设备SN"
    )
    merchant_pk: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("merchants.id"), nullable=False, index=True, comment="商户代理键(merchants.id)"
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, comment="生效日期")
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, comment="失效日期(None=至今)")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True, comment="是否当前归属")
    confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True, comment="机匹置信度(非金额, 人工建链为空)"
    )
    match_source: Mapped[str] = mapped_column(String(16), nullable=False, comment="来源: manual/xlsx/auto")
    raw_merchant_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="建链时原始商户名文本快照"
    )
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True, comment="变更原因")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="建链操作人")

    __table_args__ = (
        # 同一时刻一台设备仅一条当前归属(部分唯一)
        Index(
            "ix_dml_device_current",
            "device_sn",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

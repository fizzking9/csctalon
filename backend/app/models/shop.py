"""店铺 / 企业主数据模型 (#246 B3-1, A-20261007-002 §3.4)

B5 架构提前至 B3-1 落地:
- enterprises(企业)
- shops(店铺) + 父子归属企业
- shop_enterprise_history 店铺父子 SCD(企业可能调整门店归属)
- device_shop_links 设备→店铺 SCD 建链(通路② 归属, 以共享表格/人工为主)
"""

from datetime import date

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class Enterprise(IDMixin, TimestampMixin, Base):
    """企业主数据"""

    __tablename__ = "enterprises"

    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="企业名称")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", comment="active/disabled")
    raw_source: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="种子来源: return_tracking.shop / external_complaint.xxx / xlsx_import"
    )


class Shop(IDMixin, TimestampMixin, Base):
    """店铺主数据"""

    __tablename__ = "shops"

    shop_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="店铺名称")
    enterprise_pk: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("enterprises.id"), nullable=True, index=True, comment="所属企业(可空, 归属变更走 SCD)"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", comment="active/disabled")
    raw_source: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="种子来源: return_tracking.shop / external_complaint.complaint_shop / xlsx_import"
    )


class ShopEnterpriseHistory(IDMixin, TimestampMixin, Base):
    """店铺↔企业父子 SCD Type2 (#246 B3-1)

    企业可能调整门店归属; 不覆盖旧值, 打标不删。
    """

    __tablename__ = "shop_enterprise_history"

    shop_pk: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enterprise_pk: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("enterprises.id"), nullable=False, index=True
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, comment="生效日期")
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, comment="失效日期(None=至今)")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True, comment="是否当前归属")
    source: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="来源: manual/xlsx_import")
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True, comment="变更原因")

    __table_args__ = (
        Index(
            "ix_seh_shop_current",
            "shop_pk",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )


class DeviceShopLink(IDMixin, TimestampMixin, Base):
    """设备→店铺 SCD Type2 建链 (#246 B3-1)

    通路② 归属; 录入以共享表格/人工校对为主, 不做自动推断硬绑定。
    """

    __tablename__ = "device_shop_links"

    device_sn: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.sn"), nullable=False, index=True, comment="设备SN"
    )
    shop_pk: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shops.id"), nullable=False, index=True, comment="店铺代理键"
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, comment="生效日期")
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, comment="失效日期(None=至今)")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True, comment="是否当前归属")
    match_source: Mapped[str] = mapped_column(String(16), nullable=False, comment="来源: manual/xlsx_import")
    raw_source_text: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="建链时原始来源文本快照"
    )
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True, comment="变更原因")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="建链操作人")

    __table_args__ = (
        Index(
            "ix_dsl_device_current",
            "device_sn",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

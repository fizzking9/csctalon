"""套餐归一字典模型 (#247 B3-2, A-20261007-002 §3.5)

治理目标: 消除"套餐改名即拆散口径"的统计脆弱性。
- combo_dict: 归一套餐主字典(combo_code 稳定键)。
- combo_name_alias: 原始名→归一码映射, SCD 带生效期;
  别名键含 merchant_pk(可空=全局), 允许同名异实(不同商户/时期指向不同套餐)。
口径切换: 按套餐聚合处 join 别名表取归一码; 未命中回落原始名, 不回改历史数字。
"""

from datetime import date

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class ComboDict(TimestampMixin, Base):
    """归一套餐字典"""

    __tablename__ = "combo_dict"

    combo_code: Mapped[str] = mapped_column(String(64), primary_key=True, comment="归一套餐码(稳定键)")
    canonical_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="归一标准名")
    package_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="套餐类型")
    traffic_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="流量类型")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", comment="active/disabled")


class ComboNameAlias(IDMixin, TimestampMixin, Base):
    """原始套餐名 → 归一码 映射(SCD, 含商户与生效期)"""

    __tablename__ = "combo_name_alias"

    combo_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("combo_dict.combo_code"), nullable=False, index=True, comment="归一套餐码"
    )
    raw_package_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="原始套餐名(事实表文本)")
    merchant_pk: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("merchants.id"), nullable=True, index=True,
        comment="限定商户(None=全局别名); 同名异实在不同商户可指向不同套餐",
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, comment="生效日期")
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, comment="失效日期(None=至今)")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True, comment="是否当前生效")
    source: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="来源: manual/xlsx_import")

    __table_args__ = (
        # 同一 原始名+商户 同一时刻仅一条当前映射(部分唯一);
        # NULLS NOT DISTINCT 使全局别名(merchant_pk NULL)同样受唯一约束
        Index(
            "ix_cna_raw_merchant_current",
            "raw_package_name", "merchant_pk",
            unique=True,
            postgresql_where=text("is_current"),
            postgresql_nulls_not_distinct=True,
        ),
    )

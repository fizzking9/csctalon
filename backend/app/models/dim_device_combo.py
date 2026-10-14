"""设备-套餐干净维度表 (SCD Type 2, #320 P1)

设计说明（Design Rationale）:
- 目的(替谁/答何决策): 替退款/投诉两侧的「套餐维度」分析提供**单一时点口径真身**,
  消除两侧各自实现三源回落导致的口径漂移(#311/#313 事故链)。
- 逻辑(为何这样组织): SCD Type 2 记录每台设备套餐随时间的变化区间, 两侧按
  「事件时点 ∈ [valid_from, valid_to)」JOIN 取当时套餐, 语义统一为「事件发生时
  设备所持套餐」, 而非退款侧用 refunds.package_name、投诉侧用设备当前套餐的漂移口径。
- 口径: 构建时合并 bms_orders(combo_name, create_date) + device_combos(package_name,
  created_at_raw) 两路事件, 过滤工厂质检专用套餐, 连续相同套餐合并为一个区间,
  最新区间 valid_to=NULL / is_current=true。
- 取舍(不做什么及原因): ① 不在此表存金额/流量等事实(那是 device_combos 宽表职责);
  ② 不存归一码(套餐归一属 combo_dict 维表层, 保持本层只做"原始套餐名时点快照");
  ③ 质检套餐在构建期过滤而非查询期过滤(维度即"干净", 查询侧零判断成本)。
- 工单: #320 · 前置: #319 (ods 分层落地)
- C4 登记: docs/standards/数据库表结构说明.md §四(SCD Type 2 历史追溯)
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class DimDeviceCombo(IDMixin, TimestampMixin, Base):
    """设备-套餐 SCD2 干净维度 (#320)

    每台设备在任一时点有且仅有一行生效记录 (is_current=true 或 valid_from <= t < valid_to)。
    质检套餐在构建期已过滤, 查询侧无需再判。
    """

    __tablename__ = "dim_device_combo"

    device_sn: Mapped[str] = mapped_column(String(64), nullable=False, comment="设备 SN")
    package_name: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="套餐名(已过滤工厂质检)"
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="来源: bms_order / device_combo"
    )
    order_no: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="来源单号(可空, 追溯用)"
    )
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="区间起(含)"
    )
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="区间止(不含); NULL=当前生效"
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="当前生效标记"
    )

    __table_args__ = (
        UniqueConstraint("device_sn", "valid_from", name="uq_dim_device_combo_sn_from"),
        Index("ix_dim_device_combo_sn_current", "device_sn", "is_current"),
        Index("ix_dim_device_combo_sn_range", "device_sn", "valid_from", "valid_to"),
    )

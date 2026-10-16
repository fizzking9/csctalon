"""E 路企业商户维度表 (#332 退款五路数据标准层)

设计说明（Design Rationale）:
- 目的(替谁/答何决策): E 路(企微商户号退款水单)携带的是**企业真名维度**
  (merchant_id=微信商户号, merchant_name=企业名), 把它沉淀为 SN→企业商户的
  时间感知维表, 补齐 B 路等缺失的商户维度; 与 A 路 refunds.merchant_name
  (运营标签, 不同维度)并存, 互不覆盖。
- 逻辑(为何这样组织):
  - dim_merchant_group: merchant_id → 集团 的**人工维护**映射, 作业不做自动推断
    (一期仅云客猫系 3 个商户号, 业务 2026-10-16 确认归并);
  - dim_device_merchant: 由 E 路全量重建的 SCD 风格分段, 同 SN 按退款时间排序做
    商户变化分段(末段 valid_to=NULL); 同时段多商户标 overlap, 用设备档案
    devices.merchant_name 只读仲裁; 全重复位假 SN(如全 0)标 fake 且不产生分段。
- 口径: docs/standards/退款五路数据标准与采纳规则.md; 构建作业在
  app/services/governance/dim_device_merchant_service.py(T5)。
- 取舍: ① 不做企业归并自动算法, 只接受人工映射; ② 不回写任何 ods 原始表;
  ③ 2026 年前历史覆盖率低是已知边界, 消费侧对 NULL 企业维度兜底展示。
- 工单: #332
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin


class DimMerchantGroup(Base):
    """`public.dim_merchant_group` — 商户号 → 集团 人工映射表 (仅人工/迁移写入)。"""

    __tablename__ = "dim_merchant_group"

    merchant_id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="微信商户号(E 路 merchant_id)")
    merchant_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="商户企业名(映射确认时快照)")
    group_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="集团名(消费侧归并口径)")
    mapping_basis: Mapped[str] = mapped_column(
        String(64), nullable=False,
        comment="映射依据: 如 business_confirmed_20261016(业务确认日期)",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False, comment="最后维护时间"
    )
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="最后维护人")


class DimDeviceMerchant(IDMixin, Base):
    """`public.dim_device_merchant` — 设备 SN → 企业商户 时间分段 (E 路全量重建)。

    消费: 按事件时间 t 取 valid_from <= t < valid_to(末段 valid_to IS NULL)
    的最近分段; conflict_flag='overlap' 表示该 SN 在段内同时段出现多商户,
    由消费侧结合设备档案 merchant_name/集团仲裁, 无法仲裁时按冲突标记兜底展示。
    fake SN(全重复位, 如 00000000000000)不产出分段, 仅计入作业统计。
    """

    __tablename__ = "dim_device_merchant"
    __table_args__ = (
        UniqueConstraint("device_sn", "valid_from", name="uq_dim_device_merchant_sn_from"),
        Index("ix_dim_device_merchant_sn_range", "device_sn", "valid_to"),
        Index("ix_dim_device_merchant_conflict", "conflict_flag"),
    )

    device_sn: Mapped[str] = mapped_column(String(64), nullable=False, comment="设备 SN")
    merchant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="微信商户号")
    merchant_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="商户企业名")
    merchant_group: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="集团名(无人工映射时=merchant_name)"
    )
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="分段起(段首退款时间, 含)"
    )
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="分段止(下段 valid_from, 不含); NULL=末段"
    )
    conflict_flag: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'none'"),
        comment="冲突标记: none=正常 / overlap=同时段多商户待仲裁(fake 不入表)",
    )

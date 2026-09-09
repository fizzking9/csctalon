"""质量问题治理处置规则模型(A-20260909-001 LOCKED)

"治一次复用"核心: 人工对质量问题行做处置(忽略/回填/修正)时, 把
「检测特征 + 处置动作 + 自动级别」沉淀为一条规则; 后续同类行命中规则后
auto(直接执行)或 suggest(带建议待人工确认)治理, 实现同类问题不再重复人工处理。

与 DataQualityRule(检测规则, L1 判定"该不该标问题")分层:
本表是"处置规则"(L2/L3, 标了之后怎么治), 职责不同。

表: quality_disposition_rules
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class QualityDispositionRule(TimestampMixin, Base):
    """质量处置规则 - 一次治理沉淀, 后续同类自动/半自动复用"""

    __tablename__ = "quality_disposition_rules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    table_code: Mapped[str] = mapped_column(
        String(32), index=True, nullable=False,
        comment="作用表: refund/wallet/shanmao/manual_refund/ticket",
    )
    # 触发问题分类(与 quality_note 分类对应)
    trigger_category: Mapped[str] = mapped_column(
        String(32), index=True, nullable=False,
        comment="触发问题分类: time_future/time_past/time_reversed/enum_dirty/"
                "amount_invalid/missing_field/e_mismatch/residual/dup/ad_overlap_dup",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="规则名(人读)")
    # 特征匹配条件(表字段级 jsonb): 如 {"order_no_prefix":"WM","reg_year_future":true}
    match_pattern: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment="特征匹配条件(表字段级, jsonb)"
    )
    # 处置动作: fix_field / ignore / backfill_from_e / confirm_ok
    disposition: Mapped[str] = mapped_column(
        String(24), index=True, nullable=False, comment="处置动作: fix_field/ignore/backfill_from_e/confirm_ok"
    )
    disposition_params: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment="动作参数: 如 fix_field 的 {field, mode}; backfill_from_e 的匹配键"
    )
    # 自动级别: auto(直接执行) / suggest(带建议待确认)
    auto_level: Mapped[str] = mapped_column(
        String(12), index=True, default="suggest", nullable=False, comment="自动级别: auto/suggest"
    )
    confidence: Mapped[int] = mapped_column(
        Integer, default=60, nullable=False, comment="置信度 0-100(命中次数越多越高)"
    )
    hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="命中计数(学习信号)")
    last_hit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最后命中时间"
    )
    # 溯源: 由哪条人工处置沉淀而来
    source_rule_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="由哪条人工处置沉淀而来(溯源)"
    )
    source_ref: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="沉淀来源(如 manual_refund:id=123 处置时生成)"
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True, comment="规则说明")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人")
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="最后更新人")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否启用")

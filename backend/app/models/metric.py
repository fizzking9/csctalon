"""运营指标注册表 - 指标元数据/三级阈值参数/每日快照

指标阈值化监测的核心元数据:
- MetricRegistry: 指标定义(口径/方向/周期/依赖数据源), 计算函数在代码层注册(metric_registry_service)
- MetricThreshold: 多级阈值参数(优秀/预警/告警线), 支持生效区间留痕
- MetricDailySnapshot: 指标每日求值快照(回溯"为什么触发/持续多久")
- MetricDictionary: 指标字典(#257 B4-1) —— 口径单点登记(语义/公式/来源表),
  与 MetricRegistry 职责不同: registry 管预警水位, dictionary 管"这个数字怎么算的"。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class MetricRegistry(IDMixin, TimestampMixin, Base):
    """指标注册表(元数据; 计算函数在代码层注册)"""

    __tablename__ = "metric_registry"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True, comment="指标code, 与代码注册对应")
    name_cn: Mapped[str] = mapped_column(String(128), nullable=False, comment="指标名")
    direction: Mapped[str] = mapped_column(String(16), nullable=False, default="higher_better", comment="方向:higher_better/lower_better")
    period: Mapped[str] = mapped_column(String(8), nullable=False, default="day", comment="周期:day/week/month")
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="单位:%/笔/秒/个")
    data_sources: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="依赖数据源(供健康检查)")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="口径说明")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否参与预警")


class MetricThreshold(IDMixin, TimestampMixin, Base):
    """指标阈值参数(四级水位: 优秀/达标/预警/告警, 支持历史调参留痕)"""

    __tablename__ = "metric_thresholds"

    metric_code: Mapped[str] = mapped_column(String(64), ForeignKey("metric_registry.code"), nullable=False, index=True, comment="指标code")
    excellent_value: Mapped[float | None] = mapped_column(Float, nullable=True, comment="优秀线(可选, 不配则无优秀状态)")
    warning_value: Mapped[float] = mapped_column(Float, nullable=False, comment="预警线")
    critical_value: Mapped[float] = mapped_column(Float, nullable=False, comment="告警线")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="生效起始")
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="生效结束(关闭历史版本)")
    updated_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, comment="修改人")


class MetricDailySnapshot(IDMixin, TimestampMixin, Base):
    """指标每日求值快照(回溯依据)"""

    __tablename__ = "metric_daily_snapshots"
    __table_args__ = {"comment": "指标每日求值快照"}

    metric_code: Mapped[str] = mapped_column(String(64), ForeignKey("metric_registry.code"), nullable=False, index=True, comment="指标code")
    value: Mapped[float | None] = mapped_column(Float, nullable=True, comment="指标值")
    period_date: Mapped[str] = mapped_column(String(16), nullable=False, comment="统计周期(YYYY-MM-DD, 北京时间)")
    level: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="级别:excellent/green/warning/critical/data_missing")
    data_ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="数据是否完整(防假告警)")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True, comment="求值明细")


class MetricDictionary(TimestampMixin, Base):
    """指标字典(#257 B4-1): 口径单点登记 —— 回答"这个数字怎么算的"

    与 MetricRegistry 的分工:
    - MetricRegistry: 预警水位引擎的元数据(方向/周期/阈值), 计算函数代码层注册
    - MetricDictionary: 指标语义的**单点登记处**(含什么/排什么/时点/公式/来源表),
      看板卡片 ⓘ 说明与新人交接以此为唯一口径来源(渐进迁移: 谁改动、谁挂接)

    code 为自然主键(如 reception.session_count); name_cn 全站唯一(同 AGENTS 数据源命名纪律)。
    """

    __tablename__ = "metric_dictionary"
    __table_args__ = {"comment": "指标字典: 口径单点登记(#257 B4-1)"}

    code: Mapped[str] = mapped_column(String(64), primary_key=True, comment="指标code(域.名, 如 reception.session_count)")
    name_cn: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, comment="显示名(全站唯一)")
    definition: Mapped[str] = mapped_column(Text, nullable=False, comment="口径语义: 含什么/排什么/时点")
    formula: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,
        comment="结构化公式: {source, aggregation, filter, window}",
    )
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="单位: 个/次/人/笔/元/%/值")
    category: Mapped[str] = mapped_column(String(16), nullable=False, index=True, comment="分类: reception/refund/complaint/cash/performance")
    source_tables: Mapped[list | None] = mapped_column(JSONB, nullable=True, comment="来源表清单(供下钻/健康检查)")
    owner: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="口径负责人")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否现行口径(停用不删, 留历史引用可查)")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注(变更背景/工单号)")

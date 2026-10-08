"""夜间投诉风险预警配置模型 - 评分权重/回看窗口/阈值/等级线 DB 可配

背景(#276, 排期 O-5 / S-P2-5):
  complaint_risk_service v1(#250) 把权重/窗口/阈值硬编码为模块常量, 调参要发版。
  本表把这 13 个运营参数 DB 化, 改库即生效(服务层 60s 缓存), 无需重启。

设计(复用 scheduler_settings 单行表模式):
- **单行表**(id 恒为 1): 避免"多行配置哪行生效"的歧义; 缺失自动补默认行。
- 默认值 = v1 拍板值(与 #250 上线口径一致, 迁移灌默认行保证行为不变)。
- 取舍:
  * 只配置化「权重/窗口/阈值/等级线」; 评分阶梯(40/70/100 等曲线形状)
    保持代码常量 —— 改阶梯=改算法, 属发版行为, 不做自助调整。
  * complaint_same_source 的 SIM_THRESHOLD/DAY_WINDOW **不**入本表:
    那是 #206 拍板判重口径(改动需重跑判重 + 铁律#10 人工审核),
    自助可改反而削弱口径权威性。
  * 不做管理界面/API: 低频改参, 直接改库 + DB CheckConstraint 兜底,
    60s 缓存内生效; 如需界面另开工单。
"""

from decimal import Decimal

from sqlalchemy import CheckConstraint, Float, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin

# 单行表固定主键
SINGLETON_ID = 1

# ── 默认值 = #250 v1 拍板值(迁移灌行值, 保证升级前后评分结果不变) ──
DEFAULT_WEIGHT_RECHARGE = 25   # 充值多: 高价值用户升级损失大
DEFAULT_WEIGHT_LOW_FLOW = 25   # 流量少: 疑似限速/信号差
DEFAULT_WEIGHT_CONTACT = 20    # 愿沟通: 有沟通意愿, 不处理易升级
DEFAULT_WEIGHT_REPEAT = 30     # 同题重复: 历史未根治是升级最强信号

DEFAULT_RECHARGE_DAYS = 90     # 充值回看窗口(天)
DEFAULT_CONTACT_DAYS = 30      # 接待回看窗口(天)
DEFAULT_COMPLAINT_DAYS = 90    # 投诉工单回看窗口(天)
DEFAULT_EXTERNAL_DAYS = 180    # 外部投诉回看窗口(天)

DEFAULT_LOW_FLOW_GB = 5.0                  # 流量少阈值(GB, 最近完整月; 非金额, Float)
DEFAULT_RECHARGE_HIGH_AMOUNT = Decimal("500")  # 高价值充值金额阈值(元, Decimal)

DEFAULT_LEVEL_CRITICAL = 70    # 总分≥70 → critical
DEFAULT_LEVEL_HIGH = 45        # 总分≥45 → high
DEFAULT_LEVEL_MEDIUM = 25      # 总分≥25 → medium; 否则 low

# 校验边界(与 DB CheckConstraint 共用, 单一事实来源)
MIN_WEIGHT, MAX_WEIGHT = 0, 100
MIN_DAYS, MAX_DAYS = 1, 365
MIN_LEVEL, MAX_LEVEL = 0, 100


class RiskAlertSettings(IDMixin, TimestampMixin, Base):
    """夜间投诉风险预警评分配置(单行)"""

    __tablename__ = "risk_alert_settings"
    __table_args__ = (
        # 应用层直接改库场景下, DB 约束是最后防线(防手工改库写入非法值)
        CheckConstraint(
            f"weight_recharge BETWEEN {MIN_WEIGHT} AND {MAX_WEIGHT} "
            f"AND weight_low_flow BETWEEN {MIN_WEIGHT} AND {MAX_WEIGHT} "
            f"AND weight_contact BETWEEN {MIN_WEIGHT} AND {MAX_WEIGHT} "
            f"AND weight_repeat BETWEEN {MIN_WEIGHT} AND {MAX_WEIGHT}",
            name="ck_risk_alert_settings_weights",
        ),
        CheckConstraint(
            f"recharge_days BETWEEN {MIN_DAYS} AND {MAX_DAYS} "
            f"AND contact_days BETWEEN {MIN_DAYS} AND {MAX_DAYS} "
            f"AND complaint_days BETWEEN {MIN_DAYS} AND {MAX_DAYS} "
            f"AND external_days BETWEEN {MIN_DAYS} AND {MAX_DAYS}",
            name="ck_risk_alert_settings_days",
        ),
        CheckConstraint("low_flow_gb > 0", name="ck_risk_alert_settings_low_flow"),
        CheckConstraint("recharge_high_amount >= 0", name="ck_risk_alert_settings_amount"),
        # 等级线必须严格递减, 否则 _level 语义错乱(高线覆盖低线)
        CheckConstraint(
            f"level_critical > level_high AND level_high > level_medium "
            f"AND level_medium >= {MIN_LEVEL} AND level_critical <= {MAX_LEVEL}",
            name="ck_risk_alert_settings_levels",
        ),
    )

    # ── 四维权重(百分比, 总和不强制=100: 权重只影响相对排序, 缩放不变序) ──
    weight_recharge: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_WEIGHT_RECHARGE, comment="权重: 充值多维")
    weight_low_flow: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_WEIGHT_LOW_FLOW, comment="权重: 流量少维")
    weight_contact: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_WEIGHT_CONTACT, comment="权重: 愿沟通维")
    weight_repeat: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_WEIGHT_REPEAT, comment="权重: 同题重复维")

    # ── 回看窗口(天) ──
    recharge_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_RECHARGE_DAYS, comment="充值回看窗口(天)")
    contact_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_CONTACT_DAYS, comment="接待回看窗口(天)")
    complaint_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_COMPLAINT_DAYS, comment="投诉工单回看窗口(天)")
    external_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_EXTERNAL_DAYS, comment="外部投诉回看窗口(天)")

    # ── 阈值 ──
    low_flow_gb: Mapped[float] = mapped_column(
        Float, nullable=False, default=DEFAULT_LOW_FLOW_GB,
        comment="流量少阈值(GB, 最近完整月; 非金额语义, 允许 Float)")
    recharge_high_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=DEFAULT_RECHARGE_HIGH_AMOUNT,
        comment="高价值充值金额阈值(元)")

    # ── 等级线(总分 cutoff, 严格递减) ──
    level_critical: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_LEVEL_CRITICAL, comment="总分≥此值 → critical")
    level_high: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_LEVEL_HIGH, comment="总分≥此值 → high")
    level_medium: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_LEVEL_MEDIUM, comment="总分≥此值 → medium")

    updated_by: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="最后修改人账号")

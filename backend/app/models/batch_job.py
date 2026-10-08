"""夜间批处理基建模型 (#258 B4-2, S-P1-7)

参考 dify 调度表 + zammad daily_event_lock(只抄机制, 自建不引入):
- BatchJob: 任务调度配置(代码层注册 job 函数, DB 行存调度参数 —— 同 metric_registry 分工)
- BatchJobRun: 执行日志 + 幂等锁载体(status='running' 即锁)

幂等两道锁(部分唯一索引, 见迁移):
- 同一 job 全局仅一个 running(防重叠执行)
- 同一 job 同一 business_date 仅一个 running/success(防当日重跑)
"""

from datetime import date, datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin

# 连续失败熔断阈值(达到即 enabled=false, 需人工重新启用)
BREAKER_FAILURE_THRESHOLD = 3


class BatchJob(TimestampMixin, Base):
    """批任务调度配置(单 worker; schedule_daily_at 为北京时间 HH:MM)"""

    __tablename__ = "batch_jobs"

    job_name: Mapped[str] = mapped_column(String(64), primary_key=True, comment="任务名(代码层 batch_registry 注册键)")
    schedule_daily_at: Mapped[str] = mapped_column(String(5), nullable=False, comment="每日调度时刻 北京时间 HH:MM")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否启用(熔断自动关, 人工重开)")
    timeout_minutes: Mapped[int] = mapped_column(Integer, default=120, nullable=False, comment="运行超时(分钟); 超时 run 标记 timeout 并释放锁")
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="下次调度时刻(UTC tz-aware)")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="最近一次调度时刻")
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="最近结果: success/failed/timeout/skipped")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="最近错误摘要")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="连续失败次数(成功清零; 达阈值熔断)")
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="最后修改人(人工启用/停用留痕)")


class BatchJobRun(IDMixin, TimestampMixin, Base):
    """批任务执行日志(节点级留痕; running 行即幂等锁)"""

    __tablename__ = "batch_job_runs"
    __table_args__ = (
        # 锁1: 同一 job 全局仅一个 running(dify 式防重叠)
        Index(
            "ux_batch_job_runs_running", "job_name",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        # 锁2: 同一 job 同一业务日仅一个 running/success(zammad daily_event_lock 式防重跑)
        Index(
            "ux_batch_job_runs_daily", "job_name", "business_date",
            unique=True,
            postgresql_where=text("status IN ('running', 'success')"),
        ),
    )

    job_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="任务名")
    business_date: Mapped[date] = mapped_column(String(10), nullable=False, comment="业务日(YYYY-MM-DD 北京时间; 夜批跑昨数但归属调度日)")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running", comment="running/success/failed/timeout/skipped")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="结束时刻(运行中为 NULL)")
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="执行明细(行数/耗时等, job 函数返回)")
    error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="错误摘要(失败/超时)")
    triggered_by: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="触发来源: scheduler/manual")

"""B4-2 (#258): 夜间批处理基建 - batch_jobs 调度表 + batch_job_runs 执行日志

Revision ID: b4_2_batch_jobs
Revises: b4_1_metric_dictionary
Create Date: 2026-10-09 01:00:00.000000

依据: 排期 §七 B4-2 (S-P1-7); 机制来源 dify 调度表 + zammad daily_event_lock
     (只抄模式, 自建不引入; 详见 app/services/batch_runner.py 模块注释)。

范围:
1. batch_jobs 调度配置(job_name PK / schedule_daily_at 北京时间 / enabled /
   timeout_minutes / next_run_at / 留痕字段 / consecutive_failures 熔断计数)
2. batch_job_runs 执行日志 + 两道幂等锁(部分唯一索引):
   - ux_batch_job_runs_running: (job_name) WHERE status='running'      防重叠
   - ux_batch_job_runs_daily:   (job_name, business_date)
         WHERE status IN ('running','success')                          防当日重跑

不 seed 调度行: runner 启动时按代码注册表 _ensure_job_rows 幂等补插(ON CONFLICT DO
NOTHING), 新任务挂接零迁移。

downgrade: 新表 DROP 即安全回退, 无业务数据损失(仅夜批日志)。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = 'b4_2_batch_jobs'
down_revision: Union[str, None] = 'b4_1_metric_dictionary'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'batch_jobs',
        sa.Column('job_name', sa.String(length=64), primary_key=True, comment='任务名(代码层 batch_registry 注册键)'),
        sa.Column('schedule_daily_at', sa.String(length=5), nullable=False, comment='每日调度时刻 北京时间 HH:MM'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true'),
                  comment='是否启用(熔断自动关, 人工重开)'),
        sa.Column('timeout_minutes', sa.Integer(), nullable=False, server_default='120',
                  comment='运行超时(分钟); 超时 run 标记 timeout 并释放锁'),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True, comment='下次调度时刻(UTC tz-aware)'),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True, comment='最近一次调度时刻'),
        sa.Column('last_status', sa.String(length=16), nullable=True, comment='最近结果: success/failed/timeout/skipped'),
        sa.Column('last_error', sa.Text(), nullable=True, comment='最近错误摘要'),
        sa.Column('consecutive_failures', sa.Integer(), nullable=False, server_default='0',
                  comment='连续失败次数(成功清零; 达阈值熔断)'),
        sa.Column('updated_by', sa.String(length=64), nullable=True, comment='最后修改人(人工启用/停用留痕)'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        'batch_job_runs',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('job_name', sa.String(length=64), nullable=False, index=True),
        sa.Column('business_date', sa.String(length=10), nullable=False,
                  comment='业务日(YYYY-MM-DD 北京时间; 夜批跑昨数但归属调度日)'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='running',
                  comment='running/success/failed/timeout/skipped'),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True, comment='结束时刻(运行中为 NULL)'),
        sa.Column('detail', JSONB(), nullable=True, comment='执行明细(行数/耗时等, job 函数返回)'),
        sa.Column('error', sa.Text(), nullable=True, comment='错误摘要(失败/超时)'),
        sa.Column('triggered_by', sa.String(length=32), nullable=True, comment='触发来源: scheduler/manual'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # 幂等锁 1: 同一 job 全局仅一个 running
    op.create_index(
        'ux_batch_job_runs_running', 'batch_job_runs', ['job_name'],
        unique=True, postgresql_where=sa.text("status = 'running'"),
    )
    # 幂等锁 2: 同一 job 同一业务日仅一个 running/success
    op.create_index(
        'ux_batch_job_runs_daily', 'batch_job_runs', ['job_name', 'business_date'],
        unique=True, postgresql_where=sa.text("status IN ('running', 'success')"),
    )


def downgrade() -> None:
    op.drop_index('ux_batch_job_runs_daily', table_name='batch_job_runs',
                  postgresql_where=sa.text("status IN ('running', 'success')"))
    op.drop_index('ux_batch_job_runs_running', table_name='batch_job_runs',
                  postgresql_where=sa.text("status = 'running'"))
    op.drop_table('batch_job_runs')
    op.drop_table('batch_jobs')

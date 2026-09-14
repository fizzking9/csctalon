"""add SCRM models: scrm_operators/scrm_sessions/scrm_session_transfers/scrm_sync_logs + persons SCRM fields

对接微信聚合平台(SCRM)所需的 4 张新业务表 + persons 表加 2 列(SCRM operator 对接字段):
  - persons: 加 scrm_operator_id(String(128),index) + scrm_operator_name(String(64))
  - scrm_operators: 客服账号快照(按 operator_id upsert,可归并到 Person)
  - scrm_sessions: 会话主表(私聊/群聊/虚拟三合一,含首响/均响/消息数等绩效指标)
  - scrm_session_transfers: 转接事件流(一对多归属会话)
  - scrm_sync_logs: SCRM 独立同步日志(分页调用量/对账独立)

Revision ID: z9y8x7w6v5u4
Revises: a1b2c3d4e5f6
Create Date: 2026-08-04 16:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'z9y8x7w6v5u4'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) persons 表加 2 列(SCRM 对接)
    op.add_column(
        'persons',
        sa.Column('scrm_operator_id', sa.String(length=128), nullable=True,
                  comment='SCRM平台客服ID(对接后回填,聚合平台内部ID≠企微ID)')
    )
    op.add_column(
        'persons',
        sa.Column('scrm_operator_name', sa.String(length=64), nullable=True,
                  comment='SCRM平台客服显示名原文')
    )
    op.create_index('ix_persons_scrm_operator_id', 'persons', ['scrm_operator_id'])

    # 2) scrm_operators (客服账号快照)
    op.create_table(
        'scrm_operators',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('operator_id', sa.String(length=128), nullable=False, comment='SCRM 平台客服 ID(业务唯一键)'),
        sa.Column('operator_name', sa.String(length=64), nullable=False, comment='客服显示名(原始名,如 企微-张三)'),
        sa.Column('person_id', sa.BigInteger(), nullable=True, comment='归一后归属 Person；null 表示待归并'),
        sa.Column('department', sa.String(length=128), nullable=True, comment='SCRM 侧部门名称'),
        sa.Column('position', sa.String(length=64), nullable=True, comment='岗位/角色原文'),
        sa.Column('status', sa.String(length=16), nullable=True, comment='在线状态:online/offline/busy(原文保留)'),
        sa.Column('last_active_at', sa.DateTime(timezone=True), nullable=True, comment='SCRM 上报的最后活跃时间'),
        sa.Column('receive_count_today', sa.Integer(), nullable=True, comment='当日接待会话数(如接口有,则每次同步覆盖)'),
        sa.Column('raw_extra', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='原始响应 JSON 冗余'),
        sa.Column('remark', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['person_id'], ['persons.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        comment='SCRM 平台侧客服账号(操作员快照,用于别名归一)',
    )
    op.create_index('ix_scrm_operators_operator_id', 'scrm_operators', ['operator_id'], unique=True)
    op.create_index('ix_scrm_operators_operator_name', 'scrm_operators', ['operator_name'])
    op.create_index('ix_scrm_operators_person_id', 'scrm_operators', ['person_id'])
    op.create_index('ix_scrm_operators_status', 'scrm_operators', ['status'])
    op.create_index('ix_scrm_operators_last_active_at', 'scrm_operators', ['last_active_at'])

    # 3) scrm_sessions (会话主表:private/group/virtual 三合一)
    op.create_table(
        'scrm_sessions',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('session_id', sa.String(length=128), nullable=False, comment='SCRM 会话 ID(业务唯一键)'),
        sa.Column('session_type', sa.String(length=16), nullable=False, comment='会话类型:private/group/virtual'),
        sa.Column('operator_id', sa.String(length=128), nullable=True, comment='SCRM 客服ID → scrm_operators.operator_id'),
        sa.Column('operator_name', sa.String(length=64), nullable=True, comment='客服显示名原文(用于别名归一)'),
        sa.Column('person_id', sa.BigInteger(), nullable=True, comment='别名归一后归属 Person；null 表示待归并'),
        sa.Column('customer_id', sa.String(length=128), nullable=True, comment='SCRM 客户 ID/外显微信 ID(如有)'),
        sa.Column('customer_name', sa.String(length=128), nullable=True, comment='客户昵称/备注'),
        sa.Column('group_id', sa.String(length=128), nullable=True, comment='群聊ID(仅群聊会话有)'),
        sa.Column('group_name', sa.String(length=256), nullable=True),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=False, comment='会话开始时间'),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=True, comment='会话结束时间(空=仍在进行)'),
        sa.Column('duration_sec', sa.Integer(), nullable=True, comment='会话时长(秒):end-start 预计算'),
        sa.Column('msg_count_customer', sa.Integer(), server_default='0', nullable=False, comment='客户消息数'),
        sa.Column('msg_count_staff', sa.Integer(), server_default='0', nullable=False, comment='客服回复消息数'),
        sa.Column('first_response_sec', sa.Integer(), nullable=True, comment='首次响应时长(秒)'),
        sa.Column('avg_response_sec', sa.Integer(), nullable=True, comment='平均响应时长(秒)'),
        sa.Column('transfer_count', sa.Integer(), server_default='0', nullable=False, comment='转接次数(0=未转接;1+=转接N次)'),
        sa.Column('question_type_id', sa.String(length=64), nullable=True, comment='SCRM 问题类型ID'),
        sa.Column('question_type_name', sa.String(length=128), nullable=True, comment='问题类型名称原文'),
        sa.Column('sync_status', sa.String(length=16), server_default='open', nullable=False, comment='open=进行中;closed=已结束;reconciled=已对账确认'),
        sa.Column('quality_computed', sa.Boolean(), server_default=sa.false(), nullable=False, comment='首响/均响/时长 是否已计算完成'),
        sa.Column('raw_extra', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='原始会话 JSON 冗余(便于后续加字段不用重拉)'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['person_id'], ['persons.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        comment='SCRM 客服接待会话(三合一:private/group/virtual,核心绩效数据源)',
    )
    op.create_index('ix_scrm_sessions_session_id', 'scrm_sessions', ['session_id'], unique=True)
    op.create_index('ix_scrm_sessions_session_type', 'scrm_sessions', ['session_type'])
    op.create_index('ix_scrm_sessions_operator_id', 'scrm_sessions', ['operator_id'])
    op.create_index('ix_scrm_sessions_operator_name', 'scrm_sessions', ['operator_name'])
    op.create_index('ix_scrm_sessions_person_id', 'scrm_sessions', ['person_id'])
    op.create_index('ix_scrm_sessions_customer_id', 'scrm_sessions', ['customer_id'])
    op.create_index('ix_scrm_sessions_group_id', 'scrm_sessions', ['group_id'])
    op.create_index('ix_scrm_sessions_start_time', 'scrm_sessions', ['start_time'])
    op.create_index('ix_scrm_sessions_end_time', 'scrm_sessions', ['end_time'])
    op.create_index('ix_scrm_sessions_question_type_name', 'scrm_sessions', ['question_type_name'])
    op.create_index('ix_scrm_sessions_sync_status', 'scrm_sessions', ['sync_status'])
    # 复合索引:看板查询常用组合
    op.create_index('ix_scrm_sessions_person_start', 'scrm_sessions', ['person_id', 'start_time'])
    op.create_index('ix_scrm_sessions_type_start', 'scrm_sessions', ['session_type', 'start_time'])
    op.create_index('ix_scrm_sessions_qtype_start', 'scrm_sessions', ['question_type_name', 'start_time'])

    # 4) scrm_session_transfers (转接事件流)
    op.create_table(
        'scrm_session_transfers',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('session_id', sa.String(length=128), nullable=False, comment='归属会话'),
        sa.Column('from_operator_id', sa.String(length=128), nullable=True, comment='转出客服 SCRM ID'),
        sa.Column('from_operator_name', sa.String(length=64), nullable=True, comment='转出客服名原文'),
        sa.Column('to_operator_id', sa.String(length=128), nullable=True, comment='转入客服 SCRM ID'),
        sa.Column('to_operator_name', sa.String(length=64), nullable=True, comment='转入客服名原文'),
        sa.Column('transfer_time', sa.DateTime(timezone=True), nullable=False, comment='转接发生时间'),
        sa.Column('transfer_reason', sa.String(length=256), nullable=True, comment='转接原因(如接口给)'),
        sa.Column('raw_extra', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['scrm_sessions.session_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        comment='SCRM 会话转接事件(每条记录一次转出→转入)',
    )
    op.create_index('ix_scrm_session_transfers_session_id', 'scrm_session_transfers', ['session_id'])
    op.create_index('ix_scrm_session_transfers_from_operator_id', 'scrm_session_transfers', ['from_operator_id'])
    op.create_index('ix_scrm_session_transfers_to_operator_id', 'scrm_session_transfers', ['to_operator_id'])
    op.create_index('ix_scrm_session_transfers_transfer_time', 'scrm_session_transfers', ['transfer_time'])

    # 5) scrm_sync_logs (同步日志,独立于 BMS)
    op.create_table(
        'scrm_sync_logs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('data_type', sa.String(length=32), nullable=False,
                  comment='scrm_operator/scrm_session_private/scrm_session_group/scrm_session_virtual'),
        sa.Column('sync_scope', sa.String(length=256), nullable=True, comment='本次查询窗口: startTime→endTime 摘要'),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=False, comment='查询的起始时间(本地东八区)'),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=False, comment='查询的结束时间(本地东八区)'),
        sa.Column('status', sa.String(length=16), server_default='running', nullable=False,
                  comment='running/success/failed/reconciled_mismatch'),
        sa.Column('trigger_type', sa.String(length=16), server_default='manual', nullable=False, comment='manual/scheduled/catch_up'),
        sa.Column('triggered_by', sa.String(length=64), nullable=True, comment='触发者用户名'),
        sa.Column('api_call_count', sa.Integer(), server_default='0', nullable=False, comment='本轮 HTTP 调用次数(含分页)'),
        sa.Column('api_total_reported', sa.Integer(), nullable=True, comment='接口返回的 total 之和(跨页相加)'),
        sa.Column('fetched_rows', sa.Integer(), server_default='0', nullable=False, comment='实际收到的记录数'),
        sa.Column('inserted_rows', sa.Integer(), server_default='0', nullable=False, comment='新写入行数'),
        sa.Column('updated_rows', sa.Integer(), server_default='0', nullable=False, comment='已存在并覆盖更新行数'),
        sa.Column('failed_rows', sa.Integer(), server_default='0', nullable=False, comment='入库失败行(解析错/主键错)'),
        sa.Column('reconcile_delta', sa.Integer(), nullable=True, comment='api_total_reported − (inserted+updated);正值=可能漏采'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True, comment='本轮结束时间'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        comment='SCRM OpenAPI 同步运行日志(独立于 BMS,字段贴合分页调用+对账)',
    )
    op.create_index('ix_scrm_sync_logs_data_type', 'scrm_sync_logs', ['data_type'])
    op.create_index('ix_scrm_sync_logs_status', 'scrm_sync_logs', ['status'])
    op.create_index('ix_scrm_sync_logs_trigger_type', 'scrm_sync_logs', ['trigger_type'])
    op.create_index('ix_scrm_sync_logs_created_at', 'scrm_sync_logs', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_scrm_sync_logs_created_at', table_name='scrm_sync_logs')
    op.drop_index('ix_scrm_sync_logs_trigger_type', table_name='scrm_sync_logs')
    op.drop_index('ix_scrm_sync_logs_status', table_name='scrm_sync_logs')
    op.drop_index('ix_scrm_sync_logs_data_type', table_name='scrm_sync_logs')
    op.drop_table('scrm_sync_logs')

    op.drop_index('ix_scrm_session_transfers_transfer_time', table_name='scrm_session_transfers')
    op.drop_index('ix_scrm_session_transfers_to_operator_id', table_name='scrm_session_transfers')
    op.drop_index('ix_scrm_session_transfers_from_operator_id', table_name='scrm_session_transfers')
    op.drop_index('ix_scrm_session_transfers_session_id', table_name='scrm_session_transfers')
    op.drop_table('scrm_session_transfers')

    op.drop_index('ix_scrm_sessions_qtype_start', table_name='scrm_sessions')
    op.drop_index('ix_scrm_sessions_type_start', table_name='scrm_sessions')
    op.drop_index('ix_scrm_sessions_person_start', table_name='scrm_sessions')
    op.drop_index('ix_scrm_sessions_sync_status', table_name='scrm_sessions')
    op.drop_index('ix_scrm_sessions_question_type_name', table_name='scrm_sessions')
    op.drop_index('ix_scrm_sessions_end_time', table_name='scrm_sessions')
    op.drop_index('ix_scrm_sessions_start_time', table_name='scrm_sessions')
    op.drop_index('ix_scrm_sessions_group_id', table_name='scrm_sessions')
    op.drop_index('ix_scrm_sessions_customer_id', table_name='scrm_sessions')
    op.drop_index('ix_scrm_sessions_person_id', table_name='scrm_sessions')
    op.drop_index('ix_scrm_sessions_operator_name', table_name='scrm_sessions')
    op.drop_index('ix_scrm_sessions_operator_id', table_name='scrm_sessions')
    op.drop_index('ix_scrm_sessions_session_type', table_name='scrm_sessions')
    op.drop_index('ix_scrm_sessions_session_id', table_name='scrm_sessions')
    op.drop_table('scrm_sessions')

    op.drop_index('ix_scrm_operators_last_active_at', table_name='scrm_operators')
    op.drop_index('ix_scrm_operators_status', table_name='scrm_operators')
    op.drop_index('ix_scrm_operators_person_id', table_name='scrm_operators')
    op.drop_index('ix_scrm_operators_operator_name', table_name='scrm_operators')
    op.drop_index('ix_scrm_operators_operator_id', table_name='scrm_operators')
    op.drop_table('scrm_operators')

    op.drop_index('ix_persons_scrm_operator_id', table_name='persons')
    op.drop_column('persons', 'scrm_operator_name')
    op.drop_column('persons', 'scrm_operator_id')

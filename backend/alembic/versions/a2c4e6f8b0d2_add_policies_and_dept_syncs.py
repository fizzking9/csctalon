"""add policies and dept_info_syncs tables (占位建表迁移)

管理闭环 Phase 2 新增两张表:
- policies: 管理制度/服务规范/流程SOP/沟通话术/应急预案,带分类、版本、正文+附件、
  状态流转(草稿→待审批→已发布→归档)+审批/调整/归档审计操作
- dept_info_syncs: 部门间信息同步(规则变动/流程变动/活动通知/风险预警/数据同步/协作请求),
  带优先级(低/普通/高/紧急)+状态(草稿→已发布→已读→已回复→已闭环)+阅读回执/回复/闭环

说明:
- 本表是 alembic 迁移占位,运行 `cd backend && alembic upgrade head` 即可自动建表。
- 若当前环境不跑 Alembic,可复制同目录下 `_manual_placeholder_sql/policies_and_dept_syncs.sql`
  直接在 PostgreSQL 控制台执行。
- 所有写入 requests (POST/PUT/PATCH/DELETE) 都会经 operation_log_middleware 自动
  写 operation_logs 表,支持分类/审批/发布/归档/调整/回复/闭环 等动作中文标签检索。

Revision ID: a2c4e6f8b0d2
Revises: z9y8x7w6v5u4
Create Date: 2026-08-09 09:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a2c4e6f8b0d2'
down_revision: Union[str, None] = 'z9y8x7w6v5u4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- policies 管理制度 ----
    op.create_table(
        'policies',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False, comment='制度标题'),
        sa.Column('category', sa.String(length=32), server_default='other', nullable=False, comment='分类:service_standard/process_sop/management_rule/communication_script/incident_response/other'),
        sa.Column('summary', sa.String(length=500), nullable=True, comment='摘要/关键词'),
        sa.Column('content', sa.Text(), nullable=True, comment='正文内容(Markdown/纯文本)'),
        sa.Column('attachment_urls', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='附件URL列表(JSON数组)'),
        sa.Column('version', sa.String(length=32), nullable=True, comment='版本号'),
        sa.Column('status', sa.String(length=32), server_default='draft', nullable=False, comment='发布状态:draft/pending_approval/published/archived'),
        sa.Column('valid_from', sa.DateTime(timezone=True), nullable=True, comment='生效起始'),
        sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True, comment='生效结束'),
        sa.Column('owner_id', sa.Integer(), nullable=True, comment='制度负责人'),
        sa.Column('approver_id', sa.Integer(), nullable=True, comment='审批人'),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True, comment='审批时间'),
        sa.Column('approver_note', sa.Text(), nullable=True, comment='审批意见'),
        sa.Column('view_count', sa.Integer(), server_default='0', nullable=False, comment='查阅次数'),
        sa.Column('created_by', sa.String(length=64), nullable=True, comment='创建人'),
        sa.Column('remark', sa.Text(), nullable=True, comment='备注/调整记录'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['approver_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        comment='管理制度/服务规范/流程SOP/话术/预案',
    )
    op.create_index('ix_policies_category', 'policies', ['category'])
    op.create_index('ix_policies_status', 'policies', ['status'])

    # ---- dept_info_syncs 部门间信息同步 ----
    op.create_table(
        'dept_info_syncs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False, comment='同步标题'),
        sa.Column('category', sa.String(length=32), server_default='other', nullable=False, comment='分类:rule_change/process_change/activity_notice/risk_alert/data_sync/coordination/other'),
        sa.Column('priority', sa.String(length=16), server_default='normal', nullable=False, comment='优先级:low/normal/high/urgent'),
        sa.Column('status', sa.String(length=24), server_default='draft', nullable=False, comment='同步状态:draft/sent/read/responded/closed'),
        sa.Column('from_department', sa.String(length=128), nullable=True, comment='发起部门'),
        sa.Column('to_departments', sa.String(length=500), nullable=True, comment='接收部门(多部门逗号分隔)'),
        sa.Column('content', sa.Text(), nullable=True, comment='同步正文'),
        sa.Column('impact', sa.Text(), nullable=True, comment='影响范围/需要配合的动作'),
        sa.Column('attachment_urls', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='附件URL列表(JSON数组)'),
        sa.Column('related_incident_id', sa.Integer(), nullable=True, comment='关联异常事件ID'),
        sa.Column('related_measure_id', sa.Integer(), nullable=True, comment='关联措施ID'),
        sa.Column('effective_from', sa.DateTime(timezone=True), nullable=True, comment='生效时间(变化类)'),
        sa.Column('deadline', sa.DateTime(timezone=True), nullable=True, comment='需回复/配合截止'),
        sa.Column('sender_id', sa.Integer(), nullable=True, comment='发送人'),
        sa.Column('receiver_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='接收人ID列表(JSON数组)'),
        sa.Column('read_receipts', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='阅读回执[{user_id, username, read_at}]'),
        sa.Column('response', sa.Text(), nullable=True, comment='接收方回复/协调结果'),
        sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True, comment='回复时间'),
        sa.Column('responded_by', sa.String(length=64), nullable=True, comment='回复人账号'),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True, comment='闭环时间'),
        sa.Column('closed_by', sa.String(length=64), nullable=True, comment='闭环人账号'),
        sa.Column('created_by', sa.String(length=64), nullable=True, comment='创建人账号'),
        sa.Column('remark', sa.Text(), nullable=True, comment='备注/调整记录'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        comment='部门间信息同步登记',
    )
    op.create_index('ix_dept_info_syncs_category', 'dept_info_syncs', ['category'])
    op.create_index('ix_dept_info_syncs_priority', 'dept_info_syncs', ['priority'])
    op.create_index('ix_dept_info_syncs_status', 'dept_info_syncs', ['status'])


def downgrade() -> None:
    op.drop_index('ix_dept_info_syncs_status', table_name='dept_info_syncs')
    op.drop_index('ix_dept_info_syncs_priority', table_name='dept_info_syncs')
    op.drop_index('ix_dept_info_syncs_category', table_name='dept_info_syncs')
    op.drop_table('dept_info_syncs')

    op.drop_index('ix_policies_status', table_name='policies')
    op.drop_index('ix_policies_category', table_name='policies')
    op.drop_table('policies')

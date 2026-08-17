"""add groups and specialties tables

新增行政分组与业务专项双体系 (6张表):
  Group体系 (行政管理维度):
    - groups: 行政分组 (如 董梦玲组/叶珍凤组/张蕾组等)
    - group_leadership_history: 组长/副主管变更历史 (SCD Type 2)
    - person_group_memberships: 人员分组归属历史 (SCD Type 2)
  Specialty体系 (业务任务维度):
    - specialties: 业务专项 (咨询接待/套餐专项/退款处理/退款挽回/内部投诉/外部投诉)
    - specialty_leadership_history: 专项负责人变更历史 (SCD Type 2)
    - person_specialty_assignments: 人员专项分配历史 (SCD Type 2, 支持主/辅专项)

对应模型文件: app/models/group.py, app/models/specialty.py
提交 f418255 引入了模型代码但遗漏了 migration, 此文件补齐.

说明:
  本地/已部署环境的 6 张表已通过 Base.metadata.create_all() 自动创建,
  此 migration 的 upgrade() 使用 IF NOT EXISTS 语义 (先检查再建),
  因此对已有表的环境是幂等安全的; 新环境通过 alembic upgrade head 也可正确建表.
  已有环境执行后需 alembic stamp 到此版本 (不重复执行DDL).

Revision ID: n1b2c3d4e5f6
Revises: m0a1b2c3d4e5
Create Date: 2026-08-17 10:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'n1b2c3d4e5f6'
down_revision: Union[str, None] = 'm0a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    """检查表是否已存在 (用于幂等升级, 避免重复建表报错)"""
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=:t)"
    ), {"t": table_name})
    return bool(result.scalar())


def upgrade() -> None:
    # ==================== Group体系 (3张表) ====================

    # 1. groups: 行政分组
    if not _table_exists('groups'):
        op.create_table(
            'groups',
            sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column('name', sa.String(64), nullable=False, comment='组名,如 董梦玲组'),
            sa.Column('department', sa.String(32), nullable=True, comment='所属部门:企微/店铺/研发'),
            sa.Column('description', sa.Text(), nullable=True, comment='备注'),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true'), comment='是否启用'),
            sa.Column('display_order', sa.Integer(), nullable=False, server_default='0', comment='显示排序'),
            sa.Column('current_leader_id', sa.BigInteger(), sa.ForeignKey('persons.id'), nullable=True, comment='当前组长ID'),
            sa.Column('current_deputy_leader_id', sa.BigInteger(), sa.ForeignKey('persons.id'), nullable=True, comment='当前副主管ID'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('name', name='ix_groups_name'),
        )
        op.create_index('ix_groups_department', 'groups', ['department'])

    # 2. group_leadership_history: 组长/副主管变更历史
    if not _table_exists('group_leadership_history'):
        op.create_table(
            'group_leadership_history',
            sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column('group_id', sa.BigInteger(), sa.ForeignKey('groups.id', ondelete='CASCADE'), nullable=False, comment='分组ID'),
            sa.Column('person_id', sa.BigInteger(), sa.ForeignKey('persons.id'), nullable=False, comment='被任命人'),
            sa.Column('role', sa.String(16), nullable=False, comment='角色:leader=组长,deputy=副主管'),
            sa.Column('effective_from', sa.Date(), nullable=False, comment='生效日期'),
            sa.Column('effective_to', sa.Date(), nullable=True, comment='失效日期(None=至今)'),
            sa.Column('is_current', sa.Boolean(), nullable=False, server_default=sa.text('true'), comment='是否当前任职'),
            sa.Column('change_reason', sa.Text(), nullable=True, comment='变更原因'),
            sa.Column('source', sa.String(32), nullable=True, comment='来源:manual/xlsx_import'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_group_leadership_history_group_id', 'group_leadership_history', ['group_id'])
        op.create_index('ix_group_leadership_history_person_id', 'group_leadership_history', ['person_id'])
        op.create_index('ix_group_leadership_history_is_current', 'group_leadership_history', ['is_current'])
        op.create_index('ix_glh_group_role_current', 'group_leadership_history', ['group_id', 'role', 'is_current'])

    # 3. person_group_memberships: 人员分组归属历史
    if not _table_exists('person_group_memberships'):
        op.create_table(
            'person_group_memberships',
            sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column('person_id', sa.BigInteger(), sa.ForeignKey('persons.id', ondelete='CASCADE'), nullable=False, comment='人员ID'),
            sa.Column('group_id', sa.BigInteger(), sa.ForeignKey('groups.id', ondelete='CASCADE'), nullable=False, comment='分组ID'),
            sa.Column('effective_from', sa.Date(), nullable=False, comment='入组日期'),
            sa.Column('effective_to', sa.Date(), nullable=True, comment='离组日期(None=至今)'),
            sa.Column('is_current', sa.Boolean(), nullable=False, server_default=sa.text('true'), comment='是否当前归属'),
            sa.Column('change_reason', sa.Text(), nullable=True, comment='变更原因(入组/调组/离组)'),
            sa.Column('source', sa.String(32), nullable=True, comment='来源'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_person_group_memberships_person_id', 'person_group_memberships', ['person_id'])
        op.create_index('ix_person_group_memberships_group_id', 'person_group_memberships', ['group_id'])
        op.create_index('ix_person_group_memberships_is_current', 'person_group_memberships', ['is_current'])
        op.create_index('ix_pgm_person_current', 'person_group_memberships', ['person_id', 'is_current'])

    # ==================== Specialty体系 (3张表) ====================

    # 4. specialties: 业务专项
    if not _table_exists('specialties'):
        op.create_table(
            'specialties',
            sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column('code', sa.String(32), nullable=True, comment='编码(consulting/complaint等)'),
            sa.Column('name', sa.String(64), nullable=False, comment='专项名称,如 咨询接待'),
            sa.Column('description', sa.Text(), nullable=True, comment='专项描述'),
            sa.Column('difficulty_weight', sa.Float(), nullable=False, server_default='1.0', comment='难度系数'),
            sa.Column('is_temporary', sa.Boolean(), nullable=False, server_default=sa.text('false'), comment='是否临时专项'),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true'), comment='是否启用'),
            sa.Column('display_order', sa.Integer(), nullable=False, server_default='0', comment='显示排序'),
            sa.Column('valid_from', sa.Date(), nullable=True, comment='专项生效日期'),
            sa.Column('valid_to', sa.Date(), nullable=True, comment='专项结束日期'),
            sa.Column('current_lead_id', sa.BigInteger(), sa.ForeignKey('persons.id'), nullable=True, comment='当前专项负责人ID'),
            sa.Column('current_deputy_lead_id', sa.BigInteger(), sa.ForeignKey('persons.id'), nullable=True, comment='当前副负责人ID'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('code', name='ix_specialties_code'),
            sa.UniqueConstraint('name', name='ix_specialties_name'),
        )

    # 5. specialty_leadership_history: 专项负责人变更历史
    if not _table_exists('specialty_leadership_history'):
        op.create_table(
            'specialty_leadership_history',
            sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column('specialty_id', sa.BigInteger(), sa.ForeignKey('specialties.id', ondelete='CASCADE'), nullable=False, comment='专项ID'),
            sa.Column('person_id', sa.BigInteger(), sa.ForeignKey('persons.id'), nullable=False, comment='被任命人'),
            sa.Column('role', sa.String(16), nullable=False, comment='角色:lead=负责人,deputy=副负责人'),
            sa.Column('effective_from', sa.Date(), nullable=False, comment='生效日期'),
            sa.Column('effective_to', sa.Date(), nullable=True, comment='失效日期(None=至今)'),
            sa.Column('is_current', sa.Boolean(), nullable=False, server_default=sa.text('true'), comment='是否当前任职'),
            sa.Column('change_reason', sa.Text(), nullable=True, comment='变更原因'),
            sa.Column('source', sa.String(32), nullable=True, comment='来源'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_specialty_leadership_history_specialty_id', 'specialty_leadership_history', ['specialty_id'])
        op.create_index('ix_specialty_leadership_history_person_id', 'specialty_leadership_history', ['person_id'])
        op.create_index('ix_specialty_leadership_history_is_current', 'specialty_leadership_history', ['is_current'])
        op.create_index('ix_slh_spec_role_current', 'specialty_leadership_history', ['specialty_id', 'role', 'is_current'])

    # 6. person_specialty_assignments: 人员专项分配历史
    if not _table_exists('person_specialty_assignments'):
        op.create_table(
            'person_specialty_assignments',
            sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column('person_id', sa.BigInteger(), sa.ForeignKey('persons.id', ondelete='CASCADE'), nullable=False, comment='人员ID'),
            sa.Column('specialty_id', sa.BigInteger(), sa.ForeignKey('specialties.id', ondelete='CASCADE'), nullable=False, comment='专项ID'),
            sa.Column('is_primary', sa.Boolean(), nullable=False, server_default=sa.text('true'), comment='是否主专项'),
            sa.Column('effective_from', sa.Date(), nullable=False, comment='分配生效日期'),
            sa.Column('effective_to', sa.Date(), nullable=True, comment='分配结束日期'),
            sa.Column('is_current', sa.Boolean(), nullable=False, server_default=sa.text('true'), comment='是否当前分配'),
            sa.Column('change_reason', sa.Text(), nullable=True, comment='变更原因'),
            sa.Column('source', sa.String(32), nullable=True, comment='来源'),
            sa.Column('workload_ratio', sa.Float(), nullable=False, server_default='1.0', comment='工作量占比'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_person_specialty_assignments_person_id', 'person_specialty_assignments', ['person_id'])
        op.create_index('ix_person_specialty_assignments_specialty_id', 'person_specialty_assignments', ['specialty_id'])
        op.create_index('ix_person_specialty_assignments_is_current', 'person_specialty_assignments', ['is_current'])
        op.create_index('ix_psa_person_current', 'person_specialty_assignments', ['person_id', 'is_current'])
        op.create_index('ix_psa_spec_current', 'person_specialty_assignments', ['specialty_id', 'is_current'])

    # ==================== 种子数据: 6个标准专项 ====================
    # 对齐 app/models/specialty.py 中的 STANDARD_SPECIALTIES
    conn = op.get_bind()
    existing = conn.execute(sa.text("SELECT count(*) FROM specialties")).scalar()
    if existing == 0:
        conn.execute(sa.text("""
            INSERT INTO specialties (code, name, description, difficulty_weight, is_temporary, is_active, display_order, created_at, updated_at)
            VALUES
                ('consulting',         '咨询接待', '常规咨询接待工作',                               1.0, false, true, 1, now(), now()),
                ('package',            '套餐专项', '套餐相关问题处理',                               1.8, false, true, 2, now(), now()),
                ('refund_processing',  '退款处理', '日常退款操作处理(唐彩慧等)',                      2.0, false, true, 3, now(), now()),
                ('refund_recovery',    '退款挽回', '退款客户挽回工作(朱海洋组)',                      2.2, false, true, 4, now(), now()),
                ('internal_complaint', '内部投诉', '私域/企微内部客户投诉处理(刘景山/苏紫依等)',       3.0, false, true, 5, now(), now()),
                ('external_complaint', '外部投诉', '平台/12315等外部投诉处理(洪佳/陈玉红等)',          5.0, false, true, 6, now(), now())
            ON CONFLICT (name) DO NOTHING
        """))


def downgrade() -> None:
    # 按依赖反序删除 (先删引用方, 再删被引用方)
    op.drop_table('person_specialty_assignments')
    op.drop_table('specialty_leadership_history')
    op.drop_table('specialties')
    op.drop_table('person_group_memberships')
    op.drop_table('group_leadership_history')
    op.drop_table('groups')

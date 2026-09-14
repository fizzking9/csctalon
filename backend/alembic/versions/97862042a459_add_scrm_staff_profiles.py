"""add_scrm_staff_profiles

只建 SCRM 人设信息独立表，其他无关改动（其他表的索引/列 comment 变更）全部不包含 ——
autogen 把 DB 状态与 metadata head 的差异也带进来了，那些不是本次需求，且包含 drop_table 破坏性操作，故全部移除。

Revision ID: 97862042a459
Revises: c3e4f5a6b7c8
Create Date: 2026-08-07 13:06:50.653313
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '97862042a459'
down_revision: Union[str, None] = 'c3e4f5a6b7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'scrm_staff_profiles',
        sa.Column('source_config_id', sa.BigInteger(), nullable=True,
                  comment='来源 sync_source_configs.id(白音 SCRM 网页登录源)'),
        sa.Column('wecom_nick', sa.String(length=128), nullable=False,
                  comment="企微人设昵称 如 '小苏（苏紫依）'"),
        sa.Column('enterprise_suffix', sa.String(length=128), nullable=True,
                  comment="所属企业后缀(从 @拆分) 如 '自在联'/'云客猫'"),
        sa.Column('staff_unique_key', sa.String(length=256), nullable=False,
                  comment="业务唯一键 = md5(source_config_id or 0 + '|' + wecom_nick + '|' + (enterprise_suffix or ''))"),
        sa.Column('person_id', sa.BigInteger(), nullable=True,
                  comment='归一化归属 Person;null=未匹配/待人工确认'),
        sa.Column('user_id', sa.BigInteger(), nullable=True,
                  comment='直接绑定本地 Web 登录账号 users.id; 可选'),
        sa.Column('match_status', sa.String(length=16), nullable=False, server_default='unmatched',
                  comment='匹配状态: unmatched=未匹配 / auto_matched=自动匹配 / manual_confirmed=人工确认 / rejected=排除'),
        sa.Column('match_score', sa.Integer(), nullable=True,
                  comment='自动匹配评分(越高越像),供人工确认参考'),
        sa.Column('match_reason', sa.String(length=256), nullable=True,
                  comment='自动匹配命中的规则说明'),
        sa.Column('company_group', sa.String(length=128), nullable=True,
                  comment="所属企业/分组 (页面 分组列) 如 '深圳市云客猫科技有限公司'"),
        sa.Column('online_status', sa.String(length=16), nullable=True,
                  comment='在线状态: online=在线 / offline=离线 / busy=忙碌 等(原文保留)'),
        sa.Column('real_name', sa.String(length=64), nullable=True,
                  comment='实名 (页面 实名 列)'),
        sa.Column('alias_name', sa.String(length=64), nullable=True,
                  comment="别名/微信号 (页面 别名 列 如 '随身WiFi-小苏')"),
        sa.Column('external_display_name', sa.String(length=64), nullable=True,
                  comment='对外展示名 (客户侧看到的昵称)'),
        sa.Column('gender', sa.String(length=8), nullable=True,
                  comment='性别: 男/女/未设置'),
        sa.Column('device_type', sa.String(length=32), nullable=True,
                  comment='设备类型: ipad扫码号 / 手机 / PC 网页等(页面 类型 列)'),
        sa.Column('auto_accept_friend', sa.String(length=16), nullable=True,
                  comment='自动通过好友请求: 已开启/已关闭/未设置'),
        sa.Column('auto_accept_group_verify', sa.String(length=16), nullable=True,
                  comment='通过群聊验证: 已开启/已关闭/未设置'),
        sa.Column('moments_cover', sa.String(length=256), nullable=True,
                  comment='朋友圈封面 (文件名或 URL,原文保留)'),
        sa.Column('persona_template', sa.String(length=128), nullable=True,
                  comment='人设模板名称'),
        sa.Column('new_customer_template', sa.String(length=128), nullable=True,
                  comment='新客运营模板名称'),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True,
                  comment='最近一次同步抓取时间'),
        sa.Column('sync_source_name', sa.String(length=128), nullable=True,
                  comment='来源源名(冗余,便于前端展示)'),
        sa.Column('raw_extra', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='页面解析出的原始行(中文列名)冗余,方便后续扩字段'),
        sa.Column('remark', sa.Text(), nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['person_id'], ['persons.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['source_config_id'], ['sync_source_configs.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source_config_id', 'wecom_nick', 'enterprise_suffix',
                            name='uq_scrm_staff_src_nick_ent'),
    )
    op.create_index('ix_scrm_staff_company_status', 'scrm_staff_profiles',
                    ['company_group', 'online_status'], unique=False)
    op.create_index(op.f('ix_scrm_staff_profiles_alias_name'), 'scrm_staff_profiles',
                    ['alias_name'], unique=False)
    op.create_index(op.f('ix_scrm_staff_profiles_company_group'), 'scrm_staff_profiles',
                    ['company_group'], unique=False)
    op.create_index(op.f('ix_scrm_staff_profiles_device_type'), 'scrm_staff_profiles',
                    ['device_type'], unique=False)
    op.create_index(op.f('ix_scrm_staff_profiles_enterprise_suffix'), 'scrm_staff_profiles',
                    ['enterprise_suffix'], unique=False)
    op.create_index(op.f('ix_scrm_staff_profiles_last_synced_at'), 'scrm_staff_profiles',
                    ['last_synced_at'], unique=False)
    op.create_index(op.f('ix_scrm_staff_profiles_match_status'), 'scrm_staff_profiles',
                    ['match_status'], unique=False)
    op.create_index(op.f('ix_scrm_staff_profiles_online_status'), 'scrm_staff_profiles',
                    ['online_status'], unique=False)
    op.create_index(op.f('ix_scrm_staff_profiles_person_id'), 'scrm_staff_profiles',
                    ['person_id'], unique=False)
    op.create_index(op.f('ix_scrm_staff_profiles_real_name'), 'scrm_staff_profiles',
                    ['real_name'], unique=False)
    op.create_index(op.f('ix_scrm_staff_profiles_source_config_id'), 'scrm_staff_profiles',
                    ['source_config_id'], unique=False)
    op.create_index(op.f('ix_scrm_staff_profiles_staff_unique_key'), 'scrm_staff_profiles',
                    ['staff_unique_key'], unique=True)
    op.create_index(op.f('ix_scrm_staff_profiles_user_id'), 'scrm_staff_profiles',
                    ['user_id'], unique=False)
    op.create_index('ix_scrm_staff_real_name_alias', 'scrm_staff_profiles',
                    ['real_name', 'alias_name'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_scrm_staff_real_name_alias', table_name='scrm_staff_profiles')
    op.drop_index(op.f('ix_scrm_staff_profiles_user_id'), table_name='scrm_staff_profiles')
    op.drop_index(op.f('ix_scrm_staff_profiles_staff_unique_key'), table_name='scrm_staff_profiles')
    op.drop_index(op.f('ix_scrm_staff_profiles_source_config_id'), table_name='scrm_staff_profiles')
    op.drop_index(op.f('ix_scrm_staff_profiles_real_name'), table_name='scrm_staff_profiles')
    op.drop_index(op.f('ix_scrm_staff_profiles_person_id'), table_name='scrm_staff_profiles')
    op.drop_index(op.f('ix_scrm_staff_profiles_online_status'), table_name='scrm_staff_profiles')
    op.drop_index(op.f('ix_scrm_staff_profiles_match_status'), table_name='scrm_staff_profiles')
    op.drop_index(op.f('ix_scrm_staff_profiles_last_synced_at'), table_name='scrm_staff_profiles')
    op.drop_index(op.f('ix_scrm_staff_profiles_enterprise_suffix'), table_name='scrm_staff_profiles')
    op.drop_index(op.f('ix_scrm_staff_profiles_device_type'), table_name='scrm_staff_profiles')
    op.drop_index(op.f('ix_scrm_staff_profiles_company_group'), table_name='scrm_staff_profiles')
    op.drop_index(op.f('ix_scrm_staff_profiles_alias_name'), table_name='scrm_staff_profiles')
    op.drop_index('ix_scrm_staff_company_status', table_name='scrm_staff_profiles')
    op.drop_table('scrm_staff_profiles')

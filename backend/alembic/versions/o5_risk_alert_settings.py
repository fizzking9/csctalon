"""O-5 (#276): 夜间投诉风险预警配置表 risk_alert_settings (S-P2-5)

Revision ID: o5_risk_alert_settings
Revises: b4_3_audit_changes
Create Date: 2026-10-09 13:30:00.000000

依据: 排期 §八 O-5; 模式复用 scheduler_settings(单行表 + CheckConstraint + 灌默认行)。

范围:
1. risk_alert_settings 单行配置表(id 恒为 1): 四维权重 + 回看窗口 + 流量/金额阈值
   + 三级等级线, 默认值 = #250 v1 拍板值(保证升级前后评分结果不变)。
2. 灌默认行: 服务层 get_settings 虽有"缺失补默认行"兜底, 但显式灌行让
   DBA 直接看到配置存在, 且 updated_by='system' 留痕。

downgrade: 新表 DROP 即安全回退(配置数据, 无业务数据损失;
           回退后 complaint_risk_service 读配置兜底为代码默认值, 行为不变)。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'o5_risk_alert_settings'
down_revision: Union[str, None] = 'b4_3_audit_changes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'risk_alert_settings',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        # 四维权重(百分比)
        sa.Column('weight_recharge', sa.Integer(), nullable=False, server_default='25', comment='权重: 充值多维'),
        sa.Column('weight_low_flow', sa.Integer(), nullable=False, server_default='25', comment='权重: 流量少维'),
        sa.Column('weight_contact', sa.Integer(), nullable=False, server_default='20', comment='权重: 愿沟通维'),
        sa.Column('weight_repeat', sa.Integer(), nullable=False, server_default='30', comment='权重: 同题重复维'),
        # 回看窗口(天)
        sa.Column('recharge_days', sa.Integer(), nullable=False, server_default='90', comment='充值回看窗口(天)'),
        sa.Column('contact_days', sa.Integer(), nullable=False, server_default='30', comment='接待回看窗口(天)'),
        sa.Column('complaint_days', sa.Integer(), nullable=False, server_default='90', comment='投诉工单回看窗口(天)'),
        sa.Column('external_days', sa.Integer(), nullable=False, server_default='180', comment='外部投诉回看窗口(天)'),
        # 阈值
        sa.Column('low_flow_gb', sa.Float(), nullable=False, server_default='5.0', comment='流量少阈值(GB, 最近完整月)'),
        sa.Column('recharge_high_amount', sa.Numeric(12, 2), nullable=False, server_default='500', comment='高价值充值金额阈值(元)'),
        # 等级线(严格递减)
        sa.Column('level_critical', sa.Integer(), nullable=False, server_default='70', comment='总分≥此值 → critical'),
        sa.Column('level_high', sa.Integer(), nullable=False, server_default='45', comment='总分≥此值 → high'),
        sa.Column('level_medium', sa.Integer(), nullable=False, server_default='25', comment='总分≥此值 → medium'),
        sa.Column('updated_by', sa.String(length=64), nullable=True, comment='最后修改人账号'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            'weight_recharge BETWEEN 0 AND 100 AND weight_low_flow BETWEEN 0 AND 100 '
            'AND weight_contact BETWEEN 0 AND 100 AND weight_repeat BETWEEN 0 AND 100',
            name='ck_risk_alert_settings_weights'),
        sa.CheckConstraint(
            'recharge_days BETWEEN 1 AND 365 AND contact_days BETWEEN 1 AND 365 '
            'AND complaint_days BETWEEN 1 AND 365 AND external_days BETWEEN 1 AND 365',
            name='ck_risk_alert_settings_days'),
        sa.CheckConstraint('low_flow_gb > 0', name='ck_risk_alert_settings_low_flow'),
        sa.CheckConstraint('recharge_high_amount >= 0', name='ck_risk_alert_settings_amount'),
        sa.CheckConstraint(
            'level_critical > level_high AND level_high > level_medium '
            'AND level_medium >= 0 AND level_critical <= 100',
            name='ck_risk_alert_settings_levels'),
        comment='夜间投诉风险预警评分配置(单行, #276 O-5)',
    )
    # 灌默认行(= #250 v1 拍板值, 保证升级前后评分结果不变)
    op.execute(
        "INSERT INTO risk_alert_settings "
        "(id, weight_recharge, weight_low_flow, weight_contact, weight_repeat, "
        " recharge_days, contact_days, complaint_days, external_days, "
        " low_flow_gb, recharge_high_amount, level_critical, level_high, level_medium, "
        " updated_by) "
        "VALUES (1, 25, 25, 20, 30, 90, 30, 90, 180, 5.0, 500, 70, 45, 25, 'system')"
    )


def downgrade() -> None:
    op.drop_table('risk_alert_settings')

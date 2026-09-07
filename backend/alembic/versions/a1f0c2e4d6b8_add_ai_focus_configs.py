"""add ai_focus_configs

AI 助手关注点/分析视角配置表 ai_focus_configs (A-20260904-003 v1.2 二期切片 / #26):
- 运营可增改/排序/启停"关注点/分析视角"(注入 AI Prompt user 段, 建议性参考)
- scope_key 路由(global/refund/complaint/reception) + sort_order + enabled
- seed 默认关注点(与 QUICK_QUESTIONS 对应), 保证空库也有合理默认

Revision ID: a1f0c2e4d6b8
Revises: c0a1b2c3d4e7
Create Date: 2026-09-07 15:40:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'a1f0c2e4d6b8'
down_revision: Union[str, None] = 'c0a1b2c3d4e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _seed_defaults() -> list[dict]:
    """默认关注点 seed(与 QUICK_QUESTIONS / GLOSSARY_ROUTE 对应)。"""
    return [
        {
            "code": "focus-refund", "scope_key": "refund", "title": "退款关注点",
            "sort_order": 10,
            "content": (
                "退款分析关注点: ①四路分布(A线上直退/B钱包取现/C闪猫/D手动)看结构变化, "
                "指出主路径与异常抬升; ②涉诉率与涉诉金额是风险信号, 涉诉抬升须提示; "
                "③E(退款记录报表)为对账基准, 若与四路经营差异明显(偏差>5%)要说明口径原因; "
                "④金额单位=元, 大额单笔可点名但不能编造明细。"
            ),
        },
        {
            "code": "focus-complaint", "scope_key": "complaint", "title": "投诉关注点",
            "sort_order": 10,
            "content": (
                "投诉分析关注点: ①三源(内部/商户号/外部)结构, 外部平台投诉最需重视; "
                "②登记(窗口新增)与处理(真实办结)要分开看, 处理滞后于登记说明积压恶化; "
                "③积压(未闭环存量)是核心风险, 抬升须提示; ④措辞区分'登记量/处理量/积压', 不要混用。"
            ),
        },
        {
            "code": "focus-reception", "scope_key": "reception", "title": "接待关注点",
            "sort_order": 10,
            "content": (
                "接待分析关注点: ①总体接待量与服务设备数(SN去重)看覆盖面; "
                "②六个专项(咨询/套餐/退款处理/退款挽回/内部投诉/外部投诉)的会话与设备规模, "
                "指出量大的专项与负责人(lead); ③环比(同比前移等长窗口)变化要解释可能原因; "
                "④接待量含智能助手处理(cs_record), 口径与'人工工单'不同勿混。"
            ),
        },
        {
            "code": "focus-global", "scope_key": "global", "title": "通用经营视角",
            "sort_order": 1,
            "content": (
                "通用经营分析视角: ①先给结论再给数据支撑, 用标题/要点/小表格组织; "
                "②对比(同比/环比)给出升降幅与方向; ③有异常(突变/为0/负增长)优先说明; "
                "④数字一律引用给定数据, 无数据的写'暂无'; 金额单位=元。"
            ),
        },
    ]


def upgrade() -> None:
    op.create_table(
        'ai_focus_configs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('code', sa.String(length=32), nullable=False, comment='唯一标识(如 focus-refund-litigation)'),
        sa.Column('scope_key', sa.String(length=16), nullable=False, server_default='global', comment='适用范围: global/refund/complaint/reception'),
        sa.Column('title', sa.String(length=64), nullable=False, comment='标题(如 退款关注点)'),
        sa.Column('content', sa.Text(), nullable=False, comment='关注点正文(注入 Prompt 的指导文本)'),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0', comment='排序(小在前)'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true'), comment='启用; false=停用不注入'),
        sa.Column('created_by', sa.String(length=64), nullable=True, comment='创建人'),
        sa.Column('updated_by', sa.String(length=64), nullable=True, comment='最后更新人'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_ai_focus_configs_code'),
        comment='AI 助手关注点/分析视角配置(运营可增改/排序/启停)',
    )
    op.create_index('ix_ai_focus_configs_scope_key', 'ai_focus_configs', ['scope_key'])
    # seed 默认关注点(幂等: 仅空表插入)
    conn = op.get_bind()
    existing = conn.execute(sa.text("SELECT count(*) FROM ai_focus_configs")).scalar()
    if not existing:
        for row in _seed_defaults():
            conn.execute(sa.text(
                "INSERT INTO ai_focus_configs (code, scope_key, title, content, sort_order, enabled, created_by) "
                "VALUES (:code, :scope_key, :title, :content, :sort_order, true, 'seed')"
            ), row)


def downgrade() -> None:
    op.drop_index('ix_ai_focus_configs_scope_key', table_name='ai_focus_configs')
    op.drop_table('ai_focus_configs')

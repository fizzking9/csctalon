"""add doc_sync_sources

共享文档在线采集源配置表 doc_sync_sources (#33, A-20260907-002):
- 每行 = 一份共享文档(腾讯 docs.qq.com / 金山 kdocs.cn)的采集配置
- 用登录态(BROWSER_STATE_DIR/tencent_doc.json 或 kdocs.json)+ 官方 UI 导出 xlsx,
  再复用 complaint_sync / manual_refund_sync 既有入库管线(见 shared_doc_sync_service)
- target_type: complaint=投诉(315+卡商2 sheet) / manual_refund=商户号退款
- sheet_rules: sheet→目标表映射, 如
  [{'sheet':'监督局-315投诉','target':'external_complaint'},
   {'sheet':'卡商群投诉','target':'card_complaint'}]
- ⚠️ 不含任何登录态/凭证/敏感信息(登录态文件路径从 settings.BROWSER_STATE_DIR 读取)

Revision ID: 47cadbf38a6f
Revises: a1f0c2e4d6b8
Create Date: 2026-09-08 12:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = '47cadbf38a6f'
down_revision: Union[str, None] = 'a1f0c2e4d6b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 默认 seed 配置(幂等, INSERT ... ON CONFLICT DO NOTHING)。
# 不含敏感信息: 仅平台/链接/文档名/目标类型/sheet 映射, 登录态文件名走默认值。
_DEFAULT_SOURCES: list[dict] = [
    {
        "platform": "tencent",
        "doc_url": "https://docs.qq.com/sheet/DZWF4WVZJcWxGVUNW",
        "doc_name": "315投诉处理登记表新",
        "target_type": "complaint",
        "sheet_rules": [
            {"sheet": "监督局-315投诉", "target": "external_complaint"},
            {"sheet": "卡商群投诉", "target": "card_complaint"},
        ],
    },
    {
        "platform": "kdocs",
        "doc_url": "https://www.kdocs.cn/l/cbZIHtt6obyf",
        "doc_name": "商户号手机退款处理登记",
        "target_type": "manual_refund",
        "sheet_rules": [],
    },
]


def upgrade() -> None:
    op.create_table(
        'doc_sync_sources',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('platform', sa.String(length=32), nullable=False, comment='平台: tencent=腾讯文档 / kdocs=金山文档'),
        sa.Column('doc_url', sa.String(length=512), nullable=False, comment='共享文档链接'),
        sa.Column('doc_name', sa.String(length=128), nullable=True, comment='文档名(展示)'),
        sa.Column('target_type', sa.String(length=32), nullable=False, comment='目标业务类型: complaint=投诉(315+卡商2sheet) / manual_refund=商户号退款'),
        sa.Column('sheet_rules', JSONB(), nullable=True, comment="sheet→data_type映射, 如 [{'sheet':'监督局-315投诉','target':'external_complaint'},{'sheet':'卡商群投诉','target':'card_complaint'}]"),
        sa.Column('state_file', sa.String(length=128), nullable=True, comment='登录态文件名(默认 tencent_doc.json/kdocs.json)'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true'), comment='启用; false=停用不采集'),
        sa.Column('sync_interval_minutes', sa.Integer(), nullable=False, server_default='60', comment='轮询间隔(分钟)'),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True, comment='最近同步时间(UTC)'),
        sa.Column('last_sync_status', sa.String(length=32), nullable=True, comment='最近同步状态 success/partial/failed'),
        sa.Column('last_sync_summary', JSONB(), nullable=True, comment='最近同步统计(各sheet行数/增改等)'),
        sa.Column('last_error', sa.Text(), nullable=True, comment='最近一次同步错误信息'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('platform', 'doc_url', name='uq_doc_sync_sources_platform_doc_url'),
        comment='共享文档在线采集源配置(每行一份共享文档)',
    )
    op.create_index('ix_doc_sync_sources_platform', 'doc_sync_sources', ['platform'])
    op.create_index('ix_doc_sync_sources_target_type', 'doc_sync_sources', ['target_type'])

    # seed 默认配置(幂等: 同 platform+doc_url 已存在则跳过)。
    # sheet_rules: python 对象 + bindparam(type_=JSONB) 作为绑定参数——
    # SQLAlchemy 编译期按 jsonb 类型序列化, psycopg2 正确写入 JSONB 数组。
    # (禁止 type_coerce 表达式对象直接作绑定参数: psycopg2 无法适配)
    conn = op.get_bind()
    for row in _DEFAULT_SOURCES:
        conn.execute(
            sa.text(
                "INSERT INTO doc_sync_sources "
                "(platform, doc_url, doc_name, target_type, sheet_rules, state_file, enabled, sync_interval_minutes) "
                "VALUES (:platform, :doc_url, :doc_name, :target_type, :sheet_rules, NULL, true, 60) "
                "ON CONFLICT (platform, doc_url) DO NOTHING"
            ).bindparams(sa.bindparam("sheet_rules", type_=JSONB)),
            {
                "platform": row["platform"],
                "doc_url": row["doc_url"],
                "doc_name": row["doc_name"],
                "target_type": row["target_type"],
                "sheet_rules": row["sheet_rules"] or [],
            },
        )


def downgrade() -> None:
    op.drop_index('ix_doc_sync_sources_target_type', table_name='doc_sync_sources')
    op.drop_index('ix_doc_sync_sources_platform', table_name='doc_sync_sources')
    op.drop_table('doc_sync_sources')

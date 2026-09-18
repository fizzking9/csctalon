"""add doc_sync_login_logs (工单 #149)

登录历史表: 记录每次页面内扫码登录的最终结果(成功/失败), 供运维审计
「谁在何时用什么账号登录」, 定位登录态被覆盖或失效的时序。

Revision ID: dc20f480c17e
Revises: x9y0z1a2b3c4
Create Date: 2026-09-18 16:25:29.463158
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'dc20f480c17e'
down_revision: Union[str, None] = 'x9y0z1a2b3c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 登录历史表: 一行 = 一次登录会话的最终结果
    op.create_table(
        'doc_sync_login_logs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            'platform', sa.String(length=32), nullable=False,
            comment='平台: tencent=腾讯 / kdocs=金山',
        ),
        sa.Column(
            'source_id', sa.Integer(), nullable=True,
            comment='触发登录的采集源 id(可为空: CLI 上传登录态)',
        ),
        sa.Column('uid', sa.String(length=128), nullable=True, comment='登录账号(脱敏, 取不到为空)'),
        sa.Column(
            'cookies_count', sa.Integer(), nullable=False,
            comment='保存成功后的 cookie 条数(失败为 0)',
        ),
        sa.Column(
            'result', sa.String(length=16), nullable=False,
            comment='success=登录态已保存 / failed=校验或保存失败',
        ),
        sa.Column('error', sa.Text(), nullable=True, comment='失败原因(成功为空)'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_doc_sync_login_logs_platform'), 'doc_sync_login_logs', ['platform'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_doc_sync_login_logs_platform'), table_name='doc_sync_login_logs')
    op.drop_table('doc_sync_login_logs')
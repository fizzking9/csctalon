"""add options(JSONB) to sync_source_configs + BMS data_type expand to SCRM lengths

为支持 SCRM 两种抓取模式(OpenAPI / Playwright 网页验证码登录) + 各种选择器/扩展配置,
给 sync_source_configs 加 options(JSONB) 列,同时把 data_type 长度从 32 扩到 64
(兼容 SCRM 新数据源名 如 scrm_session_transfers)。

Revision ID: a1b2c3d4e5f7
Revises: z9y8x7w6v5u4
Create Date: 2026-08-05 14:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, None] = "z9y8x7w6v5u4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 先扩 data_type 长度到 64(兼容 scrm_session_transfers 等长名字)
    op.alter_column(
        "sync_source_configs",
        "data_type",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
        existing_comment="refund/ticket/wallet/device_combo 等数据源类型",
        comment="BMS/SCRM 数据源类型(如 refund / scrm_operator / scrm_sessions 等)",
    )
    # 2. username/password comment 升级为多模式说明(只改 PostgreSQL COMMENT,不碰列长度)
    op.alter_column(
        "sync_source_configs",
        "username",
        existing_type=sa.Text(),
        existing_nullable=True,
        comment="加密用户名: OpenAPI=ClientId / Playwright=登录账号(Fernet 加密)",
    )
    op.alter_column(
        "sync_source_configs",
        "password",
        existing_type=sa.Text(),
        existing_nullable=True,
        comment="加密密码: OpenAPI=ClientSecret / Playwright=登录密码(Fernet 加密)",
    )
    # 3. 加 options(JSONB) 扩展字段(抓取模式 / 验证码类型 / 各种选择器 等)
    op.add_column(
        "sync_source_configs",
        sa.Column(
            "options",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="扩展参数:抓取模式/验证码类型/页面选择器等",
        ),
    )


def downgrade() -> None:
    # 回滚 3
    op.drop_column("sync_source_configs", "options")
    # 回滚 2
    op.alter_column(
        "sync_source_configs",
        "password",
        existing_type=sa.Text(),
        existing_nullable=True,
        comment="BMS 密码(Fernet 加密)",
    )
    op.alter_column(
        "sync_source_configs",
        "username",
        existing_type=sa.Text(),
        existing_nullable=True,
        comment="BMS 账号(Fernet 加密)",
    )
    # 回滚 1
    op.alter_column(
        "sync_source_configs",
        "data_type",
        existing_type=sa.String(length=64),
        type_=sa.String(length=32),
        existing_nullable=False,
        existing_comment="BMS/SCRM 数据源类型(如 refund / scrm_operator / scrm_sessions 等)",
        comment="refund/ticket/wallet/device_combo",
    )

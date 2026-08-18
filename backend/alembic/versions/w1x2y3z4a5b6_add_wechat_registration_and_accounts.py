"""add wechat registration & account tables, extend users with wechat_openid/unionid

- 新增 wechat_registrations 表 (扫码注册待审批记录)
- 新增 wechat_accounts 表 (user<->openid 绑定关系)
- users 新增 wechat_openid / wechat_unionid 列
- 所有改动均为幂等 (列已存在则跳过, 表已存在则跳过)

Revision ID: w1x2y3z4a5b6
Revises: c0d1e2f3a4b5
Create Date: 2026-08-19 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "w1x2y3z4a5b6"
down_revision: Union[str, None] = "c0d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table: str) -> bool:
    insp = sa.inspect(bind)
    return insp.has_table(table)


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # 1) users 表增加 wechat_openid / wechat_unionid
    if not _has_column(bind, "users", "wechat_openid"):
        op.add_column("users", sa.Column("wechat_openid", sa.String(length=64), nullable=True))
        op.create_index("ix_users_wechat_openid", "users", ["wechat_openid"], unique=False)
    if not _has_column(bind, "users", "wechat_unionid"):
        op.add_column("users", sa.Column("wechat_unionid", sa.String(length=64), nullable=True))
        op.create_index("ix_users_wechat_unionid", "users", ["wechat_unionid"], unique=False)

    # 2) wechat_registrations 表
    if not _has_table(bind, "wechat_registrations"):
        op.create_table(
            "wechat_registrations",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("openid", sa.String(length=64), nullable=False),
            sa.Column("unionid", sa.String(length=64), nullable=True),
            sa.Column("nickname", sa.String(length=64), nullable=True),
            sa.Column("avatar", sa.String(length=512), nullable=True),
            sa.Column("source", sa.String(length=16), server_default="miniapp", nullable=False),
            sa.Column("desired_username", sa.String(length=64), nullable=True),
            sa.Column("desired_display_name", sa.String(length=64), nullable=True),
            sa.Column("desired_role", sa.String(length=32), nullable=True),
            sa.Column("desired_group", sa.String(length=64), nullable=True),
            sa.Column("qw_name", sa.String(length=64), nullable=True),
            sa.Column("remark", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
            sa.Column("reviewed_at", sa.DateTime(), nullable=True),
            sa.Column("reviewed_by", sa.Integer(), nullable=True),
            sa.Column("reject_reason", sa.Text(), nullable=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("subscribed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        )
        op.create_index("ix_wr_openid", "wechat_registrations", ["openid"])
        op.create_index("ix_wr_unionid", "wechat_registrations", ["unionid"])
        op.create_index("ix_wr_status", "wechat_registrations", ["status"])
        op.create_index("ix_wr_user_id", "wechat_registrations", ["user_id"])
        op.create_foreign_key("fk_wr_reviewed_by", "wechat_registrations", "users", ["reviewed_by"], ["id"])
        op.create_foreign_key("fk_wr_user_id", "wechat_registrations", "users", ["user_id"], ["id"])

    # 3) wechat_accounts 表
    if not _has_table(bind, "wechat_accounts"):
        op.create_table(
            "wechat_accounts",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("openid", sa.String(length=64), nullable=False),
            sa.Column("unionid", sa.String(length=64), nullable=True),
            sa.Column("source", sa.String(length=16), server_default="miniapp", nullable=False),
            sa.Column("bound_at", sa.DateTime(), nullable=False),
            sa.Column("is_primary", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        )
        op.create_index("ix_wa_user_id", "wechat_accounts", ["user_id"])
        op.create_index("ix_wa_openid", "wechat_accounts", ["openid"])
        op.create_index("ix_wa_unionid", "wechat_accounts", ["unionid"])
        op.create_foreign_key("fk_wa_user_id", "wechat_accounts", "users", ["user_id"], ["id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "wechat_accounts"):
        op.drop_table("wechat_accounts")
    if _has_table(bind, "wechat_registrations"):
        op.drop_table("wechat_registrations")
    if _has_column(bind, "users", "wechat_openid"):
        op.drop_index("ix_users_wechat_openid", table_name="users")
        op.drop_column("users", "wechat_openid")
    if _has_column(bind, "users", "wechat_unionid"):
        op.drop_index("ix_users_wechat_unionid", table_name="users")
        op.drop_column("users", "wechat_unionid")

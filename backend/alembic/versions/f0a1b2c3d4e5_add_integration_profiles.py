"""add integration_profiles table (#44)

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-09-10 19:00:00.000000

背景(#44, A-20260910-002 LOCKED v1.0):
  调用方(WorkBuddy 等)接入需要「提示词 + 字段规范 + 接口说明」一整套物料,
  此前散落在代码常量与 Markdown 文档里, 改一次要发版。
  本表把配置包模板 DB 化, 管理员可在「接入中心」直接维护。

新增表 `integration_profiles`:
  code         String(48)  唯一   模板标识
  name         String(64)         显示名
  scene        String(24)  索引   场景 push/pull/manual_refund
  content_md   Text               Markdown 模板(含 {{变量}})
  variables    JSONB              变量声明 [{key,label,required}]
  enabled      Boolean            停用后不在列表出现
  sort_order   Integer            排序
  created_by / updated_by / created_at / updated_at

种子: 3 个默认场景(投诉推送/手动退款推送/经营日报拉取), 内容取
services/integration_profile_service._SEED_PROFILES 常量(单一事实来源)。
**已存在则跳过**, 不覆盖用户改动(遵循审计项 C-P2-2)。

⚠️ 纯新增表, 对既有表/接口零影响。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'f0a1b2c3d4e5'
down_revision: Union[str, None] = 'e9f0a1b2c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = 'integration_profiles'


def _table_exists(table_name: str) -> bool:
    """表存在性检查(幂等升级规范: create_all 环境可能已建表)。"""
    bind = op.get_bind()
    sql = sa.text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=:t"
    )
    return bind.execute(sql, {"t": table_name}).scalar() is not None


def upgrade() -> None:
    if not _table_exists(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column('code', sa.String(length=48), nullable=False, comment='唯一标识(如 workbuddy-complaint-push)'),
            sa.Column('name', sa.String(length=64), nullable=False, comment='显示名'),
            sa.Column('scene', sa.String(length=24), nullable=False, comment='场景: push/pull/manual_refund'),
            sa.Column('content_md', sa.Text(), nullable=False, comment='Markdown 模板(含 {{变量}})'),
            sa.Column('variables', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                      comment='变量声明: [{"key":"doc_url","label":"共享文档地址","required":true}]'),
            sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true'), comment='停用后不在列表出现'),
            sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0'), comment='排序(小在前)'),
            sa.Column('created_by', sa.String(length=64), nullable=True, comment='创建人'),
            sa.Column('updated_by', sa.String(length=64), nullable=True, comment='最后更新人'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_integration_profiles_code', _TABLE, ['code'], unique=True)
        op.create_index('ix_integration_profiles_scene', _TABLE, ['scene'], unique=False)

    _seed_default_profiles()


def _seed_default_profiles() -> None:
    """幂等灌入默认模板(**已存在则跳过, 不覆盖用户改动**)。

    内容来自服务层常量, 避免 migration 与服务层两份文本漂移。
    """
    try:
        from app.services.integration_profile_service import _SEED_PROFILES
    except Exception:  # noqa: BLE001  migration 环境无 app 包时跳过 seed
        return

    bind = op.get_bind()
    for item in _SEED_PROFILES:
        exists = bind.execute(
            sa.text(f"SELECT 1 FROM {_TABLE} WHERE code = :c"), {"c": item["code"]}
        ).scalar()
        if exists:
            continue
        bind.execute(
            sa.text(
                f"INSERT INTO {_TABLE} "
                "(code, name, scene, content_md, variables, enabled, sort_order, created_by, updated_by) "
                "VALUES (:code, :name, :scene, :content_md, CAST(:variables AS jsonb), true, :sort_order, 'system', 'system')"
            ),
            {
                "code": item["code"],
                "name": item["name"],
                "scene": item["scene"],
                "content_md": item["content_md"],
                "variables": __import__("json").dumps(item.get("variables") or [], ensure_ascii=False),
                "sort_order": item.get("sort_order", 0),
            },
        )


def downgrade() -> None:
    if _table_exists(_TABLE):
        op.drop_index('ix_integration_profiles_scene', table_name=_TABLE)
        op.drop_index('ix_integration_profiles_code', table_name=_TABLE)
        op.drop_table(_TABLE)

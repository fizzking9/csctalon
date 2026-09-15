"""llm_providers + llm_call_logs: LLM 多 Provider 框架 (工单 #108)

Revision ID: a8b9c0d1e2f3
Revises: b3c4d5e6f7a8
Create Date: 2026-09-15 22:30:00.000000

设计:
1. llm_providers 表: 多 Provider 接入(本地 Talon 网关 / 云端 DeepSeek)
   - API key Fernet 加密入库(同 sync_source_configs)
   - priority 升序遍历, transient 错误降级下一个
   - healthy/失败计数/最近失败时间: 连续失败 N 次自动降级
2. llm_call_logs 表: 每次调用一行审计(成功/失败均记), 不打敏感数据
3. seed 3 条默认 Provider:
   - talon-gmk-qwen3-coder-30b (主力, priority=10): 本地 Talon 网关 qwen3-coder:30b
   - talon-gmk-qwen2.5-7b (兜底, priority=20): 本地 Talon 网关 qwen2.5:7b
   - deepseek-v4-flash (云端兜底, priority=30, enabled=false 默认禁用):
     云端 DeepSeek, 防止本地节点全挂时业务中断(运维按需启用)
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'a8b9c0d1e2f3'
down_revision: Union[str, None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _encrypt(plaintext: str | None) -> str | None:
    """Fernet 加密; 失败时返回 None(等同未配 key)"""
    if not plaintext:
        return None
    try:
        from app.core.crypto import encrypt_secret
        return encrypt_secret(plaintext)
    except Exception:
        return None


def upgrade() -> None:
    # ── llm_providers ──
    op.create_table(
        'llm_providers',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('base_url', sa.String(length=256), nullable=False),
        sa.Column('model', sa.String(length=128), nullable=False),
        sa.Column('api_key_cipher', sa.Text(), nullable=True),
        sa.Column('api_key_prefix', sa.String(length=16), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('healthy', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('thinking', sa.String(length=16), nullable=False, server_default='disabled'),
        sa.Column('timeout_s', sa.Float(), nullable=False, server_default='120.0'),
        sa.Column('retry_max', sa.Integer(), nullable=False, server_default='2'),
        sa.Column('fail_threshold', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('fail_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_fail_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )
    op.create_index('ix_llm_providers_code', 'llm_providers', ['code'])
    op.create_index('ix_llm_providers_priority', 'llm_providers', ['priority'])
    op.create_index('ix_llm_providers_enabled', 'llm_providers', ['enabled'])
    op.create_index('ix_llm_providers_healthy', 'llm_providers', ['healthy'])

    # ── llm_call_logs ──
    op.create_table(
        'llm_call_logs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('provider_id', sa.Integer(), nullable=True),
        sa.Column('provider_code', sa.String(length=64), nullable=True),
        sa.Column('model', sa.String(length=128), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('prompt_chars', sa.Integer(), nullable=True),
        sa.Column('out_chars', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('source', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_llm_call_logs_provider_id', 'llm_call_logs', ['provider_id'])
    op.create_index('ix_llm_call_logs_status', 'llm_call_logs', ['status'])
    op.create_index('ix_llm_call_logs_source', 'llm_call_logs', ['source'])
    op.create_index('ix_llm_call_logs_created_at', 'llm_call_logs', ['created_at'])

    # ── seed 3 条默认 Provider ──
    # ⚠️ 安全: 不在 migration 里硬编码任何 API key(gitleaks 会拦)。
    #    key 由运维在「LLM 模型接入」管理界面手动填(POST /llm-providers);
    #    seed 仅写 base_url/model/priority/note, api_key_cipher 留 NULL。
    # 注: 读取 .env 的 LLM_API_KEY/LLM_BASE_URL/LLM_MODEL 作为 DeepSeek seed 的
    #    默认值(运维在管理界面改); 其它 Provider 的 key 一律由运维手动填。
    bind = op.get_bind()
    deepseek_key = ''
    deepseek_url = 'https://api.deepseek.com'
    deepseek_model = 'deepseek-v4-flash'
    try:
        from app.core.config import settings  # type: ignore
        deepseek_key = settings.LLM_API_KEY or ''
        if settings.LLM_BASE_URL:
            deepseek_url = settings.LLM_BASE_URL
        if settings.LLM_MODEL:
            deepseek_model = settings.LLM_MODEL
    except Exception:
        pass

    talon_url = 'http://192.168.10.21:4080/v1'

    # 1) 主力: Talon gmk-evo qwen3-coder:30b
    op.execute(sa.text(
        "INSERT INTO llm_providers (code, name, base_url, model, api_key_cipher, api_key_prefix, "
        "priority, enabled, healthy, thinking, timeout_s, retry_max, fail_threshold, fail_count, note, created_by, updated_by) "
        "VALUES (:code, :name, :base_url, :model, NULL, NULL, "
        "10, true, true, 'disabled', 120.0, 2, 3, 0, :note, 'system', 'system')"
    ).bindparams(
        code='talon-gmk-qwen3-coder-30b',
        name='Talon·GMK·qwen3-coder:30b',
        base_url=talon_url,
        model='qwen3-coder:30b',
        note='主力模型: Talon 网关 gmk-evo(192.168.10.21:11434) qwen3-coder:30b 17GB; Radeon 8060S 64GB; API key 需运维在管理界面填',
    ))

    # 2) 兜底: Talon qwen2.5:7b(轻量, 主力挂时降级用)
    op.execute(sa.text(
        "INSERT INTO llm_providers (code, name, base_url, model, api_key_cipher, api_key_prefix, "
        "priority, enabled, healthy, thinking, timeout_s, retry_max, fail_threshold, fail_count, note, created_by, updated_by) "
        "VALUES (:code, :name, :base_url, :model, NULL, NULL, "
        "20, true, true, 'disabled', 90.0, 2, 3, 0, :note, 'system', 'system')"
    ).bindparams(
        code='talon-gmk-qwen2.5-7b',
        name='Talon·GMK·qwen2.5:7b',
        base_url=talon_url,
        model='qwen2.5:7b',
        note='兜底模型: Talon 网关 qwen2.5:7b; 主力挂或并发高时降级用; API key 需运维在管理界面填',
    ))

    # 3) 云端兜底: DeepSeek(默认禁用, 运维按需启用)
    op.execute(sa.text(
        "INSERT INTO llm_providers (code, name, base_url, model, api_key_cipher, api_key_prefix, "
        "priority, enabled, healthy, thinking, timeout_s, retry_max, fail_threshold, fail_count, note, created_by, updated_by) "
        "VALUES (:code, :name, :base_url, :model, :key_cipher, :key_prefix, "
        "30, :enabled, true, 'disabled', 120.0, 2, 3, 0, :note, 'system', 'system')"
    ).bindparams(
        code='deepseek-v4-flash',
        name='DeepSeek·v4-flash',
        base_url=deepseek_url,
        model=deepseek_model,
        key_cipher=_encrypt(deepseek_key) if deepseek_key else None,
        key_prefix=(deepseek_key[:6] or None),
        enabled=False,  # 默认禁用; 运维按需启用
        note='云端兜底: DeepSeek 官方 API; 默认禁用, 本地节点全挂时运维启用',
    ))


def downgrade() -> None:
    op.drop_table('llm_call_logs')
    op.drop_table('llm_providers')

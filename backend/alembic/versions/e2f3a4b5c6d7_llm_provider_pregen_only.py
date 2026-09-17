"""feat: llm_providers 新增 pregen_only + 落 122b 预生成专用 provider(初始 disabled) (工单 #131)

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-09-17 00:20:00.000000

设计:
- pregen_only=true 的 provider 不进实时默认链(用户问答/时间解析), 不参与启动 warmup,
  仅在调用方按 code 显式指定时置顶(报告预生成); 用于 qwen3.5:122b 这类慢而重的模型。
- 同时插入 122b provider 行, **初始 enabled=false**:
  模型 81GB 拉取约 4h, 拉取完成前 ollama 对缺失模型返 404(permanent, 不降级),
  若启用会让每轮同步触发的预生成硬失败。拉完冒烟通过后由运维手动 enable + 绑模板。
- base_url 走 talon-api(4080) 模型亲和调度: 122b 只装在 gmk-evo 节点, 会自动路由过去。
- timeout_s=600(预生成放宽), retry_max=0(单次失败即降级常规链, 避免 2×10 分钟等待)。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'llm_providers',
        sa.Column(
            'pregen_only',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # 122b 预生成专用 provider(初始禁用, 待模型拉取验证后启用)
    op.execute("""
        INSERT INTO llm_providers
            (code, name, base_url, model, priority, enabled, pregen_only, healthy,
             thinking, timeout_s, retry_max, fail_threshold, fail_count,
             note, created_by, updated_by, created_at, updated_at)
        VALUES
            ('talon-gmk-qwen3.5-122b',
             'Talon·GMK·qwen3.5:122b(A10B/预生成专用)',
             'http://192.168.10.21:4080/v1',
             'qwen3.5:122b',
             3, FALSE, TRUE, TRUE,
             'disabled', 600.0, 0, 3, 0,
             '#131 122B MoE A10B q4_K_M(81GB), 仅 gmk-evo 124GB 节点; 专供报告预生成, 拉取验证后启用',
             'system', 'system', now(), now())
        ON CONFLICT (code) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM llm_providers WHERE code = 'talon-gmk-qwen3.5-122b'")
    op.drop_column('llm_providers', 'pregen_only')

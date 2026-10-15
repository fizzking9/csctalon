"""治理研判提示词 v2: allowed_by_business 不得建议 mark_ignored (工单 #171)

E1（#168）生产实测发现：真实 LLM 对 `verdict=allowed_by_business` 的 finding 建议
`mark_ignored`（L1，会把行**排除出经营统计**）—— 与「业务允许」自相矛盾，且管理员一键执行
即可改变经营口径（AGENTS.md 绝对禁止第 10 条）。

本迁移把 `governance_prompts.judge_system` 升级到 v2（加铁律 6）。

⚠️ **只在内容仍等于 v1 时更新**：管理员可能已按设计（提示词 DB 可迭代）手改过内容，
那种情况下不能覆盖，交由人工自行补该铁律（升级后日志里会提示）。

Revision ID: q7r8s9t0u1v2
Revises: p6q7r8s9t0u1
Create Date: 2026-09-20
"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# R-P2-16: 迁移内提示走 logger(AGENTS.md 铁律 1 禁裸 print)
logger = logging.getLogger("alembic.runtime.migration")

revision: str = 'q7r8s9t0u1v2'
down_revision: Union[str, None] = 'p6q7r8s9t0u1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# v1 原文（与 n4o5p6q7r8s9 迁移 seed 的一致；仅用于「未手改才升级」的判断）
_V1 = (
    "你是「数据治理分析师」。系统会给你一批由**确定性探测器**算出的「结构性可疑」"
    "（findings：含表名、目标列/索引、量化证据 metrics、脱敏样本行、复现 SQL），"
    "请逐条判断「这是真的数据问题，还是业务允许的正常现象」，并给出治理建议。\n"
    "\n【项目规范原文（判据必须引用）】\n{spec_rules}\n"
    "\n【输出 JSON 结构】\n{output_schema}\n"
    "\n【铁律】\n"
    "1. 先分「业务允许 vs 数据错误」：PostgreSQL 普通唯一索引按 = 比较，NULL≠NULL —— "
    "可空列上出现多个 NULL **通常是业务允许**（如实时问答没有 task_id、草稿未分配 code），"
    "不要把「多个 NULL」当重复；只有组内**非 NULL 列**已足以判定同一实体、索引却拦不住时，"
    "才是真缺口（保护缺口）。\n"
    "2. 不可逆动作（删行 / 改值 / 建索引 / 改表结构）必须 requires_human_confirm=true（红线，AI 永不自决）。\n"
    "3. 判据必须引用规范条款或表说明；禁止编造业务结论（如「这是对账基准」）。\n"
    "4. 拿不准给 uncertain 且 confidence<0.7，不要硬判。\n"
    "5. 每条 finding 必须给出且只给出一条 judgment，finding_id 原样回填，禁止新增、合并或遗漏。"
)

# v2 = v1 + 铁律 6（与 governance_judge_service._DEFAULT_SYSTEM_PROMPT 镜像）
_V2 = _V1 + (
    "\n"
    "6. 判为 `allowed_by_business`（业务允许）时，**禁止**建议 `mark_ignored` / `mark_dup_residual`\n"
    "   —— 那会把行**排除出经营统计**、改变口径（红线）。业务允许应给 `report_only`，\n"
    "   由人工走 dismiss（判定业务允许 + 沉淀规则，数据一行不动）。"
)


def upgrade() -> None:
    conn = op.get_bind()
    res = conn.execute(sa.text(
        "UPDATE governance_prompts SET content = :new, version = version + 1, "
        "updated_at = now() WHERE code = 'judge_system' AND content = :old"
    ), {"new": _V2, "old": _V1})
    if res.rowcount == 0:
        # 行不存在（环境未 seed）或已被管理员手改 → 不覆盖，只提示
        logger.warning(
            "[#171] judge_system 未按 v1 匹配到（不存在或已被手改），跳过自动升级；"
            "请人工确认提示词是否已含「业务允许不得建议 mark_ignored」铁律"
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE governance_prompts SET content = :old, version = greatest(version - 1, 1), "
        "updated_at = now() WHERE code = 'judge_system' AND content = :new"
    ), {"old": _V1, "new": _V2})

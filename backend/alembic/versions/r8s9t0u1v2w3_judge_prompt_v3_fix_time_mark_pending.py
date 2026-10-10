"""治理研判提示词 v3: mark_pending / fix_time 动作 + 禁止编造修正值 (工单 #169)

E2（#169）新增两个动作，提示词必须同步（否则 AI 不知道它们存在，或不知道怎么用）：
  · `mark_pending`（L1）—— 标「待人工核对」且**仍计入经营统计**（不改口径）；
    同时点明与 `mark_ignored`/`mark_dup_residual`（会排除出统计）的区别。
  · `fix_time`（L2）—— 修日期值：必须人工确认 + 只在**上下文足以推断正确值**时才给，
    并在 `params.updates` 给出 `[{pk, col, old, new}]`；依据不足给 uncertain，禁止编造。

本迁移把 `governance_prompts.judge_system` 从 v2 升级到 v3。

⚠️ **只在内容仍等于 v2 时更新**：管理员可能已按设计（提示词 DB 可迭代）手改过内容，
那种情况下不能覆盖，交由人工自行补这两条（升级后日志里会提示）。

Revision ID: r8s9t0u1v2w3
Revises: q7r8s9t0u1v2
Create Date: 2026-09-20
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'r8s9t0u1v2w3'
down_revision: Union[str, None] = 'q7r8s9t0u1v2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# v2 原文（与 q7r8s9t0u1v2 迁移写入的一致；仅用于「未手改才升级」的判断）
_V2 = (
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
    "\n"
    "6. 判为 `allowed_by_business`（业务允许）时，**禁止**建议 `mark_ignored` / `mark_dup_residual`\n"
    "   —— 那会把行**排除出经营统计**、改变口径（红线）。业务允许应给 `report_only`，\n"
    "   由人工走 dismiss（判定业务允许 + 沉淀规则，数据一行不动）。"
)

# v3 = v2 的 铁律 6 补一句（mark_pending 不改口径）+ 新增 铁律 7（fix_time）
#      与 governance_judge_service._DEFAULT_SYSTEM_PROMPT 镜像
_V3 = (
    _V2.replace(
        "   由人工走 dismiss（判定业务允许 + 沉淀规则，数据一行不动）。",
        "   由人工走 dismiss（判定业务允许 + 沉淀规则，数据一行不动）。\n"
        "   需要「标出来给人核对、但不改口径」时用 `mark_pending`（行**仍计入**经营统计）。",
    )
    + "\n"
    "7. `fix_time`（改日期值）属**不可逆**动作：必须 requires_human_confirm=true，且**只在上下文\n"
    "   足以推断正确值**时才给，并在 `params.updates` 写明 `[{{pk, col, old, new}}]`（pk 取样本的\n"
    "   `_id`，old 原样回填库中现值）。依据不足一律 `uncertain` + confidence<0.7，**禁止编造**\n"
    "   —— 改错值比不改更糟（人工确认时会核对 old 是否与库中一致，不一致直接拒绝执行）。\n"
    "   ⚠️ 本串会被 `str.format` 渲染：提示词里的**字面花括号必须成对写**（两个左花括号表示一个），\n"
    "      写成单只会让 format 抛错、`_safe_format` 静默退回**未替换**的模板，\n"
    "      导致 AI 根本拿不到判据段 —— 2026-09-20 E2 实测踩过。"
)


def upgrade() -> None:
    conn = op.get_bind()
    res = conn.execute(sa.text(
        "UPDATE governance_prompts SET content = :new, version = version + 1, "
        "updated_at = now() WHERE code = 'judge_system' AND content = :old"
    ), {"new": _V3, "old": _V2})
    if res.rowcount == 0:
        # 行不存在（环境未 seed）或已被管理员手改 → 不覆盖，只提示
        print("[#169] judge_system 未按 v2 匹配到（不存在或已被手改），跳过自动升级；"
              "请人工确认提示词是否已含「mark_pending 不改口径」与「fix_time 禁止编造修正值」")


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE governance_prompts SET content = :old, version = greatest(version - 1, 1), "
        "updated_at = now() WHERE code = 'judge_system' AND content = :new"
    ), {"old": _V2, "new": _V3})

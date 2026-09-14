# -*- coding: utf-8 -*-
"""AI 接待纳入效率值核算 —— 口径契约测试（2026-09-14 用户要求）

## 需求原文
「能不能 在效率值的核算表里面 把AI接待的 也纳入统计，这样可以比对出 AI的效率值，
  计算办法 和专项人员一致。」

## 本文件锁定的核心不变式
1. **同一张权重表** —— AI 与真人必须用同一套 `WEIGHT`/`CAT_DEFAULT_WEIGHT`，
   否则两者效率值**不可比**，需求（比对 AI 效率）就失去意义。
2. **AI 不参与真人排名** —— `rank` 必须为 None（AI 不是人，
   排进榜会挤掉真人名次，且"排名"对 AI 无意义）。
3. **AI 行必须有非零效率值能力** —— 不能恒为 0（这正是本次修复的缺陷）。
4. **桶与真人同构** —— 用同一 `CAT_BUCKETS`，key 不得另写一套。

## 为什么必须用测试锁
`AI 行 work_score=0` 这个缺陷**不报错、不影响加载**，
只是静默地让"AI 效率"永远显示 0 —— 单靠看代码很难发现。
"""
from __future__ import annotations

import inspect

import pytest

from app.services import analytics as A


@pytest.fixture(scope="module")
def src() -> str:
    return inspect.getsource(A.today_staff_efficiency)


class TestSameWeightTable:
    """★ 最核心：AI 与真人共用同一权重表。"""

    def test_ai_scores_helper_uses_cat_buckets(self, src):
        """AI 计分必须遍历**同一个** `CAT_BUCKETS`（不得另写桶列表）。"""
        assert "def _ai_scores(" in src, "缺少 AI 计分函数"
        # 取出 _ai_scores 函数体
        body = src.split("def _ai_scores(")[1].split("def ")[0]
        assert "CAT_BUCKETS" in body, "AI 计分未复用 CAT_BUCKETS → 口径不可比"

    def test_ai_scores_uses_cat_default_weight(self, src):
        """AI 系数必须取自 `CAT_DEFAULT_WEIGHT`（与真人"管理岗"同一路径）。"""
        body = src.split("def _ai_scores(")[1].split("def ")[0]
        assert "CAT_DEFAULT_WEIGHT[cat_name]" in body, (
            "AI 系数未取 CAT_DEFAULT_WEIGHT → 与真人不可比"
        )

    def test_ai_does_not_define_own_weights(self, src):
        """AI 路径**不得**出现字面量系数（那意味着另写了一套）。"""
        body = src.split("def _ai_scores(")[1].split("def ")[0]
        for bad in ("1.0", "1.8", "2.0", "2.2", "3.0", "5.0"):
            assert bad not in body, f"AI 计分出现硬编码系数 {bad} → 与真人漂移风险"

    def test_uses_default_weight_not_transfer_penalty(self, src):
        """AI 的系数必须取**桶默认系数**，不能取转接系数 0.2。

        为什么：AI 不是「转接接待」—— 它是在**独立承接**接待。
        若给 AI 用 0.2，其效率值会被系统性低估 5~25 倍，比对结论会完全反过来。

        ⚠️ 取样边界：只取 `_ai_scores` 的**函数体**（到下一个同级 def 为止），
        用 `return w, e, bd` 作结束标记 —— 否则会吃到后面 record 组装段里的
        `"transfer_penalty": TRANSFER_PENALTY`（那是字段声明，不是计分）。
        """
        body = src.split("def _ai_scores(")[1].split("return w, e, bd")[0]
        code_lines = [
            ln for ln in body.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        code = "\n".join(code_lines)
        assert "TRANSFER_PENALTY" not in code, (
            "AI 计分在**代码**里用了 TRANSFER_PENALTY → AI 效率被低估"
        )


class TestAiNotRanked:
    def test_ai_rank_is_none(self, src):
        """AI 行的 rank 必须为 None（不进真人排名）。"""
        # AI 追加段
        block = src.split("_AI_SYS_OPS = [")[1]
        assert '"rank": None' in block, "AI 行 rank 未置 None"

    def test_ai_appended_after_ranking(self, src):
        """AI 行必须在真人排名**之后**追加（否则会占掉名次）。

        ⚠️ 锚点必须用 AI 追加段的**独有**特征 —— `"is_virtual": True` 在真人
        record 里也出现一次（占位），用它会锚到错误位置。
        此处用 `_AI_SYS_OPS` 界定 AI 段，再在段内找排名语句的相对位置。
        """
        # 「真人排名」发生在 AI 聚合之前；AI 行在 `aggregate_mode == "person"`
        # 分支里追加，该分支必须晚于 `rec["rank"] = i`
        i_rank = src.index('rec["rank"] = i')
        i_ai_append = src.index('for rec in virtual_rows:')
        assert i_rank < i_ai_append, "virtual_rows 追加早于排名 → AI 会占用真人名次"


class TestAiEfficiencyPresent:
    """★ 回归：修复前 AI 行 work_score/eff_score 恒为 0。"""

    def test_ai_work_and_eff_score_computed(self, src):
        block = src.split("for vr in _virtual_agg:")[1]
        assert '"work_score": round(_v_ws, 2)' in block, "AI work_score 未计算"
        assert '"eff_score": round(_v_es, 2)' in block, "AI eff_score 未计算"

    def test_ai_breakdown_populated(self, src):
        """AI 必须给出 breakdown（前端才能按其构成展示，与真人同构）。"""
        block = src.split("for vr in _virtual_agg:")[1]
        assert '"breakdown": _v_bd' in block, "AI breakdown 未填充"

    def test_ai_bucket_numbers_wired_to_sp_pp_fields(self, src):
        """AI 的 sp_*/pp_* 字段须由 breakdown 派生（而非恒 0）。"""
        block = src.split("for vr in _virtual_agg:")[1]
        assert '"sp_zixun": _v_bd["咨询接待"]["sessions"]' in block
        assert '"pp_zixun": _v_bd["咨询接待"]["people"]' in block
        # 旧实现是写死 0 —— 确认已移除
        assert '"sp_zixun": 0, "sp_taocan": 0' not in block


class TestAiCountsSourceConsistency:
    """AI 的接待量口径须与真人侧一致（同一 merged_pp / cs_cat_by_op）。"""

    def test_ai_cat_counts_reuses_shared_maps(self, src):
        body = src.split("def _ai_cat_counts(")[1].split("def ")[0]
        assert "cs_cat_by_op" in body, "AI 会话数未复用真人侧的 cs_cat_by_op"
        assert "merged_pp" in body, "AI 接待数未复用真人侧的 merged_pp"

    def test_ai_does_not_count_refund_or_ticket(self, src):
        """AI 不执行退款操作/工单完工 → processing 桶的 sessions 须为 0。"""
        body = src.split("def _ai_cat_counts(")[1].split("def ")[0]
        assert '"processing": (0,' in body, "AI 不应计入退款操作量"

# -*- coding: utf-8 -*-
"""AI / 系统账户展示表 —— 结构契约测试（2026-09-14 用户要求）

## 用户原文
「AI 系统账户的表格样式 可以仿照效率值的样式计算，如果归到一个表格里应该不好写，
  但是单独核算应该可以。会话数、专项接待数 接待合计 会话效率 接待效率
  都可以计算出来的吧。不需要等有数据的时候再来显示吧？」

## 本文件锁定三条
1. **列结构对齐** —— AI 表必须与真人效率值表**同名同序**的关键列，
   否则读者要自己做映射，无法「上下对读」。
2. **始终渲染** —— 不得用 `v-if="virtualStaffList.length"` 隐藏整块
   （那会让"AI 本期零工作"与"功能未启用"无法区分，违反看板规范
   「空必须可解释」）。
3. **后端字段齐备** —— 前端用到的每一列，后端都必须真的返回。

## 为什么用文本断言而非渲染测试
本仓库前端无组件渲染测试基建（vitest 只覆盖 src/config 等纯逻辑）。
对这类「列/条件是否写了」的契约，文本断言足够且零成本 ——
真正会回归的是**人改代码时漏改一处**，而非渲染细节。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web" / "src" / "views" / "dashboards"
TODAY = WEB / "Today.vue"
OPS = WEB / "OperationDashboard.vue"

# AI 表必须与真人表一致的列（同名）
ALIGNED_COLUMNS = ["会话数(次)", "各专项接待数(个)", "接待合计", "会话效率", "接待效率"]


def _read(p: Path) -> str:
    if not p.exists():
        pytest.skip(f"前端文件不存在: {p}")
    return p.read_text(encoding="utf-8")


def _ai_block(src: str) -> str:
    """截出 AI / 系统账户表格区块。

    用 `virtual-staff-block` 的开头到所在 section 结束为界。
    """
    i = src.index('class="virtual-staff-block"')
    # 取其后 6000 字符足够覆盖整表（表约 3-4k）
    return src[i: i + 7000]


# ═══════════════════════════════════════════════════════════════════
# 1. 列结构对齐
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("page", [TODAY, OPS], ids=["Today", "OperationDashboard"])
def test_ai_table_has_aligned_columns(page):
    """★ AI 表必须含与真人表同名的 5 个关键列（用户要求的可比性基础）。"""
    block = _ai_block(_read(page))
    missing = [c for c in ALIGNED_COLUMNS if c not in block]
    assert not missing, (
        f"{page.name} 的 AI 表缺少与真人对齐的列: {missing}\n"
        f"（用户要求「仿照效率值的样式」—— 列名不一致就无法上下对读）"
    )


@pytest.mark.parametrize("page", [TODAY, OPS], ids=["Today", "OperationDashboard"])
def test_ai_table_reuses_specialty_cols(page):
    """专项列必须复用 `SPECIALTY_COLS`（不得另写一套列定义）。

    回归风险：若 AI 表硬编码列名，将来加/改专项时只改真人表 →
    AI 表悄悄少一列，且不报错。
    """
    block = _ai_block(_read(page))
    assert 'v-for="col in SPECIALTY_COLS"' in block, (
        f"{page.name} 的 AI 表未复用 SPECIALTY_COLS → 专项列会与真人表漂移"
    )
    assert "getSpecialtyPeople(row, col.key)" in block, (
        f"{page.name} 的 AI 表未用 getSpecialtyPeople 取数"
    )


@pytest.mark.parametrize("page", [TODAY, OPS], ids=["Today", "OperationDashboard"])
def test_ai_table_has_score_breakdown_popover(page):
    """效率值列必须带构成弹窗（与真人表一致）—— 否则数字不可解释。"""
    block = _ai_block(_read(page))
    assert "work_score" in block and "eff_score" in block
    assert "score-tip-title" in block, "缺少效率值构成弹窗"


# ═══════════════════════════════════════════════════════════════════
# 2. ★ 始终渲染（用户明确要求）
# ═══════════════════════════════════════════════════════════════════
def _strip_html_comments(src: str) -> str:
    """去掉 HTML 注释 —— 注释里为说明原因会引用旧写法（如
    `v-if="virtualStaffList.length"`），连注释一起断言会误报（本人踩过两次）。"""
    return re.sub(r"<!--.*?-->", "", src, flags=re.S)


@pytest.mark.parametrize("page", [TODAY, OPS], ids=["Today", "OperationDashboard"])
def test_ai_block_always_rendered(page):
    """★ AI 块**不得**用 `v-if="virtualStaffList.length"` 隐藏。

    用户原话：「不需要等有数据的时候再来显示吧？」
    理由（看板规范「空必须可解释」）：整块消失会让人分不清
    「AI 本期零工作」与「这个功能没做」。
    """
    src = _strip_html_comments(_read(page))
    assert 'v-if="virtualStaffList.length"' not in src, (
        f"{page.name} 仍用 v-if 隐藏 AI 块 → 空态无法解释"
    )


@pytest.mark.parametrize("page", [TODAY, OPS], ids=["Today", "OperationDashboard"])
def test_ai_table_has_explicit_empty_state(page):
    """★ 必须有 `#empty` 空态文案，且**说明「不是功能未启用」**。"""
    block = _ai_block(_read(page))
    assert "#empty" in block, f"{page.name} AI 表缺 #empty 空态"
    assert "不是功能未启用" in block, (
        f"{page.name} 空态未说明「不是功能未启用」→ 读者仍会误判"
    )


# ═══════════════════════════════════════════════════════════════════
# 3. 后端字段齐备
# ═══════════════════════════════════════════════════════════════════
def test_backend_returns_all_columns_used_by_frontend():
    """★ 前端 AI 表用到的每个字段，后端都要真的返回。

    回归风险：前端加了列但后端没给字段 → 列恒空（不报错，静默空白）。
    """
    import inspect

    from app.services import analytics as A

    src = inspect.getsource(A.today_staff_efficiency)
    ai_block = src.split("for vr in _virtual_agg:")[1]

    for field in ("reception_count", "reception_device_count",
                  "work_score", "eff_score", "breakdown"):
        assert f'"{field}"' in ai_block, f"AI 行缺字段 {field}"

    # 专项接待数：必须由 breakdown 派生（而非写死 0）
    for prefix in ("sp_", "pp_"):
        assert f'"{prefix}zixun": _v_bd' in ai_block, (
            f"AI 行的 {prefix}* 字段未由 breakdown 派生 → 前端专项列恒空"
        )


def test_backend_ai_breakdown_has_all_buckets():
    """AI 的 breakdown 必须含全部展示桶（含新增投诉专项）。

    若某桶缺席，前端 `getSpecialtyPeople(row, '投诉专项')` 恒返回 0 ——
    看起来像"AI 不管投诉"（可能对），但若是漏写则**无法区分**。
    """
    import inspect

    from app.services import analytics as A

    src = inspect.getsource(A.today_staff_efficiency)
    ai_scores = src.split("def _ai_scores(")[1].split("return w, e, bd")[0]
    # 遍历的是 CAT_BUCKETS → 自动含全部桶（这是正确做法）
    assert "CAT_BUCKETS" in ai_scores, (
        "AI 的 breakdown 未遍历 CAT_BUCKETS → 新增桶时 AI 表会漏列"
    )
    assert 'bd[cat_name] = {' in ai_scores, "breakdown 未按 cat_name 填充"

# -*- coding: utf-8 -*-
"""B2-5 (#240 / R-P1-4): 预生成 max_tokens 按 period 对齐 R3 长报告口径。

锁定:
- default_max_tokens_for_period 各 period 分档与 #134 迁移 / rolling seed 现存值一致;
- 所有「详尽长报告」period 默认 ≥4000(R3 下限), 仅日报 daily 例外(2500);
- resolve_max_tokens 显式正整数优先, None/0 回退 period 默认;
- 列 ORM 兜底默认 = 4000。

纯函数测试, 不依赖 DB。
"""

import pytest

from app.models.ai_report_template import (
    DEFAULT_MAX_TOKENS_BY_PERIOD,
    DEFAULT_MAX_TOKENS_LONG_REPORT,
    AiReportTemplate,
    default_max_tokens_for_period,
    resolve_max_tokens,
)


@pytest.mark.parametrize("period,expected", [
    ("daily", 2500),
    ("weekly", 4000),
    ("monthly", 5000),
    ("rolling_7d", 4000),
    ("rolling_30d", 5000),
])
def test_period_defaults_match_existing_data(period, expected):
    """分档取值必须与 #134 迁移 / rolling seed 现存值逐一对齐(单一来源)。"""
    assert default_max_tokens_for_period(period) == expected


def test_unknown_and_none_period_floor_to_long_report():
    """未知/None/空白 period 一律按 R3 长报告下限 4000 兜底(宁宽裕不截断)。"""
    assert default_max_tokens_for_period("range") == 4000
    assert default_max_tokens_for_period(None) == 4000
    assert default_max_tokens_for_period("  weekly ") == 4000  # strip 后命中


def test_every_long_report_period_meets_r3_floor():
    """R3: 除日报外所有内置长报告 period 默认不得低于 4000(防截断回流)。"""
    long_periods = ["weekly", "monthly", "rolling_7d", "rolling_30d"]
    for p in long_periods:
        assert DEFAULT_MAX_TOKENS_BY_PERIOD[p] >= 4000, f"{p} 违反 R3 长报告 ≥4000"
    # 未知 period 兜底同样达标
    assert DEFAULT_MAX_TOKENS_LONG_REPORT >= 4000


def test_resolve_explicit_value_wins():
    """显式正整数 max_tokens 优先于 period 默认(管理员自定义不被覆盖)。"""
    assert resolve_max_tokens("weekly", 3333) == 3333
    assert resolve_max_tokens("monthly", 4000) == 4000


@pytest.mark.parametrize("explicit", [None, 0])
def test_resolve_falls_back_when_unspecified(explicit):
    """未给 / 给 0(非法上限) → 回退 period 分档默认。"""
    assert resolve_max_tokens("monthly", explicit) == 5000
    assert resolve_max_tokens("daily", explicit) == 2500


def test_column_orm_default_is_r3_floor():
    """列 Python 端兜底默认 = 4000(无 server_default, 不产生表结构变更)。"""
    assert DEFAULT_MAX_TOKENS_LONG_REPORT == 4000
    col = AiReportTemplate.__table__.c.max_tokens
    # SQLAlchemy Python 端默认(ScalarOption)取 .arg
    assert col.default.arg == 4000
    assert col.server_default is None

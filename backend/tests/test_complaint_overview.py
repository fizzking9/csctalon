# -*- coding: utf-8 -*-
"""投诉决策概览 (GET /complaints/overview) 口径自洽回归测试。

工单: #67（投诉分析整体重构 · P1）
设计: docs/arch/_pending/A-20260911-001-投诉分析整体重构.md §二.1
规范: docs/standards/界面设计说明规范.md

本测试锁定**口径自洽性**（而非仅函数返回值）—— 这是 #67 的核心价值：
  ① 堆叠趋势之和 ≡ KPI 总量      （同屏数字必须能对上）
  ② 累计净水位(末桶) ≡ 总积压     （水位与积压必须同源）
  ③ (外)卡商 独立，不入投诉总量    （#53 用户口径，防混桶回归）
  ④ 完成率 ≤ 100%                （防对称口径被破坏）
  ⑤ 粒度自动推导规则             （≤31天=日 / 32~120天=周 / >120天=月）

★ 为什么必须有这些断言:
  历史上同一屏出现过「KPI 与图表对不上」「水位与积压方向相反」
  「卡商被并回外部」三类缺陷，都是**口径漂移**而非语法错误，
  只有把等价关系固化成测试才能防住。
"""

from datetime import datetime, timedelta

import pytest

from app.api.v1.complaints import (
    _MAX_CREDIBLE_PCT,
    _MIN_BASE_FOR_PCT,
    _OVERDUE_DAYS,
    _agg_by_bucket,
    _auto_grain,
    _pct,
)
from app.services.analytics import TZ

# ── 纯函数测试(无需数据库) ──


class TestAutoGrain:
    """粒度自动推导: 跨度 → 粒度。"""

    @pytest.mark.parametrize("days,expected", [
        (1, "day"), (7, "day"), (31, "day"),          # ≤31 天 → 日
        (32, "week"), (90, "week"), (120, "week"),    # 32~120 天 → 周
        (121, "month"), (365, "month"), (1000, "month"),  # >120 天 → 月
    ])
    def test_boundaries(self, days, expected):
        assert _auto_grain(days) == expected

    def test_monotonic_never_downgrades(self):
        """粒度只应随跨度变粗(日→周→月)，不应回退。"""
        order = {"day": 0, "week": 1, "month": 2}
        prev = -1
        for d in range(1, 400):
            cur = order[_auto_grain(d)]
            assert cur >= prev, f"跨度 {d} 天粒度回退"
            prev = cur


class TestAggByBucket:
    """按日 → 日/周/月 聚合。"""

    def test_day_grain_passthrough(self):
        day_map = {
            "2026-09-01": {"内部投诉": 3},
            "2026-09-02": {"内部投诉": 5},
        }
        out = _agg_by_bucket(day_map, "day")
        assert len(out) == 2
        assert out[0]["label"] == "09-01"
        assert out[0]["内部投诉"] == 3
        # 升序
        assert [b["bucket"] for b in out] == ["2026-09-01", "2026-09-02"]

    def test_week_grain_uses_iso_monday(self):
        """ISO 周: 2026-09-07 是周一 → 该周桶键应为 2026-09-07，标签含区间。"""
        day_map = {
            "2026-09-07": {"外部投诉": 1},  # 周一
            "2026-09-13": {"外部投诉": 2},  # 周日(同一 ISO 周)
            "2026-09-14": {"外部投诉": 4},  # 下周一
        }
        out = _agg_by_bucket(day_map, "week")
        assert len(out) == 2
        assert out[0]["bucket"] == "2026-09-07"
        assert out[0]["外部投诉"] == 3, "同一 ISO 周必须合并"
        assert out[0]["label"] == "09-07~09-13"
        assert out[1]["bucket"] == "2026-09-14"
        assert out[1]["外部投诉"] == 4

    def test_month_grain(self):
        day_map = {
            "2026-08-31": {"内部投诉": 1},
            "2026-09-01": {"内部投诉": 2},
            "2026-09-30": {"内部投诉": 4},
        }
        out = _agg_by_bucket(day_map, "month")
        assert [b["bucket"] for b in out] == ["2026-08", "2026-09"]
        assert out[1]["内部投诉"] == 6

    def test_partial_fields_are_zero_filled_by_absence(self):
        """不同日出现不同字段时，聚合不应崩(缺失按 0 处理)。"""
        day_map = {
            "2026-09-01": {"内部投诉": 2},
            "2026-09-02": {"外部投诉": 3},
        }
        out = _agg_by_bucket(day_map, "month")
        assert out[0]["内部投诉"] == 2
        assert out[0]["外部投诉"] == 3

    def test_invalid_day_skipped(self):
        """脏日期不应导致崩溃。"""
        out = _agg_by_bucket({"not-a-date": {"内部投诉": 1}}, "day")
        assert out == []


class TestPct:
    """环比百分比。"""

    def test_basic(self):
        assert _pct(120, 100) == 20.0
        assert _pct(80, 100) == -20.0

    def test_zero_base_returns_none(self):
        """基数为 0 → 不可比值，返回 None(而非无限大)。"""
        assert _pct(10, 0) is None
        assert _pct(0, 0) is None

    def test_rounding(self):
        assert _pct(100, 3) == 3233.3


class TestConclusionGuards:
    """结论条护栏常量(防伪信号)。"""

    def test_min_base_guard_prevents_small_denominator_noise(self):
        """小基数护栏: 3→6 条虽 +100%，但基数 < 阈值 → 不应作为结论(由接口层过滤)。"""
        assert _pct(6, 3) == 100.0
        assert 3 < _MIN_BASE_FOR_PCT, "基数 3 应被最小基数护栏挡下"

    def test_max_credible_guard_is_configured(self):
        """可信上限应存在且为正 —— 用于抑制「对比期几乎无数据」造成的伪暴增。"""
        assert _MAX_CREDIBLE_PCT > 0
        assert _pct(2824, 141) > _MAX_CREDIBLE_PCT, (
            "长窗口跨系统上线前时的伪暴增应超上限而被抑制"
        )

    def test_overdue_threshold_is_seven_days(self):
        """超期阈值 7 天 —— 与积压页龄期口径一致，改此值需同步改前端文案。"""
        assert _OVERDUE_DAYS == 7


# ── 数据库集成测试(口径自洽性) ──

pytest.importorskip("sqlalchemy")


def _overview(db, **kw):
    """调用概览接口并归一化返回(dict / pydantic 兼容)。"""
    from app.api.v1.complaints import complaint_overview
    res = complaint_overview(db=db, _=None, **kw)
    if hasattr(res, "data"):
        return res.data
    return res["data"] if isinstance(res, dict) and "data" in res else res


@pytest.fixture
def db_session():
    """真实库会话(只读用法)。

    ⚠️ 本测试**只读**，不写任何数据；库不可用时整体跳过(不阻塞 CI)。
    """
    try:
        from app.core.database import SessionLocal
        db = SessionLocal()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"数据库不可用: {e}")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def db_with_complaints(db_session):
    """要求库中**有投诉数据**才运行的自洽性测试。

    ★ 为什么需要这道闸门:
      测试库 `csctalon_test` 是独立空库(见 conftest.py 的库名强制隔离)，
      口径自洽性断言在**空数据上恒成立但无意义**(如"堆叠之和 0 == 总量 0")。
      若不加闸门会得到"绿灯假象" —— 看似通过，实则没验证任何东西。
      故: 空库 → **显式跳过**并说明原因；有数据(本地/预发验证) → 真正校验。
    """
    from sqlalchemy import func, select

    from app.models.ticket import ServiceTicket
    n = db_session.scalar(select(func.count()).select_from(ServiceTicket)) or 0
    if not n:
        pytest.skip("库中无 service_tickets 数据 —— 口径自洽性断言需真实数据才有意义")
    return db_session


def _clear_cache():
    """清理概览接口缓存 —— 否则会读到改动前的旧结果(实测踩过)。"""
    try:
        from app.core.redis_client import get_redis
        r = get_redis()
        ks = list(r.scan_iter("complaint:overview:*"))
        if ks:
            r.delete(*ks)
    except Exception:  # pragma: no cover
        pass


def _cases():
    """(start, end, 期望粒度) 三档跨度，覆盖日/周/月。"""
    today = datetime.now(TZ).date()
    return [
        ((today - timedelta(days=29)).isoformat(), today.isoformat(), "day"),
        ((today - timedelta(days=40)).isoformat(), today.isoformat(), "week"),
        ((today - timedelta(days=400)).isoformat(), today.isoformat(), "month"),
    ]


def _require_window_data(d):
    """窗口内必须有真实数据，否则跳过 —— 防止空窗口上 "0 == 0" 产生**假绿**。

    ★ 这是本测试最关键的一道护栏:
      测试库可能是空库、或真实库但所选窗口无数据。此时
      「堆叠之和 0 == KPI 总量 0」「净水位 0 == 积压 0」都会**恒真通过**，
      看起来验证了口径，实际什么都没验证。故显式跳过并说明。
    """
    has_trend = bool(d.get("trend_stacked"))
    has_backlog = bool((d.get("backlog") or {}).get("total"))
    if not (has_trend or has_backlog):
        pytest.skip("所选窗口内无投诉数据 —— 空数据上自洽性断言恒真，无验证意义")


class TestOverviewCoherence:
    """概览口径自洽性(核心回归防线)。"""

    @pytest.mark.parametrize("idx", [0, 1, 2])
    def test_stacked_trend_sum_equals_kpi_total(self, db_with_complaints, idx):
        """① 堆叠趋势之和 ≡ KPI 总量。

        若不等 → 同屏 KPI 与图表打架(#67 要修的核心问题之一)。
        """
        _clear_cache()
        start, end, _ = _cases()[idx]
        d = _overview(db_with_complaints, start=start, end=end, grain=None, compare="none")
        _require_window_data(d)
        st = d["trend_stacked"]
        s_sum = sum(
            b.get("内部投诉", 0) + b.get("商户号投诉", 0) + b.get("外部投诉", 0)
            for b in st
        )
        kpi_total = next(k for k in d["kpi"] if k["key"] == "total")["value"]
        assert s_sum == kpi_total, f"堆叠之和 {s_sum} != KPI 总量 {kpi_total}"

    @pytest.mark.parametrize("idx", [0, 1, 2])
    def test_cum_net_water_equals_backlog(self, db_with_complaints, idx):
        """② 累计净水位(末桶) ≡ 总积压。

        若不等 → 水位图与积压卡方向相反(历史上发生过的缺陷)。
        """
        _clear_cache()
        start, end, _ = _cases()[idx]
        d = _overview(db_with_complaints, start=start, end=end, grain=None, compare="none")
        _require_window_data(d)
        rh = d["trend_reg_handled"]
        assert rh, "窗口内有数据时水位序列不应为空"
        assert rh[-1]["cum_net"] == d["backlog"]["total"], (
            f"累计净水位 {rh[-1]['cum_net']} != 积压 {d['backlog']['total']}"
        )

    @pytest.mark.parametrize("idx", [0, 1, 2])
    def test_card_bucket_excluded_from_total_stack(self, db_with_complaints, idx):
        """③ (外)卡商 独立 —— 不得出现在总量堆叠中(#53 用户口径)。

        若出现 → 复现 #53 之前的混桶问题(卡商被并回投诉总量)。
        """
        _clear_cache()
        start, end, _ = _cases()[idx]
        d = _overview(db_with_complaints, start=start, end=end, grain=None, compare="none")
        _require_window_data(d)
        for b in d["trend_stacked"]:
            assert "(外)卡商" not in b, f"卡商混入总量堆叠: {b}"
        # 卡商自身趋势独立返回(允许为空 —— 窗口内可能无卡商投诉)
        assert isinstance(d["card_trend"], list)

    @pytest.mark.parametrize("idx", [0, 1, 2])
    def test_completion_rate_within_100(self, db_with_complaints, idx):
        """④ 完成率 ≤ 100%(对称口径: 分子分母同为「窗口内登记」)。"""
        _clear_cache()
        start, end, _ = _cases()[idx]
        d = _overview(db_with_complaints, start=start, end=end, grain=None, compare="none")
        _require_window_data(d)
        rate = next(k for k in d["kpi"] if k["key"] == "completion_rate")["value"]
        assert rate is None or 0 <= rate <= 100, f"完成率越界: {rate}"

    @pytest.mark.parametrize("idx,expected", [(0, "day"), (1, "week"), (2, "month")])
    def test_grain_auto_deduced(self, db_with_complaints, idx, expected):
        """⑤ 粒度按跨度自动推导。"""
        _clear_cache()
        start, end, _ = _cases()[idx]
        d = _overview(db_with_complaints, start=start, end=end, grain=None, compare="none")
        assert d["range"]["grain"] == expected

    def test_grain_override_respected(self, db_with_complaints):
        """显式传 grain 时不被自动推导覆盖。"""
        _clear_cache()
        today = datetime.now(TZ).date()
        start = (today - timedelta(days=10)).isoformat()
        d = _overview(db_with_complaints, start=start, end=today.isoformat(),
                      grain="month", compare="none")
        assert d["range"]["grain"] == "month"
        assert len(d["trend_stacked"]) <= 2, "10 天强制按月应只余 1~2 个桶"

    def test_ratios_placeholder_is_null(self, db_with_complaints):
        """比率指标占位: 本期恒 null(见 #68 专项研究)，前端据此不渲染。"""
        _clear_cache()
        d = _overview(db_with_complaints, start=None, end=None, grain=None, compare="none")
        assert d["ratios"] is None

    def test_compare_period_is_adjacent_equal_length(self, db_with_complaints):
        """对比期 = 紧邻的等长前段窗口(环比可比性前提)。"""
        _clear_cache()
        today = datetime.now(TZ).date()
        start = (today - timedelta(days=29)).isoformat()
        d = _overview(db_with_complaints, start=start, end=today.isoformat(), grain=None, compare="prev")
        rng = d["range"]
        assert rng["compare"] == "prev"
        cur_days = (datetime.strptime(rng["end"], "%Y-%m-%d") - datetime.strptime(rng["start"], "%Y-%m-%d")).days
        prev_days = (datetime.strptime(rng["prev_end"], "%Y-%m-%d") - datetime.strptime(rng["prev_start"], "%Y-%m-%d")).days
        assert cur_days == prev_days, "对比期应与本期等长"
        # 对比期结束日 = 本期开始日 − 1 天(紧邻不重叠)
        assert (
            datetime.strptime(rng["prev_end"], "%Y-%m-%d")
            - datetime.strptime(rng["start"], "%Y-%m-%d")
        ).days == -1, "对比期必须紧邻本期之前，不得重叠"

    def test_compare_none_hides_prev(self, db_with_complaints):
        """compare=none 时不输出对比期与环比 —— 避免前端显示误导性的 0%。"""
        _clear_cache()
        d = _overview(db_with_complaints, start=None, end=None, grain=None, compare="none")
        assert d["range"]["compare"] == "none"
        assert "prev_start" not in d["range"]
        for k in d["kpi"]:
            assert k["prev"] is None
            assert k["delta_pct"] is None

    def test_kpi_has_five_cards_with_meta(self, db_with_complaints):
        """KPI 恰好 5 张(概览不堆卡)，且每张都有口径说明(供前端 ⓘ 渲染)。"""
        _clear_cache()
        d = _overview(db_with_complaints, start=None, end=None, grain=None, compare="none")
        keys = [k["key"] for k in d["kpi"]]
        assert keys == ["total", "handled", "completion_rate", "backlog", "refund_amount"]
        for k in keys:
            assert k in d["kpi_meta"], f"{k} 缺少口径说明"
            assert d["kpi_meta"][k], f"{k} 口径说明不得为空"

    def test_conclusions_sorted_by_severity(self, db_with_complaints):
        """结论条按 danger→warn→info→ok 排序(最该看的在最前)。"""
        _clear_cache()
        d = _overview(db_with_complaints, start=None, end=None, grain=None, compare="prev")
        order = {"danger": 0, "warn": 1, "info": 2, "ok": 3}
        levels = [order[c["level"]] for c in d["conclusions"]]
        assert levels == sorted(levels), f"结论未按严重度排序: {d['conclusions']}"

    def test_backlog_items_capped(self, db_with_complaints):
        """积压明细上限(概览只给 Top，全量在「执行跟踪」页)。"""
        _clear_cache()
        d = _overview(db_with_complaints, start=None, end=None, grain=None, compare="none")
        assert len(d["backlog"]["items"]) <= 10

    def test_backlog_excludes_card_bucket(self, db_with_complaints):
        """概览积压不含卡商(卡商独立体系)。"""
        _clear_cache()
        d = _overview(db_with_complaints, start=None, end=None, grain=None, compare="none")
        for item in d["backlog"]["items"]:
            assert item.get("source") != "(外)卡商"

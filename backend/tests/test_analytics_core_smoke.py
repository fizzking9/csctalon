# -*- coding: utf-8 -*-
"""核心引擎关键路径冒烟测试 (B1-5 / R-P1-15 / 工单 #230)

## 背景
R-P1-15 指出 `refund_chain.py` 与 `rr_views.py` 两个核心引擎模块零冒烟覆盖。
本文件补齐 happy-path 冒烟, 守护:
  ① 模块可导入 + 公开函数签名稳定 (防拆分/重命名回归)
  ② 纯 Python helper 行为正确 (无 DB 依赖, 始终生效)
  ③ DB 依赖函数对空库不崩 (返回空结构, 不报 SQL 错误)

## 测试分层
- 纯 Python (无 DB): 始终生效, 守护签名/helper 行为
- DB 冒烟 (用 db fixture): CI 与本地有测试库时生效; 空库应返回空结构而非崩
"""
from __future__ import annotations

import inspect

import pytest

from app.services.analytics import (
    HANDLERS,
    _build_chain_rows,
    _build_reason_bar_data,
    _rr_parse_params,
    _sh_parse_params,
    refund_withdrawal_daily_split,
    refund_withdrawal_detail,
    refund_withdrawal_dimensions,
    refund_withdrawal_facets,
    refund_withdrawal_overview,
    refund_withdrawal_sleeping,
    refund_withdrawal_time_distribution,
)


# ═══════════════════════════════════════════════════════════════════
# 1. 模块结构 / 签名稳定性 (纯 Python, 始终生效)
# ═══════════════════════════════════════════════════════════════════
class TestRefundChainSignatures:
    """refund_chain.py 8 个公开函数签名稳定 (防重命名/拆分回归)。"""

    EXPECTED = {
        "refund_withdrawal_overview": ["db", "start", "end"],
        "refund_withdrawal_time_distribution": ["db", "start", "end"],
        "refund_withdrawal_dimensions": ["db", "start", "end", "topn"],
        "refund_withdrawal_facets": ["db", "start", "end"],
        "refund_withdrawal_detail": ["db", "start", "end", "sort", "page", "page_size",
                                      "slow_only", "path", "comp", "operator", "reason",
                                      "device_type", "combo", "merchant",  # #273 设备维度
                                      "active_start", "active_end"],
        "refund_withdrawal_daily_split": ["db", "start", "end"],
        "refund_withdrawal_sleeping": ["db", "start", "end", "page", "page_size"],
    }

    @pytest.mark.parametrize("name,expected_params", list(EXPECTED.items()))
    def test_public_function_signature(self, name, expected_params):
        fn = globals()[name]
        assert callable(fn), f"{name} 不可调用"
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        assert params == expected_params, (
            f"{name} 签名变化: 期望 {expected_params}, 实际 {params} — "
            f"拆分/重命名可能破坏调用方"
        )

    def test_build_chain_rows_is_callable(self):
        """_build_chain_rows 是 refund_chain 的内部入口, 必须可调用。"""
        assert callable(_build_chain_rows)
        sig = inspect.signature(_build_chain_rows)
        assert "db" in sig.parameters, "_build_chain_rows 必须含 db 参数"


class TestRrViewsHandlers:
    """rr_views.py HANDLERS 字典是 ViewItemCard 调度入口, 必须稳定。"""

    EXPECTED_KEYS = {
        "preset_refund_recovery_kpi",
        "preset_refund_recovery_daily_trend",
        "preset_refund_recovery_hourly_today",
        "preset_refund_recovery_operator_rank",
        "preset_refund_recovery_reason_dist",
        "preset_refund_recovery_reason_wish",
        "preset_refund_recovery_reason_saved",
        "preset_refund_operator_v2",
        "preset_refund_reason_top",
        "preset_ticket_work_content_top20",
        "preset_complaint_subtype_rootcause_heatmap",
        "preset_today_sn_top_coverage",
        "preset_today_phone_dup_complaint",
        "preset_today_handler_sn_rank",
        "preset_today_cold_tickets",
    }

    def test_handlers_dict_has_all_expected_keys(self):
        """15 个预设视图键必须全部存在 (前端路由硬编码, 缺一即页面空白)。"""
        missing = self.EXPECTED_KEYS - set(HANDLERS.keys())
        assert not missing, f"HANDLERS 缺键: {missing}"

    def test_handlers_dict_no_extra_keys(self):
        """无未登记的额外键 (防漂移: 新增 handler 必须同步本测试)。"""
        extra = set(HANDLERS.keys()) - self.EXPECTED_KEYS
        assert not extra, (
            f"HANDLERS 有未登记键 {extra} — 若确为新 handler, "
            f"请同步本测试 EXPECTED_KEYS"
        )

    def test_all_handlers_callable(self):
        """每个 handler 必须是可调用对象 (函数引用, 非字符串)。"""
        for key, fn in HANDLERS.items():
            assert callable(fn), f"HANDLERS[{key!r}] = {fn!r} 不可调用"


# ═══════════════════════════════════════════════════════════════════
# 2. 纯 Python helper (始终生效, 守护解析/格式化逻辑)
# ═══════════════════════════════════════════════════════════════════
class TestRrParseParams:
    """_rr_parse_params: 从视图执行参数解析 start/end (兼容 AnalysisView 约定)。"""

    def test_none_returns_none_pair(self):
        assert _rr_parse_params(None) == (None, None)

    def test_empty_dict_returns_none_pair(self):
        assert _rr_parse_params({}) == (None, None)

    def test_start_end_keys(self):
        result = _rr_parse_params({"start": "2026-10-01", "end": "2026-10-08"})
        assert result == ("2026-10-01", "2026-10-08")

    def test_date_start_date_end_aliases(self):
        """AnalysisView 约定的备用键名必须兼容。"""
        result = _rr_parse_params({"date_start": "2026-10-01", "date_end": "2026-10-08"})
        assert result == ("2026-10-01", "2026-10-08")

    def test_start_takes_priority_over_date_start(self):
        """start 优先于 date_start (按 .get(date_start) if start 缺省)。"""
        result = _rr_parse_params({
            "start": "2026-10-01", "date_start": "2026-09-01"
        })
        assert result == ("2026-10-01", None)


class TestShParseParams:
    """_sh_parse_params: 解析视图参数 → 北京时间当日窗口 (naive UTC)。

    本函数返回**单日窗口** (北京 00:00 ~ 23:59:59.999999), 转 UTC 后
    ts.hour 可能是 16 (前一天 16:00 UTC = 北京 00:00), 不应断言 ts.hour==0。
    """

    def test_none_returns_today_window(self):
        """无参数 → 返回今天北京自然日窗口 (单日, 长度≈86400s)。"""
        import datetime as _dt
        ts, te = _sh_parse_params(None)
        assert isinstance(ts, _dt.datetime), f"ts 类型错: {type(ts)}"
        assert isinstance(te, _dt.datetime), f"te 类型错: {type(te)}"
        # ts 早于 te (单日窗口)
        assert ts < te, f"窗口反转: ts={ts} >= te={te}"
        # 单日窗口长度 = 86400s - 1us (00:00:00 ~ 23:59:59.999999)
        delta_seconds = (te - ts).total_seconds()
        assert 86400 - 1 <= delta_seconds <= 86400, (
            f"单日窗口长度应≈86399.999999s, 实际 {delta_seconds}s"
        )

    def test_date_param_returns_that_day_window(self):
        """date='YYYY-MM-DD' → 该日北京自然日窗口 (单日, 长度≈86400s)。"""
        ts, te = _sh_parse_params({"date": "2026-10-01"})
        delta_seconds = (te - ts).total_seconds()
        assert 86400 - 1 <= delta_seconds <= 86400, (
            f"单日窗口长度应≈86399.999999s, 实际 {delta_seconds}s"
        )

    def test_start_end_params_uses_start_day(self):
        """start/end 字符串 → 用 start 的日期作为单日窗口 (end 被忽略)。

        _sh_parse_params 设计为返回**单日窗口**, 即便传 start+end 也只用 start 那一天。
        多日窗口由调用方自行处理 (本函数仅做"当日"解析)。
        """
        ts, te = _sh_parse_params({"start": "2026-10-01", "end": "2026-10-08"})
        delta_seconds = (te - ts).total_seconds()
        # 仍是单日窗口, 不是 7 天
        assert 86400 - 1 <= delta_seconds <= 86400, (
            f"应只用 start 那天 (单日窗口), 实际 {delta_seconds}s"
        )


class TestBuildReasonBarData:
    """_build_reason_bar_data: 原因分布 → 柱(数量)+线(占比%) 双Y轴图表数据。"""

    def test_empty_input_returns_empty_x_axis(self):
        """空输入 → x_axis 为空, series 有 2 项 (双Y轴结构固定)。"""
        result = _build_reason_bar_data([], "测试标题", "数量")
        assert result["chart_type"] == "bar"
        assert result["data"]["title"] == "测试标题"
        assert result["data"]["x_axis"] == []
        assert len(result["data"]["series"]) == 2  # bar + line
        assert result["data"]["series"][0]["name"] == "数量"
        assert result["data"]["series"][1]["name"] == "占比%"

    def test_with_data_builds_correct_structure(self):
        """有数据 → x_axis 与输入行数对齐, bars_data 数值正确。"""
        rd = [
            {"reason": "不想用了", "order_cnt": 10, "ratio": 50.0},
            {"reason": "充值错误", "order_cnt": 6, "ratio": 30.0},
        ]
        result = _build_reason_bar_data(rd, "退款原因", "单数")
        assert result["data"]["x_axis"] == ["不想用了", "充值错误"]
        bars = result["data"]["series"][0]["data"]
        assert bars[0]["value"] == 10
        assert bars[1]["value"] == 6
        # line 系列的 ratio 取 float(...) 转换, 保留 1 位小数
        line = result["data"]["series"][1]["data"]
        assert line[0] == 50.0
        assert line[1] == 30.0

    def test_missing_reason_falls_back_to_unknown(self):
        """reason 缺失 → x_axis 用'未知'兜底 (防 None 混入 ECharts)。"""
        result = _build_reason_bar_data(
            [{"order_cnt": 1, "ratio": 100.0}], "标题", "数量"
        )
        assert result["data"]["x_axis"] == ["未知"]


# ═══════════════════════════════════════════════════════════════════
# 3. DB 冒烟 (用 db fixture; CI / 本地有测试库时生效)
# ═══════════════════════════════════════════════════════════════════
class TestRefundChainDbSmoke:
    """refund_chain 公开函数对空库应返回空结构 (不报 SQL 错)。"""

    def test_overview_empty_db_returns_dict(self, db):
        result = refund_withdrawal_overview(db, start="2026-10-01", end="2026-10-08")
        assert isinstance(result, dict), f"overview 应返回 dict, 实际 {type(result)}"

    def test_time_distribution_empty_db_returns_list(self, db):
        result = refund_withdrawal_time_distribution(db, start="2026-10-01", end="2026-10-08")
        assert isinstance(result, list), f"time_distribution 应返回 list, 实际 {type(result)}"

    def test_dimensions_empty_db_returns_dict(self, db):
        result = refund_withdrawal_dimensions(db, start="2026-10-01", end="2026-10-08")
        assert isinstance(result, dict), f"dimensions 应返回 dict, 实际 {type(result)}"

    def test_facets_empty_db_returns_dict(self, db):
        result = refund_withdrawal_facets(db, start="2026-10-01", end="2026-10-08")
        assert isinstance(result, dict), f"facets 应返回 dict, 实际 {type(result)}"

    def test_detail_empty_db_returns_list(self, db):
        result = refund_withdrawal_detail(db, start="2026-10-01", end="2026-10-08")
        # detail 返回分页 dict: {total, page, page_size, risk_summary, list}
        assert isinstance(result, dict), f"detail 应返回 dict, 实际 {type(result)}"
        assert result["list"] == [] and result["total"] == 0

    def test_daily_split_empty_db_returns_list(self, db):
        result = refund_withdrawal_daily_split(db, start="2026-10-01", end="2026-10-08")
        assert isinstance(result, list), f"daily_split 应返回 list, 实际 {type(result)}"

    def test_sleeping_empty_db_returns_dict(self, db):
        result = refund_withdrawal_sleeping(db, start="2026-10-01", end="2026-10-08")
        assert isinstance(result, dict), f"sleeping 应返回 dict, 实际 {type(result)}"


class TestRrViewsDbSmoke:
    """rr_views HANDLERS 对空库应返回 dict (各视图 KPI/趋势空结构)。"""

    @pytest.mark.parametrize("handler_key", list(HANDLERS.keys()))
    def test_handler_returns_dict_on_empty_db(self, db, handler_key):
        """参数用 None (走默认今天窗口), 空库应返回 dict 而非崩。"""
        fn = HANDLERS[handler_key]
        result = fn(db, None)
        assert isinstance(result, dict), (
            f"HANDLERS[{handler_key!r}] 应返回 dict, 实际 {type(result)}"
        )

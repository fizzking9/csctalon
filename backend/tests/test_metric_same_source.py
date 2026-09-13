# -*- coding: utf-8 -*-
"""#83: 指标口径必须与看板同源 + 阈值经分布验证。

★ 为什么需要（教训来源）:
  2026-09-14 复核发现 `refund_count` 指标读 `refunds` 单表（含全部状态，
  近90天 p50=673/日），而看板「退款笔数」是**四路经营合计**（p50=277/日）
  → 同名不同义 → 与 #81 同类的「口径漂移」病：
    预警说「严重告警」、看板说「一切正常」，使用者无所适从。

  这类问题**语法上完全正确**，只有把「等价关系」固化成断言才能防住。
  （同类防线见 #81 的 `TestComplaintTotalSingleSource`、#67 的 `TestOverviewCoherence`）

覆盖:
1. `refund_count` 口径 == 看板四路合计（逐位一致）
2. 阈值不得"常态恒告警"：指标值落在历史常态区间时不应判 critical
   —— 用**相对判据**（值 vs 阈值的关系符合 direction 语义），不硬编码数字，
     避免管理员调阈值后测试失败（阈值是业务决策，测试只锁"逻辑正确"）
"""

import datetime as dt

import pytest

from app.services.analytics import _cash_routes_total
from app.services.metric_registry_service import evaluate_metric, get_threshold


class TestRefundCountSameSourceAsDashboard:
    """`refund_count` 必须与看板「退款笔数」**同源**（#83 核心验收）。"""

    @staticmethod
    def _window():
        """北京时间"昨日"整天 → naive UTC（与 metric _window 同口径）"""
        from app.services.metric_registry_service import _window

        y = dt.date.today() - dt.timedelta(days=1)
        return _window("day", y)

    def test_refund_count_equals_four_routes(self, db):
        """① 指标值 == 四路经营合计 A+B+C+D（逐位一致）。

        这是本单的**核心等式**：修复前两者差 ~2.4 倍（673 vs 277）。
        """
        ts, te = self._window()
        r = evaluate_metric(db, "refund_count")
        expect = int((_cash_routes_total(db, start=ts, end=te).get("operational") or {}).get("count") or 0)
        assert r["value"] == float(expect), (
            f"refund_count 指标 {r['value']} != 四路合计 {expect}"
            "（#83: 必须与看板同源，不得读 refunds 单表）"
        )

    def test_refund_count_detail_declares_source(self, db):
        """② detail 必须写明口径来源（可解释性；使用者不点代码也知道读什么）。"""
        r = evaluate_metric(db, "refund_count")
        detail = r.get("detail") or ""
        assert "四路" in detail, f"detail 未声明四路口径: {detail}"
        assert "同源" in detail, f"detail 未声明与看板同源: {detail}"

    def test_refund_count_meta_documents_fix(self, db):
        """③ 指标注册描述必须记录 #83 修正（防后人误改回单表）。"""
        from app.services.metric_registry_service import METRIC_DEFS

        desc = METRIC_DEFS["refund_count"]["description"]
        assert "四路" in desc, "描述必须写明四路口径"
        assert "#83" in desc, "描述必须标注 #83 修正（防回退）"


class TestThresholdDirectionSemantics:
    """阈值分级逻辑必须符合 direction 语义（#83 发现阈值错配的通用防线）。"""

    def test_lower_better_above_critical_is_critical(self, db):
        """lower_better: 值 > 告警线 → critical（越低越好）。"""
        m = evaluate_metric(db, "refund_count")
        thr = m.get("threshold") or {}
        if thr.get("critical_value") is None or m.get("value") is None:
            pytest.skip("该指标未配阈值或值缺失")
        # 用真实值验证分级自洽：值超 critical 线 ⇒ 必须判 critical
        if m["value"] > thr["critical_value"]:
            assert m["level"] == "critical", (
                f"值 {m['value']} > 告警线 {thr['critical_value']} 却未判 critical"
            )
        elif m["value"] > thr["warning_value"]:
            assert m["level"] in ("warning", "critical")

    def test_threshold_matches_realistic_magnitude(self, db):
        """★ 阈值不得与真实量级差数量级（#83 的**核心验收**）。

        判据（**相对**，不硬编码数字，避免阈值调整后测试失效）：
          若某 lower_better 指标的"告警线"低于其自身常态下限的一半，
          说明阈值与数据规模严重脱节 → 会**常态恒告警**（告警疲劳）。

        实现：拿该指标当前值作常态代理（生产上 p50 与当前值同数量级），
        要求 `critical_value` 与当前值**同数量级**（比值 ∈ [0.1, 10]）。
        """
        for code in ("refund_count", "ticket_backlog", "internal_complaint_daily"):
            m = evaluate_metric(db, code)
            thr = m.get("threshold") or {}
            if m.get("value") is None or thr.get("critical_value") in (None, 0):
                continue
            ratio = m["value"] / thr["critical_value"]
            assert 0.1 <= ratio <= 10, (
                f"{code}: 值 {m['value']} 与告警线 {thr['critical_value']} "
                f"差 {ratio:.2f} 倍 → 阈值与真实量级脱节（会常态恒告警，见 #83）"
            )

    def test_threshold_lookup_returns_latest(self, db):
        """`get_threshold` 必须取**当前生效**版本（valid_to IS NULL）。"""
        for code in ("refund_count", "recovery_rate"):
            thr = get_threshold(db, code)
            if thr is not None:
                assert thr.valid_to is None, f"{code} 取到了已失效阈值版本"

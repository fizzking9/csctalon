# -*- coding: utf-8 -*-
"""批次级异常检测测试 (#42, 2026-09-10)

## 动机(实证)
2026-09-05 两批 workbuddy 推送的中「投诉日期」被**整批转置**:
    库内年 = 2000 + 真值"日" ; 库内"日" = 真值年份 − 2001 ; 月份不变
该批 2532 行受影响, 而**字段级校验只检出 1684 行**(<2020 的 1556 + 未来 128),
余下 848 行落在"看似正常"的年份区间 —— 单行完全正常, 字段级阈值抓不到。

批次维度上却极醒目:
    正常批次: inserted = 0~3     异常日期占比 ≈ 0%
    污染批次: inserted = 2434    异常日期占比 75.2%(按回填前形态回放)
故需要批次级检查(A 体量 / B 日期比例 / C 组合)。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.ingest_anomaly import (
    DATE_ANOMALY_RATIO_THRESHOLD,
    MIN_VALID_YEAR,
    batch_size_stats,
    date_anomaly_stats,
    detect_anomalies,
    is_date_anomalous,
)

_UTC = timezone.utc
_TZ_CN = timezone(timedelta(hours=8))


# ---------- is_date_anomalous ----------
class TestIsDateAnomalous:
    def test_normal_business_date_ok(self):
        for y in (2023, 2024, 2025, 2026):
            assert not is_date_anomalous(datetime(y, 6, 15, tzinfo=_TZ_CN)), y

    def test_too_old_flagged(self):
        """早于业务存在期 → 异常(实证: 库内 2001-03-24 真值为 2025-03-01)"""
        assert is_date_anomalous(datetime(2001, 3, 24, tzinfo=_TZ_CN))
        assert is_date_anomalous(datetime(MIN_VALID_YEAR - 1, 12, 31, tzinfo=_TZ_CN))

    def test_future_flagged(self):
        """未来日期 → 异常(实证: 库内 2027-04-25 真值为 2026-04-27)"""
        future = datetime.now(_TZ_CN) + timedelta(days=30)
        assert is_date_anomalous(future)

    def test_none_not_counted_as_anomalous(self):
        """缺失不算"异常日期"(由 R-必填 负责), 否则会虚增比例"""
        assert not is_date_anomalous(None)

    def test_utc_stored_reads_by_cn(self):
        """★口径: 库内按 UTC 存, 判定必须按北京时间(否则差一天)"""
        # 北京 2023-09-03 00:00 = UTC 2023-09-02 16:00 → 应判为正常
        assert not is_date_anomalous(datetime(2023, 9, 2, 16, 0, tzinfo=_UTC))


# ---------- date_anomaly_stats ----------
class TestDateAnomalyStats:
    def test_counts_and_ratio(self):
        rows = [
            {"complaint_date": datetime(2025, 3, 1, tzinfo=_TZ_CN)},   # 正常
            {"complaint_date": datetime(2001, 3, 24, tzinfo=_TZ_CN)},  # 异常
            {"complaint_date": datetime(2024, 6, 1, tzinfo=_TZ_CN)},   # 正常
            {"complaint_date": datetime(2025, 5, 1, tzinfo=_TZ_CN)},   # 正常
        ]
        st = date_anomaly_stats(rows)
        assert st["total"] == 4
        assert st["with_value"] == 4
        assert st["anomalous"] == 1
        assert abs(st["ratio"] - 0.25) < 1e-9

    def test_null_dates_excluded_from_denominator(self):
        """★整列为空时 ratio 必须为 0(而非被 NULL 稀释);
        分母用 with_value 而非 total。"""
        rows = [{"complaint_date": None}] * 10
        st = date_anomaly_stats(rows)
        assert st["with_value"] == 0
        assert st["anomalous"] == 0
        assert st["ratio"] == 0.0

    def test_missing_field_safe(self):
        assert date_anomaly_stats([{}, {}])["ratio"] == 0.0


# ---------- batch_size_stats ----------
class TestBatchSizeStats:
    def test_median_used_not_mean(self):
        """★必须用中位数: 体量分布高度右偏(多数 0~3, 偶发数千),
        均值会被离群值拉高而失去判别力。"""
        hist = [0, 1, 2, 3, 0, 1, 0, 2, 3000]   # 均值 ≈ 334, 中位数 = 1
        bs = batch_size_stats(hist)
        assert bs["median"] == 1

    def test_empty_history(self):
        assert batch_size_stats([])["median"] is None

    def test_ignores_none(self):
        assert batch_size_stats([None, 2, None, 4])["median"] == 3


# ---------- detect_anomalies ----------
def _stats(anomalous: int, with_value: int) -> dict:
    return {"total": with_value, "with_value": with_value,
            "anomalous": anomalous, "ratio": anomalous / with_value if with_value else 0.0}


class TestDetectAnomalies:
    HIST = [0, 1, 0, 2, 3, 0, 1, 0, 0, 0, 3, 2, 2, 5, 0, 1, 119, 0, 0, 0]  # 中位数 0.5

    def test_normal_batch_no_alert(self):
        """正常批次(inserted=1, 无日期异常) → 不告警"""
        out = detect_anomalies(
            batch={"inserted": 1, "date_stats": _stats(0, 2500), "label": "x"},
            baseline_sizes=self.HIST)
        assert out == []

    def test_batch_size_only(self):
        out = detect_anomalies(
            batch={"inserted": 2434, "date_stats": _stats(0, 2500), "label": "x"},
            baseline_sizes=self.HIST)
        assert [a["rule"] for a in out] == ["A-batch-size"]

    def test_date_ratio_only(self):
        out = detect_anomalies(
            batch={"inserted": 1, "date_stats": _stats(500, 1000), "label": "x"},
            baseline_sizes=self.HIST)
        assert [a["rule"] for a in out] == ["B-date-ratio"]

    def test_combo_is_critical(self):
        """★复现 2026-09-05 事件: 大批次 + 高日期异常 → C-combo critical"""
        out = detect_anomalies(
            batch={"inserted": 2434, "date_stats": _stats(1896, 2520), "label": "315投诉"},
            baseline_sizes=self.HIST)
        rules = {a["rule"] for a in out}
        assert rules == {"A-batch-size", "B-date-ratio", "C-combo"}
        combo = next(a for a in out if a["rule"] == "C-combo")
        assert combo["severity"] == "critical"

    def test_small_batch_below_absolute_floor_not_flagged(self):
        """绝对下限保护: 小基数下"超中位数 20 倍"不应报警(否则噪声不断)"""
        out = detect_anomalies(
            batch={"inserted": 30, "date_stats": _stats(0, 30), "label": "x"},
            baseline_sizes=[0, 1, 0, 1, 0])
        assert not [a for a in out if a["rule"] == "A-batch-size"]

    def test_zero_median_uses_absolute_floor(self):
        """基线全 0 时: 若沿用"×20"则恒为 0 永不触发 → 必须退化为绝对下限"""
        out = detect_anomalies(
            batch={"inserted": 500, "date_stats": _stats(0, 500), "label": "x"},
            baseline_sizes=[0, 0, 0, 0])
        assert [a["rule"] for a in out] == ["A-batch-size"]

    def test_no_history_skips_size_rule(self):
        """冷启动(无历史基线)不判体量, 避免新环境首个大导入被误报"""
        out = detect_anomalies(
            batch={"inserted": 99999, "date_stats": _stats(0, 100), "label": "x"},
            baseline_sizes=[])
        assert not [a for a in out if a["rule"] == "A-batch-size"]

    def test_threshold_boundary(self):
        """占比恰达阈值 → 命中(>=)"""
        n = 100
        hit = int(DATE_ANOMALY_RATIO_THRESHOLD * n)
        out = detect_anomalies(
            batch={"inserted": 1, "date_stats": _stats(hit, n), "label": "x"},
            baseline_sizes=self.HIST)
        assert [a["rule"] for a in out] == ["B-date-ratio"]

    def test_just_below_threshold_no_alert(self):
        n = 100
        below = int(DATE_ANOMALY_RATIO_THRESHOLD * n) - 1
        out = detect_anomalies(
            batch={"inserted": 1, "date_stats": _stats(below, n), "label": "x"},
            baseline_sizes=self.HIST)
        assert out == []

    def test_all_rules_carry_detail(self):
        out = detect_anomalies(
            batch={"inserted": 2434, "date_stats": _stats(1896, 2520), "label": "x"},
            baseline_sizes=self.HIST)
        for a in out:
            assert a["detail"], a["rule"]
            assert a["message"], a["rule"]


# ---------- 接入生产链路 ----------
class TestWiredIntoProduction:
    def test_complaint_sync_calls_batch_check(self):
        """★接线锁: 检测必须真的挂在投诉同步链路上, 否则形同虚设"""
        import inspect

        from app.services import complaint_sync as cs

        src = inspect.getsource(cs)
        assert "check_complaint_batch" in src, "投诉同步未接入批次异常检测"

    def test_check_failure_does_not_break_sync(self):
        """检测异常不得影响入库主流程(必须是 try/except 包裹)"""
        import inspect

        from app.services import complaint_sync as cs

        src = inspect.getsource(cs)
        idx = src.find("check_complaint_batch")
        assert idx > 0
        # 调用点前后应有 try/except
        window = src[max(0, idx - 600): idx + 400]
        assert "except" in window, "批次检测未做异常隔离"

    def test_emit_only_notifies_on_critical(self):
        """warning 级不落事务通知(防刷屏), critical 才写"""
        import inspect

        from app.services import ingest_anomaly as ia

        src = inspect.getsource(ia._emit)
        assert 'a["severity"] == "critical"' in src

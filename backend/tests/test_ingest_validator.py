# -*- coding: utf-8 -*-
"""入库前校验规则测试 (#42, 2026-09-10)

背景: 采集链路原先对坏值"静默置空"(如时间解析失败→None), 问题不可见、事后才发现。
本模块提供入库前校验: 输出问题清单, 并按处置级别(block/warn)**落库打标**进治理队列。
**不丢弃数据** —— 实证: 一律拒收会丢 8.6%~26.6% 台账行(含 315 平台 2001~2019 真实台账)。

本测试锁定四类规则:
1. R-必填: 主时间锚点/关键业务键缺失 (block)
2. R-时间: 未来时间 / 纪元伪日期 / 时间倒挂 (warn)
3. R-枚举: result 值不在预期集合 (warn)
4. R-金额: 已退款但金额 <=0 或非数值 (warn)

附: TestSeverityGrading 锁定处置分级; TestQualityTagging 锁定打标口径。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.ingest_validator import (
    blocking_issues,
    summarize_issues,
    summarize_severity,
    validate_card_complaint_row,
    validate_external_complaint_row,
    validate_manual_refund_row,
    validate_rows,
)

_UTC = timezone.utc
_NOW = datetime.now(_UTC)
_PAST = _NOW - timedelta(days=30)


# ---------- R-必填 ----------
class TestRequired:
    def test_missing_complaint_date(self):
        """投诉日期缺失 → R-必填"""
        issues = validate_external_complaint_row({"complaint_date": None}, 0)
        assert any(i["rule"] == "R-必填" and i["field"] == "complaint_date" for i in issues)

    def test_manual_refund_missing_order_no(self):
        issues = validate_manual_refund_row({"order_no": None, "register_time": _PAST}, 0)
        assert any(i["rule"] == "R-必填" and i["field"] == "order_no" for i in issues)

    def test_present_values_no_required_issue(self):
        issues = validate_external_complaint_row({"complaint_date": _PAST, "result": "和解结案"}, 0)
        assert not [i for i in issues if i["rule"] == "R-必填"]


# ---------- R-时间 ----------
class TestTime:
    def test_future_time_flagged(self):
        """未来时间 → R-时间"""
        future = _NOW + timedelta(days=10)
        issues = validate_external_complaint_row({"complaint_date": future}, 0)
        assert any(i["rule"] == "R-时间" and "未来" in i["message"] for i in issues)

    def test_pseudo_date_flagged(self):
        """伪日期(1970 纪元, 历史 Excel 序列号 bug 产物) → R-时间

        abc-a 修正: 伪日期判定改为"是否落在 1970 纪元"(而非"年份早于 2020"),
        因为 315 平台存在 2001~2019 的**真实历史台账**。
        """
        pseudo = datetime(1969, 12, 31, 16, 0, tzinfo=_UTC)
        issues = validate_external_complaint_row({"complaint_date": pseudo}, 0)
        assert any(i["rule"] == "R-时间" and "纪元伪日期" in i["message"] for i in issues)

    def test_historical_date_not_flagged(self):
        """历史投诉(2023)是真实数据, 不应误报"""
        hist = datetime(2023, 6, 1, tzinfo=_UTC)
        issues = validate_external_complaint_row({"complaint_date": hist, "result": "和解结案"}, 0)
        assert not [i for i in issues if i["rule"] == "R-时间"]

    def test_historical_early_2000s_not_flagged(self):
        """★abc-a 回归锁: 2001~2005 年代的历史台账也不得误报

        实证: 本地库 complaint_date 落在 2001~2019 共 1556 行且逐年均匀分布
        (每年 60~97 行) → 是真实台账。旧口径("<2020 即异常")会把这 1556 行
        全部误报为问题行, 使治理队列 38.6% 被噪声淹没。
        """
        for year in (2001, 2005, 2012, 2019):
            hist = datetime(year, 6, 1, tzinfo=_UTC)
            issues = validate_external_complaint_row(
                {"complaint_date": hist, "result": "和解结案"}, 0)
            assert not [i for i in issues if i["rule"] == "R-时间"], f"{year} 不应误报"

    def test_inverted_time_flagged(self):
        """处理时间早于投诉日期 1 天以上 → R-时间(倒挂)"""
        cd = _PAST
        ht = cd - timedelta(days=5)
        issues = validate_external_complaint_row({"complaint_date": cd, "handle_time": ht}, 0)
        assert any(i["rule"] == "R-时间" and "早于" in i["message"] for i in issues)

    def test_same_day_not_inverted(self):
        """同日(或相差<1天)不算倒挂"""
        cd = datetime(2026, 6, 14, 0, 0, tzinfo=_UTC)
        ht = datetime(2026, 6, 14, 12, 0, tzinfo=_UTC)
        issues = validate_external_complaint_row({"complaint_date": cd, "handle_time": ht}, 0)
        assert not [i for i in issues if "早于" in i["message"]]

    def test_manual_refund_op_before_register(self):
        reg = _PAST
        op = reg - timedelta(days=3)
        issues = validate_manual_refund_row({"order_no": "RF1", "register_time": reg, "op_time": op}, 0)
        assert any("早于" in i["message"] for i in issues)


# ---------- R-枚举 ----------
class TestEnum:
    def test_unknown_result_flagged(self):
        issues = validate_external_complaint_row(
            {"complaint_date": _PAST, "result": "已解决但未结案"}, 0)
        assert any(i["rule"] == "R-枚举" and i["field"] == "result" for i in issues)

    def test_known_result_ok(self):
        for r in ("和解结案", "撤销结案", "重复投诉", "退回消委会"):
            issues = validate_external_complaint_row({"complaint_date": _PAST, "result": r}, 0)
            assert not [i for i in issues if i["rule"] == "R-枚举"], f"{r} 不应误报"

    def test_empty_result_not_flagged(self):
        """result 为空是常态(未办结), 不算枚举问题"""
        issues = validate_external_complaint_row({"complaint_date": _PAST, "result": None}, 0)
        assert not [i for i in issues if i["rule"] == "R-枚举"]

    def test_card_result_not_enum_checked(self):
        """卡商 result 是另一套语义, 不做枚举校验"""
        issues = validate_card_complaint_row({"complaint_date": _PAST, "result": "流量虚"}, 0)
        assert not [i for i in issues if i["rule"] == "R-枚举"]


# ---------- R-金额 ----------
class TestAmount:
    def test_refunded_with_zero_amount_flagged(self):
        issues = validate_manual_refund_row(
            {"order_no": "RF1", "register_time": _PAST, "is_refunded": "是", "refund_amount": 0}, 0)
        assert any(i["rule"] == "R-金额" for i in issues)

    def test_refunded_with_non_numeric_amount(self):
        issues = validate_manual_refund_row(
            {"order_no": "RF1", "register_time": _PAST, "is_refunded": "是", "refund_amount": "待定"}, 0)
        assert any(i["rule"] == "R-金额" for i in issues)

    def test_not_refunded_skips_amount_check(self):
        """未退款行不校验金额(金额本可为空)"""
        issues = validate_manual_refund_row(
            {"order_no": "RF1", "register_time": _PAST, "is_refunded": "否", "refund_amount": None}, 0)
        assert not [i for i in issues if i["rule"] == "R-金额"]

    def test_positive_amount_ok(self):
        issues = validate_manual_refund_row(
            {"order_no": "RF1", "register_time": _PAST, "is_refunded": "是", "refund_amount": 199.5}, 0)
        assert not [i for i in issues if i["rule"] == "R-金额"]


# ---------- 批量与聚合 ----------
class TestBatch:
    def test_validate_rows_batch(self):
        rows = [
            {"complaint_date": _PAST, "result": "和解结案"},
            {"complaint_date": None},                      # 缺日期
            {"complaint_date": _PAST, "result": "未知状态"},  # 枚举
        ]
        issues = validate_rows(rows, "external_complaint")
        assert len(issues) == 2
        assert {i["row"] for i in issues} == {1, 2}

    def test_unregistered_type_returns_empty(self):
        """未注册校验器的类型: 返回空清单, 不阻塞入库"""
        assert validate_rows([{"x": 1}], "refund") == []

    def test_empty_rows(self):
        assert validate_rows([], "external_complaint") == []

    def test_summarize(self):
        issues = [
            {"rule": "R-时间"}, {"rule": "R-时间"}, {"rule": "R-必填"},
        ]
        assert summarize_issues(issues) == {"R-时间": 2, "R-必填": 1}

    def test_validator_exception_does_not_raise(self):
        """单行校验异常必须被吞掉(校验不得阻塞入库)"""
        bad = {"complaint_date": object()}  # 非法类型
        assert isinstance(validate_rows([bad], "external_complaint"), list)


class TestWiredIntoProduction:
    """N-3 回归锁: 校验器必须真正接入生产链路(而非仅存在却零调用)

    背景(复审 N-3): validate_rows / validate_manual_refund_row 曾仅被测试引用,
    生产零调用 → 校验形同虚设。本类断言各入口确实 import 并调用了校验函数。
    """

    def test_manual_refund_channel_imports_validator(self):
        """手动退款同步模块必须导入并调用 validate_manual_refund_row"""
        import inspect

        from app.services import manual_refund_sync as mrs

        src = inspect.getsource(mrs)
        assert "validate_manual_refund_row" in src, "manual_refund_sync 未接入校验器"
        assert "validation" in src, "manual_refund_sync 未上报 validation"
        assert "validation_severity" in src, "manual_refund_sync 未上报分级计数"

    def test_complaint_channel_imports_validator(self):
        """投诉同步模块必须导入并调用两个逐行校验函数"""
        import inspect

        from app.services import complaint_sync as cs

        src = inspect.getsource(cs)
        assert "validate_external_complaint_row" in src
        assert "validate_card_complaint_row" in src
        assert "add_validation" in src, "投诉通道未走独立 validation 通道"
        assert "validation_severity" in src, "投诉通道未上报分级计数"

    def test_manual_refund_row_validate_returns_issues(self):
        """手动退款校验器对问题行确实产出问题(端到端生效的语义基础)"""
        issues = validate_manual_refund_row({
            "order_no": "RF1", "register_time": _PAST,
            "is_refunded": "是", "refund_amount": 0,
        }, 0)
        rules = {i["rule"] for i in issues}
        assert "R-金额" in rules


class TestSeverityGrading:
    """abc-a: 处置分级 —— 区分"不可修复/优先处理"(block) 与"可修复/常规"(warn)

    设计依据(实证): 一律拒收会丢 8.6%~26.6% 台账行, 其中大量是真实历史数据;
    改为"入库打标 + 分层治理", 用 severity 把不可修复项提到队首。
    """

    def test_missing_anchor_is_block(self):
        """主时间锚点缺失 → 不可修复 → block"""
        issues = validate_external_complaint_row({"complaint_date": None}, 0)
        hits = [i for i in issues if i["field"] == "complaint_date"]
        assert hits and all(i.get("severity") == "block" for i in hits)

    def test_missing_order_no_is_block(self):
        issues = validate_manual_refund_row({"order_no": None, "register_time": _PAST}, 0)
        hits = [i for i in issues if i["field"] == "order_no"]
        assert hits and hits[0].get("severity") == "block"

    def test_time_and_enum_are_warn(self):
        """时间越界/倒挂/枚举脏值 → 可修复或待复核 → warn"""
        future = _NOW + timedelta(days=10)
        issues = validate_external_complaint_row(
            {"complaint_date": future, "result": "未知结果值"}, 0)
        sevs = {i["rule"]: i.get("severity") for i in issues}
        assert sevs.get("R-时间") == "warn"
        assert sevs.get("R-枚举") == "warn"

    def test_summarize_severity_counts(self):
        issues = [
            {"rule": "R-必填", "severity": "block"},
            {"rule": "R-时间", "severity": "warn"},
            {"rule": "R-时间", "severity": "warn"},
        ]
        assert summarize_severity(issues) == {"block": 1, "warn": 2}

    def test_summarize_severity_defaults_to_warn(self):
        """未标注 severity 的历史条目按 warn 计(向后兼容, 不会静默丢失)"""
        assert summarize_severity([{"rule": "R-时间"}]) == {"block": 0, "warn": 1}

    def test_blocking_issues_filters(self):
        issues = [
            {"rule": "R-必填", "severity": "block"},
            {"rule": "R-时间", "severity": "warn"},
            {"rule": "R-校验器", "field": None},
        ]
        got = blocking_issues(issues)
        assert len(got) == 2
        assert {i["rule"] for i in got} == {"R-必填", "R-校验器"}

    def test_validator_error_marked_warn(self):
        """校验器自身异常条目也要有 severity(否则前端分级展示会漏)"""

        class _Boom:
            def __str__(self):  # 在 R-枚举 的 str(result) 处抛错 → 被 validate_rows 捕获
                raise RuntimeError("boom")

        out = validate_rows([{"complaint_date": _PAST, "result": _Boom()}], "external_complaint")
        errs = [i for i in out if i["rule"] == "R-校验器"]
        assert errs and errs[0].get("severity") == "warn"


class TestQualityTagging:
    """abc-a 回归锁: 校验结果必须**落库打标**(而非仅停在返回值/日志)

    背景: #42 一期校验只可见化(返回 validation), 数据质量看板看不到这些行,
    治理队列为空 → 校验形同"只报告不建账"。本期把它接进 quality_status。
    """

    def _tag(self, rec, issues, result=None):
        from app.services.complaint_sync import _tag_quality

        class _R:
            pass

        _tag_quality(rec, issues, result or _R())
        return rec

    def test_issue_marks_pending(self):
        """有问题 → quality_status=pending + note 带类别标签"""
        rec = self._tag({}, [{"rule": "R-时间", "message": "投诉日期为未来时间(2027-01-01)"}])
        assert rec["quality_status"] == "pending"
        assert rec["quality_note"].startswith("[时间异常]")

    def test_block_and_warn_both_pending(self):
        """block 与 warn 都进治理队列(优先级差异由 severity 字段表达)"""
        rec = self._tag({}, [
            {"rule": "R-必填", "severity": "block", "message": "投诉日期缺失"},
            {"rule": "R-枚举", "severity": "warn", "message": "结果值不在预期集合"},
        ])
        assert rec["quality_status"] == "pending"
        assert "必填缺失" in rec["quality_note"] and "枚举脏值" in rec["quality_note"]

    def test_no_issue_leaves_none(self):
        """无问题 → 不打标(保持 None, 不进治理队列)"""
        rec = self._tag({}, [])
        assert rec.get("quality_status") is None
        assert rec.get("quality_note") is None

    def test_manual_ok_not_overwritten(self):
        """★覆盖保护: 人工已确认(ok)的行不得被同步反复打回 pending"""
        rec = self._tag({"quality_status": "ok"}, [{"rule": "R-时间", "message": "x"}])
        assert rec["quality_status"] == "ok", "人工确认结果被覆盖"

    def test_manual_ignored_not_overwritten(self):
        """★覆盖保护: 人工已忽略(ignored)的行不得被重新打标"""
        rec = self._tag({"quality_status": "ignored"}, [{"rule": "R-时间", "message": "x"}])
        assert rec["quality_status"] == "ignored"

    def test_note_is_deterministic(self):
        """note 生成必须稳定(否则每轮同步都判定"内容变了"→ 无意义更新)"""
        issues = [{"rule": "R-时间", "message": "b"}, {"rule": "R-必填", "message": "a"}]
        a = self._tag({}, issues)
        b = self._tag({}, list(reversed(issues)))
        assert a["quality_note"] == b["quality_note"]

    def test_note_truncated_to_column_width(self):
        """note 必须截断到列宽内(String(512)), 否则 DB 报错丢整批"""
        rec = self._tag({}, [{"rule": "R-时间", "message": "长" * 400}] * 3)
        assert len(rec["quality_note"]) <= 500

    def test_wired_into_complaint_channel(self):
        """回归锁: 两个校验调用点都必须调 _tag_quality(否则打标只做了一半)"""
        import inspect

        from app.services import complaint_sync as cs

        src = inspect.getsource(cs)
        assert "def _tag_quality" in src
        # 315 与 卡商 两条 sheet 处理各一次
        assert src.count("_tag_quality(rec,") >= 2, "有 sheet 通道漏了打标"

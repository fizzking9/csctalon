# -*- coding: utf-8 -*-
"""入库前校验规则测试 (#42, 2026-09-10)

背景: 采集链路原先对坏值"静默置空"(如时间解析失败→None), 问题不可见、事后才发现。
本模块提供只读校验(advisory): 输出问题清单供展示/治理, **不丢弃、不改数据**。

本测试锁定四类规则:
1. R-必填: 主时间锚点/关键业务键缺失
2. R-时间: 未来时间 / 伪日期(远古年份) / 时间倒挂
3. R-枚举: result 值不在预期集合
4. R-金额: 已退款但金额 <=0 或非数值
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.ingest_validator import (
    summarize_issues,
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
        """伪日期(1969, 历史 Excel 序列号 bug 产物) → R-时间"""
        pseudo = datetime(1969, 12, 31, 16, 0, tzinfo=_UTC)
        issues = validate_external_complaint_row({"complaint_date": pseudo}, 0)
        assert any(i["rule"] == "R-时间" and "年份异常" in i["message"] for i in issues)

    def test_historical_date_not_flagged(self):
        """历史投诉(2023)是真实数据, 不应误报"""
        hist = datetime(2023, 6, 1, tzinfo=_UTC)
        issues = validate_external_complaint_row({"complaint_date": hist, "result": "和解结案"}, 0)
        assert not [i for i in issues if i["rule"] == "R-时间"]

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

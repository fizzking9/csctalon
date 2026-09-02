# -*- coding: utf-8 -*-
"""审计时间轴构建测试(audit_timeline.py)。

覆盖:
- 纯工具函数: _dt_iso(时间→ISO) / _amount(Numeric→float)
- 纯转换函数: _build_refund_items / _build_ticket_items / _build_wallet_items
  (ORM 行 → 时间轴 item, 含 person_name/device_sn 开关)
"""

from datetime import datetime, timezone

from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.models.wallet import WalletWithdrawal
from app.services.audit_timeline import (
    _amount,
    _build_refund_items,
    _build_ticket_items,
    _build_wallet_items,
    _dt_iso,
)

# ---- _dt_iso: datetime → ISO 字符串 ----

def test_dt_iso_none():
    """边界: None → None。"""
    assert _dt_iso(None) is None


def test_dt_iso_aware():
    """正向: aware datetime → ISO(含时区偏移)。"""
    dt = datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)
    assert _dt_iso(dt) == "2026-09-01T08:30:00+00:00"


def test_dt_iso_naive_treated_as_utc():
    """边界: naive datetime 兜底视为 UTC(不崩)。"""
    dt = datetime(2026, 9, 1, 8, 30)
    iso = _dt_iso(dt)
    assert iso.startswith("2026-09-01T08:30:00")
    assert "+00:00" in iso  # 已补充 tzinfo


# ---- _amount: Numeric → float ----

def test_amount_none():
    """边界: None → None。"""
    assert _amount(None) is None


def test_amount_zero_kept():
    """边界: 0 是有效金额, 保留为 0.0(不转 None)。"""
    assert _amount(0) == 0.0


def test_amount_invalid():
    """边界: 无法转 float 的值 → None。"""
    assert _amount("abc") is None


def test_amount_decimal_str():
    """正向: 字符串金额转 float。"""
    assert _amount("123.45") == 123.45


# ---- _build_refund_items ----

def _refund(**kw) -> Refund:
    return Refund(
        refund_no=kw.get("refund_no", "RF0000000001"),
        refund_method=kw.get("refund_method", "微信"),
        status=kw.get("status", "已退款"),
        operator=kw.get("operator", "张三"),
        auditor=kw.get("auditor", "李四"),
        refund_reason=kw.get("refund_reason", "用户申请"),
        refund_amount=kw.get("refund_amount", 100),
        created_at_raw=kw.get("created_at_raw", datetime(2026, 9, 1, tzinfo=timezone.utc)),
        device_sn=kw.get("device_sn", "SN-001"),
    )


def test_build_refund_items_default():
    """正向: 默认组装 refund item, 含操作人/审核人。"""
    items = _build_refund_items([_refund()])
    assert len(items) == 1
    it = items[0]
    assert it["type"] == "refund"
    assert it["ref_id"] == "RF0000000001"
    assert it["title"] == "退费单 RF0000000001"
    assert it["amount"] == 100.0
    assert it["person_names"] == ["张三", "李四"]
    assert it["summary"] == "微信 · 已退款 · 操作:张三 · 用户申请"
    # 默认不包含 device_sn
    assert it["device_sn"] is None
    assert "设备SN" not in it["fields"]


def test_build_refund_items_include_device_sn():
    """边界: include_device_sn=True 时包含设备SN。"""
    it = _build_refund_items([_refund()], include_device_sn=True)[0]
    assert it["device_sn"] == "SN-001"
    assert it["fields"]["设备SN"] == "SN-001"


def test_build_refund_items_no_person_name():
    """边界: include_person_name=False 时不收集操作人/审核人。"""
    it = _build_refund_items([_refund()], include_person_name=False)[0]
    assert it["person_names"] is None
    assert "操作:张三" not in it["summary"]


# ---- _build_ticket_items ----

def _ticket(**kw) -> ServiceTicket:
    return ServiceTicket(
        ticket_no=kw.get("ticket_no", "GDC0001"),
        service_category=kw.get("service_category", "退款"),
        service_item=kw.get("service_item", "退费申请"),
        status=kw.get("status", "已完成"),
        handler=kw.get("handler", "王五"),
        registrant=kw.get("registrant", "赵六"),
        refund_amount=kw.get("refund_amount", 50),
        handle_duration=kw.get("handle_duration", 30),
        register_time=kw.get("register_time", datetime(2026, 9, 1, 9, tzinfo=timezone.utc)),
        finish_time=kw.get("finish_time", datetime(2026, 9, 1, 10, tzinfo=timezone.utc)),
        device_sn=kw.get("device_sn", "SN-002"),
    )


def test_build_ticket_items_uses_finish_time():
    """正向: ticket item 的 time 用 finish_time(无则用 register_time)。"""
    it = _build_ticket_items([_ticket()])[0]
    assert it["type"] == "ticket"
    assert it["ref_id"] == "GDC0001"
    assert it["time"] == datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
    assert it["fields"]["处理时长(分)"] == 30
    assert it["person_names"] == ["王五", "赵六"]


def test_build_ticket_items_fallback_register_time():
    """边界: finish_time 为 None 时退回 register_time。"""
    t = _ticket(finish_time=None)
    it = _build_ticket_items([t])[0]
    assert it["time"] == datetime(2026, 9, 1, 9, tzinfo=timezone.utc)
    assert it["fields"]["完成时间"] is None


# ---- _build_wallet_items ----

def _wallet(**kw) -> WalletWithdrawal:
    w = WalletWithdrawal(
        op_type=kw.get("op_type", "用户提现"),
        op_amount=kw.get("op_amount", 200),
        wallet_balance=kw.get("wallet_balance", 500),
        operator=kw.get("operator", "客服甲"),
        account_id=kw.get("account_id", "ACC-1"),
        op_time=kw.get("op_time", datetime(2026, 9, 1, 11, tzinfo=timezone.utc)),
        remark=kw.get("remark", "提现到银行卡"),
        device_sn=kw.get("device_sn", "SN-003"),
    )
    w.id = kw.get("id", 42)
    return w


def test_build_wallet_items():
    """正向: wallet item 组装正确, ref_id 带 W 前缀。"""
    it = _build_wallet_items([_wallet()])[0]
    assert it["type"] == "wallet"
    assert it["ref_id"] == "W42"
    assert it["amount"] == 200.0
    assert it["fields"]["钱包余额"] == 500.0
    assert it["person_names"] == ["客服甲"]
    assert it["summary"] == "用户提现 · 操作:客服甲"

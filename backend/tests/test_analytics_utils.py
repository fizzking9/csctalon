# -*- coding: utf-8 -*-
"""分析引擎纯逻辑函数测试(analytics.py)。

覆盖无 DB 依赖的纯函数(手机号清洗/日期范围/失败备注判断),
锁定与业务规范一致的关键口径。
"""

from datetime import datetime, timedelta

from app.services.analytics import (
    _clean_phone,
    _date_range,
    _is_withdraw_fail_remark,
)

# ---- _clean_phone: 手机号归一化 ----

def test_clean_phone_normal():
    """正向: 11 位 1 开头的手机号原样返回。"""
    assert _clean_phone("13812345678") == "13812345678"


def test_clean_phone_with_separators():
    """正向: 去除空格/括号/连字符后归一化。"""
    assert _clean_phone("138-1234-5678") == "13812345678"
    assert _clean_phone("(138) 1234 5678") == "13812345678"


def test_clean_phone_strip_country_code():
    """正向: 去掉 +86/86/0086 前缀。"""
    assert _clean_phone("+8613812345678") == "13812345678"
    assert _clean_phone("8613812345678") == "13812345678"
    assert _clean_phone("008613812345678") == "13812345678"


def test_clean_phone_none_empty():
    """边界: None / 空串 / 纯空白 → None。"""
    assert _clean_phone(None) is None
    assert _clean_phone("") is None
    assert _clean_phone("   ") is None


def test_clean_phone_masked():
    """边界: 含掩码(****/XXXX) → None(不进去重)。"""
    assert _clean_phone("138****5678") is None
    assert _clean_phone("138XXXX5678") is None


def test_clean_phone_invalid_length_or_prefix():
    """边界: 长度非 11 或非 1 开头 → None。"""
    assert _clean_phone("23812345678") is None  # 2 开头
    assert _clean_phone("1381234567") is None   # 10 位
    assert _clean_phone("138123456789") is None  # 12 位


# ---- _date_range: 默认时间范围 ----

def test_date_range_both_provided():
    """正向: 同时给 start/end 原样返回。"""
    s, e = datetime(2026, 9, 1), datetime(2026, 9, 30)
    assert _date_range(s, e) == (s, e)


def test_date_range_default_end_now():
    """边界: 只给 start, end 默认当前时间。"""
    s = datetime(2026, 9, 1)
    start, end = _date_range(s, None)
    assert start == s
    assert end is not None
    assert end > s


def test_date_range_default_both():
    """边界: 都不给, 默认近 30 天。"""
    start, end = _date_range(None, None)
    assert end is not None
    assert start == end - timedelta(days=30)


# ---- _is_withdraw_fail_remark: 失败/冻结备注判断 ----

def test_withdraw_fail_remark_hits_keywords():
    """正向: 命中失败关键词(冻结/退款失败/驳回等) → True。"""
    assert _is_withdraw_fail_remark("账户冻结") is True
    assert _is_withdraw_fail_remark("退款失败, 余额不足") is True
    assert _is_withdraw_fail_remark("驳回: 超期订单不允许退款") is True


def test_withdraw_fail_remark_plain():
    """边界: 正常提现备注 → False。"""
    assert _is_withdraw_fail_remark("提现成功到账") is False
    assert _is_withdraw_fail_remark("") is False
    assert _is_withdraw_fail_remark(None) is False

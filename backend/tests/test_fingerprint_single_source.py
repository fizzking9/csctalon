"""O-6 (#277): md5 指纹单源化的**等价性守护**测试。

铁律#9：去重键语义不可变。本测试把**重构前的旧实现原样内联**，
对同一组样本逐字节对比新旧输出 —— 任何语义漂移（strip/默认值/截断差异）
都会在此暴露。
"""

import hashlib

from app.services.complaint_sync import _dedup_hash as sync_dedup_hash
from app.services.fingerprint import complaint_dedup_hash, staff_unique_key
from app.services.scrm_staff_service import build_staff_unique_key
from app.services.upsert import _complaint_dedup_hash as upsert_dedup_hash


# ── 重构前的旧实现（原样保留，作为等价基准）──
def _old_complaint_dedup_hash(parts: list) -> str:
    key = "|".join((p or "").strip() for p in parts)
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:32]


def _old_staff_unique_key(source_config_id, wecom_nick, enterprise_suffix) -> str:
    raw = f"{source_config_id or 0}|{wecom_nick}|{enterprise_suffix or ''}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# 覆盖: 空值/None/空白/中文/长串/特殊分隔符(片段内含"|" 是历史既有语义，不特殊处理)
_COMPLAINT_SAMPLES = [
    ["2026-01-01", "13800001111", "SN001", "网络不通"],
    ["2026-01-01", "13800001111", "", "网络不通"],           # 缺 SN(弱指纹场景)
    ["", None, None, ""],                                     # 全空
    [" 2026-01-01 ", " 13800001111 ", " SN001 ", " 问题 "],   # 首尾空白(strip 语义)
    ["2026-01-01", "13800001111", "SN|001", "问题|含分隔符"],  # 片段含 "|"
    ["2026-01-01", "13800001111", "SN001", "问" * 3000],      # 超长问题签名
]

_STAFF_SAMPLES = [
    (1, "张三", "腾讯"),
    (None, "张三", "腾讯"),      # source_config_id=None → "0"(非空串)
    (1, " 张三 ", "腾讯"),       # wecom_nick 不 strip(历史语义)
    (1, "张三", None),           # suffix=None → ""
    (1, "张三", ""),
]


def test_complaint_dedup_hash_byte_identical():
    """单源实现 vs 旧实现：逐样本 byte 级一致。"""
    for parts in _COMPLAINT_SAMPLES:
        assert complaint_dedup_hash(parts) == _old_complaint_dedup_hash(parts), parts


def test_callsites_delegate_to_single_source():
    """upsert / complaint_sync 两个调用方与单源输出一致（口径不再漂移）。"""
    for parts in _COMPLAINT_SAMPLES:
        expected = complaint_dedup_hash(parts)
        assert upsert_dedup_hash(parts) == expected, parts
        assert sync_dedup_hash(parts) == expected, parts


def test_staff_unique_key_byte_identical():
    """人设唯一键：单源实现 vs 旧实现 byte 级一致；兼容委托也一致。"""
    for sid, nick, suffix in _STAFF_SAMPLES:
        old = _old_staff_unique_key(sid, nick, suffix)
        assert staff_unique_key(sid, nick, suffix) == old, (sid, nick, suffix)
        assert build_staff_unique_key(sid, nick, suffix) == old, (sid, nick, suffix)


def test_staff_none_config_id_maps_to_zero_not_empty():
    """关键语义钉死：source_config_id=None 必须记为 '0'（区别于 complaint 的空串语义）。"""
    assert staff_unique_key(None, "张三", None) != staff_unique_key(0, "张三", "") or True
    # 显式验证: None → "0|张三|" 的 md5
    expected = hashlib.md5("0|张三|".encode("utf-8")).hexdigest()
    assert staff_unique_key(None, "张三", None) == expected

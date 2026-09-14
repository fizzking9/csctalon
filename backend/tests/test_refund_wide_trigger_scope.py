# -*- coding: utf-8 -*-
"""退费宽表触发范围测试(2026-09-14, 工单 #88)

缺陷: 同步「设备套餐」(device_combo) 等**与退费宽表无关**的源, 也会触发 refund_wide
全量重建(生产实测 66,451 行 / 约 50s)。

根因(两处):
1. bms_sync.py `use_incremental` 要求 `len(all_affected) > 0` —— 于是「本次导入与宽表
   无关 → all_affected 为空」被误判为「降级全量」。空集的正确语义是**跳过**。
2. post-sync 只看 `any_success`, 不判断该源是否与宽表相关; uploads.py 更是无条件全量。

修复: 引入相关源白名单 `REFUND_WIDE_RELEVANT_SOURCES` + 纯函数
`decide_refund_wide_mode()`, 两处通道共用同一判据。

本测试锁定:
1. 白名单内容与宽表实际消费的上游表一一对应(防止误删/误加)
2. `refund_wide_relevant_sources` 只认「成功 + 有写入 + 属白名单」的源
3. `decide_refund_wide_mode` 三态判定, 尤其是「无关源 → skipped」
4. 保守性: 只要有相关源写入, 即使 affected 为空也不跳过(不可漏采)
"""

from __future__ import annotations

import pytest

from app.services.bms_sync import (
    REFUND_WIDE_RELEVANT_SOURCES,
    decide_refund_wide_mode,
    is_refund_wide_relevant,
    refund_wide_relevant_sources,
)


def _res(data_type: str, *, success: bool = True, ins: int = 0, upd: int = 0) -> dict:
    """构造一条 sync 结果(只含判定所需字段)"""
    return {
        "data_type": data_type,
        "success": success,
        "inserted_rows": ins,
        "updated_rows": upd,
    }


# ─────────────────────────── 1. 白名单内容锁定 ───────────────────────────


def test_whitelist_matches_wide_table_consumers():
    """白名单必须与 wide_table_builder 实际消费的上游表一致。

    宽表各列来源(对照 wide_table_builder._build_wide_batch):
      refunds               → 行本体 + refund_method/status/amount/...
      service_tickets       → ticket_no/service_item/service_category/handler
      wallet_withdrawals    → wallet_id/wallet_op_time/wallet_op_amount
      external_complaints   → has_external_complaint/count/latest_time
    漏一个 → 该源同步后宽表不刷新(数据陈旧); 多一个 → 又变成本单要修的浪费。
    """
    assert set(REFUND_WIDE_RELEVANT_SOURCES) == {
        "refund",
        "ticket",
        "ticket_finish",  # ticket 双维度抓取别名, 写同一张 service_tickets
        "wallet",
        "external_complaint",
    }
    # device_combo 是本单的案发现场, 必须**不在**白名单内
    assert not is_refund_wide_relevant("device_combo")
    # cs_record 等无关源同理
    assert not is_refund_wide_relevant("cs_record")
    assert not is_refund_wide_relevant("bms_order")
    # 白名单内
    assert is_refund_wide_relevant("refund")
    assert is_refund_wide_relevant("ticket_finish")
    # None/未知值不得被判为相关(防调用方漏传)
    assert not is_refund_wide_relevant(None)
    assert not is_refund_wide_relevant("不存在的源")


# ─────────────────── 2. refund_wide_relevant_sources 筛选 ───────────────────


def test_irrelevant_source_yields_empty():
    """★案发现场复现: 只同步 device_combo → 无相关源 → 应跳过"""
    results = [_res("device_combo", ins=3307, upd=78234)]
    assert refund_wide_relevant_sources(results) == []
    assert decide_refund_wide_mode([], 0, 0, 181018) == "skipped"


def test_relevant_source_detected():
    """相关源有写入 → 命中"""
    results = [
        _res("cs_record", ins=173, upd=1645),   # 无关源, 应被忽略
        _res("refund", ins=15, upd=289),        # 相关源
    ]
    assert refund_wide_relevant_sources(results) == ["refund"]


def test_relevant_source_with_zero_writes_is_ignored():
    """相关源但 0 行写入(如 BMS 查询结果为空) → 宽表不可能变, 不算相关"""
    results = [_res("refund", ins=0, upd=0)]
    assert refund_wide_relevant_sources(results) == []


def test_failed_relevant_source_is_ignored():
    """相关源同步失败 → 没有写入, 不算相关"""
    results = [_res("refund", success=False, ins=0, upd=0)]
    assert refund_wide_relevant_sources(results) == []


def test_relevant_sources_dedup_and_order():
    """多个相关源去重且保序(ticket + ticket_finish 同表, 两条都要保留判断)"""
    results = [
        _res("ticket", ins=1, upd=2),
        _res("wallet", ins=1, upd=0),
        _res("ticket", ins=5, upd=0),        # 重复, 应去重
        _res("external_complaint", ins=1, upd=0),
    ]
    assert refund_wide_relevant_sources(results) == ["ticket", "wallet", "external_complaint"]


def test_non_dict_result_tolerated():
    """结果列表里混入非 dict(防御性)不得抛错"""
    assert refund_wide_relevant_sources([None, "x", _res("refund", ins=1)]) == ["refund"]


# ─────────────────── 3. decide_refund_wide_mode 三态判定 ───────────────────


def test_skip_only_when_no_relevant_and_no_match():
    """跳过 = 无相关源写入 且 matcher 无新增(两个条件同时满足)"""
    assert decide_refund_wide_mode([], 0, 0, 100000) == "skipped"


def test_not_skip_when_matcher_added_relations():
    """★保守性: 无相关源写入, 但 matcher 新增了关系(如退款↔工单) → 不可跳过"""
    assert decide_refund_wide_mode([], 12, 12, 100000) == "incremental"


def test_not_skip_when_relevant_source_written_even_if_affected_empty():
    """★★ 最关键的保守性: 相关源导入过数据, 即使 affected 收集为空也**不可跳过**。

    场景: 导入了 refund 数据, 但 _collect_affected_from_imports 的时间窗口过滤
    没圈到那些行(属另一类潜在漏采问题)。此时若跳过 → 静默漏采。
    修复策略: 只要有相关源写入 → 退回全量兜底。
    """
    assert decide_refund_wide_mode(["refund"], 0, 0, 100000) == "full"


def test_incremental_when_small_impact():
    """影响面小 → 增量(保持既有阈值行为不变)"""
    # 295 / 181018 ≈ 0.16% <= 25%, 且总量 >= 2000
    assert decide_refund_wide_mode(["refund"], 23, 295, 181018) == "incremental"


def test_full_when_impact_exceeds_threshold():
    """影响面 > 25% → 全量"""
    assert decide_refund_wide_mode(["refund"], 50, 60000, 181018) == "full"


def test_full_when_total_too_small():
    """总量 < 2000 → 全量更高效(既有阈值行为)"""
    assert decide_refund_wide_mode(["refund"], 1, 100, 1500) == "full"


@pytest.mark.parametrize(
    ("rel", "match_cnt", "affected", "total", "expect"),
    [
        ([], 0, 0, 181018, "skipped"),          # 案发现场: device_combo
        (["ticket"], 0, 12, 181018, "incremental"),
        (["wallet"], 2, 5, 181018, "incremental"),
        (["external_complaint"], 0, 0, 181018, "full"),   # 相关源写入但 affected 空 → 全量兜底
        ([], 0, 0, 100, "skipped"),             # 小表也一样跳过(空集语义与表大小无关)
    ],
)
def test_mode_matrix(rel, match_cnt, affected, total, expect):
    """判定矩阵"""
    assert decide_refund_wide_mode(rel, match_cnt, affected, total) == expect


# ─────────────── 4. 上传导入通道: 无关源不得重建宽表 ───────────────


class _FakeSession:
    """最小可用的假 Session(上传通道只用到 commit/close)"""

    def __init__(self):
        self.committed = 0

    def commit(self):
        self.committed += 1

    def close(self):
        pass


def _run_upload_post_tasks(monkeypatch, data_type: str, match_stats: dict | None = None):
    """调用 uploads._run_post_import_tasks, 返回 (是否调用 build_refund_wide, 调用次数)"""
    import app.api.v1.uploads as up
    from app.services import wide_table_builder as wtb

    called = {"n": 0}

    def _fake_build(db, *a, **kw):
        called["n"] += 1
        return {"built": 0, "updated": 0, "skipped": 0, "deleted": 0}

    monkeypatch.setattr(up, "SessionLocal", _FakeSession, raising=False)
    monkeypatch.setattr(up, "match_all", lambda *a, **kw: (match_stats or {}), raising=False)
    monkeypatch.setattr(up, "extract_person_aliases", lambda *a, **kw: None, raising=False)
    monkeypatch.setattr(wtb, "build_refund_wide", _fake_build, raising=False)
    # 缓存失效用不到, 挡掉避免依赖 Redis
    monkeypatch.setattr(
        "app.core.redis_client.cache_invalidate_recent_days", lambda *a, **kw: 0, raising=False
    )

    up._run_post_import_tasks(data_type)
    return called["n"]


def test_upload_device_combo_does_not_rebuild_wide(monkeypatch):
    """★案发现场(上传通道): 上传「设备套餐」不得重建退费宽表。

    修复前此处是**无条件** `build_refund_wide(db)`, data_type 只用于打日志。
    """
    n = _run_upload_post_tasks(monkeypatch, "device_combo")
    assert n == 0, "上传设备套餐不应触发 refund_wide 重建(#88)"


@pytest.mark.parametrize("dt", ["cs_record", "bms_order", "device_profile", "merchant_refund"])
def test_upload_other_irrelevant_sources_do_not_rebuild_wide(monkeypatch, dt):
    """其它非宽表相关源同样不重建"""
    assert _run_upload_post_tasks(monkeypatch, dt) == 0


def test_upload_refund_does_rebuild_wide(monkeypatch):
    """相关源(refund)上传 → 仍必须重建(不可因修复而漏刷)"""
    assert _run_upload_post_tasks(monkeypatch, "refund") == 1


def test_upload_irrelevant_source_with_new_match_still_rebuilds(monkeypatch):
    """★保守性: 无关源导入但 matcher 新增了匹配关系 → 仍要重建(不可漏采)"""
    n = _run_upload_post_tasks(
        monkeypatch, "device_combo", match_stats={"_affected_refund_nos": {"RF0000000001"}}
    )
    assert n == 1

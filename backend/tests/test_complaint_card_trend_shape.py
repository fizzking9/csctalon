# -*- coding: utf-8 -*-
"""`card.trend` 元素形状契约 —— /complaints/summary 与 /complaints/analysis 必须一致。

## 背景（2026-09-24）

同一字段路径 `card.trend` 在两个接口的元素结构曾经不同：

- `/complaints/analysis`：直接透出 `_suppl_trend_rows` 的 `{day, total, overlap}`
- `/complaints/summary` ：`_merge_trend` 的 `{day, count}`

消费端按 `count` 取数时，在 analysis 会**静默拿到 None**（不报错、图表空白），
属"同名字段不同形状"的隐形陷阱（与 2026-09-01 `_merge_trend` 嵌套崩溃同源）。

## 现状

两处统一为 `{day, count}`（analysis 经 `_merge_trend([], _card_suppl_trend)` 生成）。

⚠️ 注意区分：**元素形状**已统一，但**统计口径**仍不同（非 bug，已核实）：
- analysis 的 `count` = 补充表净增（total - overlap），不含工单侧
- summary 的 `count` = 工单侧卡商 + 补充表净增（#65）

本测试只锁定"元素键一致"，不锁定两者数值相等 —— 口径统一属产品决策，需另立工单。
"""
from __future__ import annotations

import datetime as dt

from app.api.v1.complaints import complaint_analysis, complaint_summary
from app.core.redis_client import cache_invalidate_pattern
from app.models.card_complaint import CardComplaint
from app.models.device import Device

_TS = dt.datetime(2026, 8, 6, 12, 0, tzinfo=dt.timezone.utc)
_START = "2026-08-01"
_END = "2026-08-31"


def _card(db, sn, category, *, when=_TS):
    if sn and db.get(Device, sn) is None:
        db.add(Device(sn=sn))
        db.flush()
    db.add(CardComplaint(
        device_sn=sn, complainant_phone="13800138000", complaint_group="卡商群A",
        complaint_date=when, problem_category=category,
    ))
    db.flush()


def _data(r):
    """兼容 R 包装与裸 dict。"""
    return r.get("data", r) if isinstance(r, dict) else r.model_dump()["data"]


def _analysis(db):
    cache_invalidate_pattern("complaint:analysis:*")
    return _data(complaint_analysis(db=db, _=None, start=_START, end=_END))


def _summary(db):
    cache_invalidate_pattern("complaint:summary:*")
    return _data(complaint_summary(db=db, _=None, start=_START, end=_END))


def test_analysis_card_trend_element_keys(db):
    """analysis 的 card.trend 元素键必须恰为 {day, count}（不得再透出 total/overlap）。"""
    _card(db, "869750000000101", "网卡退费")
    _card(db, "869750000000102", "注销实名解绑")
    _card(db, "869750000000103", "网卡退费")

    rows = _analysis(db)["card"]["trend"]
    assert isinstance(rows, list) and rows, "卡商有数据时 card.trend 不应为空"
    assert all(set(r.keys()) == {"day", "count"} for r in rows), (
        f"元素键不符合契约 {{day,count}}: {rows[:2]}"
    )
    assert all(isinstance(r["count"], int) for r in rows)


def test_summary_card_trend_element_keys(db):
    """summary 的 card.trend 元素键必须恰为 {day, count}。"""
    _card(db, "869750000000201", "网卡退费")
    _card(db, "869750000000202", "网卡退费")

    rows = _summary(db)["card"]["trend"]
    assert isinstance(rows, list) and rows, "卡商有数据时 card.trend 不应为空"
    assert all(set(r.keys()) == {"day", "count"} for r in rows), (
        f"元素键不符合契约 {{day,count}}: {rows[:2]}"
    )
    assert all(isinstance(r["count"], int) for r in rows)


def test_card_trend_keys_identical_across_endpoints(db):
    """跨接口一致性: 同名字段 card.trend 的元素键集合必须完全相同。

    这是本契约的核心 —— 修复前 analysis 为 {day,total,overlap}、summary 为 {day,count}。
    """
    _card(db, "869750000000301", "网卡退费")

    json_rows = _analysis(db)["card"]["trend"]
    summary_rows = _summary(db)["card"]["trend"]

    assert json_rows and summary_rows
    assert set(json_rows[0].keys()) == set(summary_rows[0].keys()) == {"day", "count"}

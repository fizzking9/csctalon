# -*- coding: utf-8 -*-
"""quality_status 排除口径单点化 + 多路径等价性测试（工单 #201 / S-P0-1）。

背景: 审查发现「经营统计排除 dup_residual/ignored」存在多份并行实现:
  - ORM 真身: analytics/_core.py 的 A/B 路过滤 + analytics/cash.py 的 C/D 路聚合
  - 裸 SQL:  analytics/refund.py 决策概览 routes_daily / _window_totals / 导出汇总
  - 宽表:    cash_flow_wide_builder.py（A/B/C/D Python 侧跳过）
             wide_table_builder.py（refund_wide 行范围，修复前完全不排除）
  - 复核:    reconcile_check.py D 路
修复后全部引用 quality_check.STATS_EXCLUDED_QUALITY_STATUSES 及其 SQL/ORM 助手。

本测试锁定:
1. 排除集合只有一个字面量来源（各模块别名必须 is 同一对象）
2. ORM 聚合器(_cash_routes_total) 与裸 SQL 决策概览(routes_daily/summary) 四路
   count/amount 完全一致，且都不含 dup_residual/ignored 行
3. refund_wide 行范围/批次组装/残留子查询都不含被打标退费单
4. 质量页 ignore/restore 后触发宽表重建钩子（物理表名映射正确）
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.manual_refund import ManualRefund
from app.models.refund import Refund
from app.models.refund_wide import RefundWide
from app.models.shanmao_refund import ShanmaoRefund
from app.models.wallet import WalletWithdrawal
from app.services import cash_flow_wide_builder, quality_disposition_service
from app.services.analytics._core import _resolve_windows
from app.services.analytics.cash import _cash_routes_total
from app.services.analytics.refund import refund_decision_overview
from app.services.quality_check import (
    STATS_EXCLUDED_QUALITY_STATUSES,
    stats_quality_excluded_sql,
)
from app.services.wide_table_builder import (
    _build_wide_batch,
    _in_scope_refund_nos,
    _scope_refund_no_subquery,
)

# 选用远离其它用例常用窗口(2026-09)的日期, 避免同库残留数据干扰计数
_DAY = "2025-03-27"
# 上海时间 2025-03-27 12:00 = UTC 04:00（两路归日均落在同一上海自然日）
_T = datetime(2025, 3, 27, 4, 0, tzinfo=timezone.utc)


def _rno() -> str:
    return f"RF201{uuid.uuid4().hex[:8]}"


def _ono() -> str:
    return f"ON201{uuid.uuid4().hex[:10]}"


# ─────────────────────────── 1. 单源锁定 ───────────────────────────


def test_excluded_set_is_single_source():
    """排除集合真身只能在 quality_check；别名必须是同一对象（不是等值拷贝）。"""
    assert STATS_EXCLUDED_QUALITY_STATUSES == ("dup_residual", "ignored")
    assert cash_flow_wide_builder._EXCLUDED_QUALITY is STATS_EXCLUDED_QUALITY_STATUSES
    assert quality_disposition_service.EXCLUDED_STATES is STATS_EXCLUDED_QUALITY_STATUSES
    # SQL 助手按同一份集合渲染
    assert stats_quality_excluded_sql("m.quality_status") == (
        "COALESCE(m.quality_status,'') NOT IN ('dup_residual','ignored')"
    )
    # 处置服务探测 SQL 的 IN 列表也源自真身（程序常量，内联安全）
    assert quality_disposition_service._EXCL_SQL_LIST == "'dup_residual','ignored'"


# ─────────────────── 2. ORM 聚合器 vs 裸 SQL 概览等价 ───────────────────


def _seed_four_routes(db):
    """A/B/C/D 每路造 2 正常 + dup_residual + ignored 各 1（治理行金额刻意巨大）。

    时间字段刻意让两条路径的归日表达式重合:
      A: created_at_raw=_T, actual/updated=NULL
         → ORM(col_a 默认 created_at_raw) 与裸 SQL COALESCE(actual,updated,created) 同值
      B: op_time=_T, actual=NULL（同上）
      C: audit_time=_T, refund_time=NULL；actual_amount=refund_amount
         → ORM(audit_time/actual_amount) 与裸 SQL COALESCE 同值
      D: op_time=_T, actual_refund_time=NULL（ORM 用 COALESCE(op,register)，
         裸 SQL 用 COALESCE(actual,op,register)，actual 为空则同值）
    """
    # A 线上直退
    db.add_all([
        Refund(refund_no=_rno(), refund_method="线上", status="已退款",
               refund_amount=100, created_at_raw=_T),
        Refund(refund_no=_rno(), refund_method="线上", status="已退款",
               refund_amount=200, created_at_raw=_T),
        Refund(refund_no=_rno(), refund_method="线上", status="已退款",
               refund_amount=9999, created_at_raw=_T, quality_status="dup_residual"),
        Refund(refund_no=_rno(), refund_method="线上", status="已退款",
               refund_amount=9999, created_at_raw=_T, quality_status="ignored"),
    ])
    # B 钱包提现（account_id 唯一以避开 device_sn IS NULL 的部分唯一索引）
    db.add_all([
        WalletWithdrawal(account_id=f"ACC-{uuid.uuid4().hex[:6]}", op_type="用户提现",
                         op_amount=50, op_time=_T),
        WalletWithdrawal(account_id=f"ACC-{uuid.uuid4().hex[:6]}", op_type="用户提现",
                         op_amount=60, op_time=_T),
        WalletWithdrawal(account_id=f"ACC-{uuid.uuid4().hex[:6]}", op_type="用户提现",
                         op_amount=9999, op_time=_T, quality_status="dup_residual"),
        WalletWithdrawal(account_id=f"ACC-{uuid.uuid4().hex[:6]}", op_type="用户提现",
                         op_amount=9999, op_time=_T, quality_status="ignored"),
    ])
    # C 闪猫退款
    db.add_all([
        ShanmaoRefund(refund_no=_rno(), order_no=_ono(), status="已退款",
                      refund_amount=70, actual_amount=70, audit_time=_T, created_at=_T),
        ShanmaoRefund(refund_no=_rno(), order_no=_ono(), status="已退款",
                      refund_amount=80, actual_amount=80, audit_time=_T, created_at=_T),
        ShanmaoRefund(refund_no=_rno(), order_no=_ono(), status="已退款",
                      refund_amount=9999, actual_amount=9999, audit_time=_T,
                      created_at=_T, quality_status="dup_residual"),
        ShanmaoRefund(refund_no=_rno(), order_no=_ono(), status="已退款",
                      refund_amount=9999, actual_amount=9999, audit_time=_T,
                      created_at=_T, quality_status="ignored"),
    ])
    # D 手动退款
    db.add_all([
        ManualRefund(order_no=_ono(), merchant_id="1611876437", is_refunded="是",
                     refund_amount=90, op_time=_T, register_time=_T, registrant="测试员"),
        ManualRefund(order_no=_ono(), merchant_id="1611876437", is_refunded="是",
                     refund_amount=110, op_time=_T, register_time=_T, registrant="测试员"),
        ManualRefund(order_no=_ono(), merchant_id="1611876437", is_refunded="是",
                     refund_amount=9999, op_time=_T, register_time=_T,
                     registrant="测试员", quality_status="dup_residual"),
        ManualRefund(order_no=_ono(), merchant_id="1611876437", is_refunded="是",
                     refund_amount=9999, op_time=_T, register_time=_T,
                     registrant="测试员", quality_status="ignored"),
    ])
    db.flush()


def test_orm_aggregator_excludes_marked_rows(db):
    """ORM 真身路径: 四路只含正常行。"""
    _seed_four_routes(db)
    cur_s, cur_e = _resolve_windows(None, _DAY, _DAY)[:2]
    tot = _cash_routes_total(db, start=cur_s, end=cur_e)
    assert (tot["route_a"]["count"], tot["route_a"]["amount"]) == (2, 300.0)
    assert (tot["route_b"]["count"], tot["route_b"]["amount"]) == (2, 110.0)
    assert (tot["route_c"]["count"], tot["route_c"]["amount"]) == (2, 150.0)
    assert (tot["route_d"]["count"], tot["route_d"]["amount"]) == (2, 200.0)
    # 经营四路总量 = 8 笔 / ¥760（任一治理行漏排都会冲到 9000+）
    assert (tot["operational"]["count"], tot["operational"]["amount"]) == (8, 760.0)


def test_raw_sql_overview_matches_orm_aggregator(db):
    """★等价性核心: 裸 SQL 决策概览与 ORM 聚合器四路口径必须完全一致。"""
    _seed_four_routes(db)
    cur_s, cur_e = _resolve_windows(None, _DAY, _DAY)[:2]
    orm_tot = _cash_routes_total(db, start=cur_s, end=cur_e)

    ov = refund_decision_overview(db, start=_DAY, end=_DAY)
    series = ov["routes_daily"]["series"]
    raw_counts = {k: sum(v["counts"]) for k, v in series.items()}
    raw_amts = {k: round(sum(v["amounts"]), 2) for k, v in series.items()}

    expect = {
        "A 线上直退": orm_tot["route_a"],
        "B 钱包提现": orm_tot["route_b"],
        "C 闪猫退款": orm_tot["route_c"],
        "D 手动退款": orm_tot["route_d"],
    }
    for k, v in expect.items():
        assert raw_counts[k] == v["count"], f"{k} 笔数: 裸SQL={raw_counts[k]} ORM={v['count']}"
        assert raw_amts[k] == v["amount"], f"{k} 金额: 裸SQL={raw_amts[k]} ORM={v['amount']}"

    # summary(_window_totals 第三套裸 SQL) 同样对齐
    s = ov["summary"]
    assert s["online_count"] == 2
    assert s["online_amount"] == 300.0
    assert s["withdraw_amount"] == 110.0
    assert s["cash_out_count"] == 8
    assert s["cash_out_total"] == 760.0


# ─────────────────── 3. refund_wide 行范围排除 ───────────────────


def test_refund_wide_scope_excludes_quality_marked(db):
    """refund_wide 行范围/批次组装/残留子查询三处都不含 dup_residual/ignored 退费单。"""
    rno_ok, rno_dup, rno_ign = _rno(), _rno(), _rno()
    db.add_all([
        Refund(refund_no=rno_ok, refund_method="线上", status="已退款",
               refund_amount=100, created_at_raw=_T),
        Refund(refund_no=rno_dup, refund_method="线上", status="已退款",
               refund_amount=9999, created_at_raw=_T, quality_status="dup_residual"),
        Refund(refund_no=rno_ign, refund_method="线上", status="已退款",
               refund_amount=9999, created_at_raw=_T, quality_status="ignored"),
    ])
    db.flush()

    # 3a. Python 集合版行范围
    scope = _in_scope_refund_nos(db)
    assert rno_ok in scope
    assert rno_dup not in scope
    assert rno_ign not in scope

    # 3b. 残留清理子查询版行范围（同一口径的 SQL 表达）
    sub = _scope_refund_no_subquery()
    sub_nos = set(db.scalars(select(sub.c.refund_no)).all())
    assert sub_nos == scope

    # 3c. 即使增量直调把治理行塞进批次, 组装时也必须跳过
    res = _build_wide_batch(db, [rno_ok, rno_dup, rno_ign], commit=False)
    assert res["built"] == 1
    assert res["skipped"] == 2
    assert db.get(RefundWide, rno_ok) is not None
    assert db.get(RefundWide, rno_dup) is None
    assert db.get(RefundWide, rno_ign) is None


# ───────────────── 4. ignore/restore 触发宽表重建钩子 ─────────────────


def test_ignore_and_restore_trigger_post_execute(db, monkeypatch):
    """质量页直接处置后必须调用治理后置（宽表重建+缓存失效），物理表名映射正确。"""
    calls: list[str] = []

    def _fake_post_execute(sess, table_name: str):
        calls.append(table_name)
        return {}

    # 惰性 import 入口, patch 模块属性即可命中
    monkeypatch.setattr(
        "app.services.governance_execute_service._post_execute", _fake_post_execute
    )
    # 处置服务内部会 commit；测试 fixture 靠回滚隔离, 这里把 commit 降级为 flush 防数据泄漏
    monkeypatch.setattr(db, "commit", db.flush)

    rno = _rno()
    db.add(Refund(refund_no=rno, refund_method="线上", status="已退款",
                  refund_amount=100, created_at_raw=_T))
    db.flush()

    quality_disposition_service.quality_ignore(db, "refund", rno, operator="t201")
    db.refresh(db.get(Refund, rno))
    assert db.get(Refund, rno).quality_status == "ignored"
    assert calls == ["refunds"]

    quality_disposition_service.quality_restore(db, "refund", rno, operator="t201")
    # ignored(非 dup 类)恢复 → 回到正常态 None；dup_residual 恢复才回 pending（设计如此）
    assert db.get(Refund, rno).quality_status is None
    assert calls == ["refunds", "refunds"]

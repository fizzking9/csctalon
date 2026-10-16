# -*- coding: utf-8 -*-
"""同步 upsert 不覆盖采纳治理四列的回归测试 (#332, T3: TR-3.1 / TR-3.2, FR-5)

五路同步路径各验证两件事:
  1. 已盖章行(adopt_state='excluded' 等)经同主键同步重放后, 四列原值不变;
  2. 新同步行四列落 NULL(由采纳作业后续填写)。
另对通用执行器做异常输入护栏测试(TR-3.2): rows 误带 adopt_* 键也不入库。
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from app.models.refund import Refund
from app.services.upsert._core import ImportResult, _execute_upsert
from app.services.upsert._masters import upsert_refund_record_reports
from app.services.upsert._refunds import (
    upsert_manual_refunds,
    upsert_refunds,
    upsert_shanmao_refunds,
    upsert_wallet,
)

_STAMPED_STATE = "excluded"
_STAMPED_REASON = "A_EX_METHOD"
_STAMPED_VER = "r20261016_v1"
_STAMPED_AT = "2026-10-16T09:00:00+08:00"


def _stamp(db, table: str, where: str, params: dict | None = None) -> None:
    """直接在 ods 表预置一行已盖章数据(模拟采纳作业结论)。"""
    db.execute(text(f"""
        UPDATE {table}
        SET adopt_state = :st, adopt_reason = :rs,
            adopt_rule_ver = :ver, adopted_at = :at
        WHERE {where}
    """), {"st": _STAMPED_STATE, "rs": _STAMPED_REASON,
           "ver": _STAMPED_VER, "at": _STAMPED_AT, **(params or {})})


def _assert_stamped_preserved(db, table: str, pk_col: str, pk_value) -> None:
    # timestamptz 直接按瞬时相等比较, 不依赖会话时区的文本渲染
    n = db.execute(text(f"""
        SELECT count(*) FROM {table}
        WHERE {pk_col} = :pk
          AND adopt_state = :st AND adopt_reason = :rs
          AND adopt_rule_ver = :ver AND adopted_at = :at
    """), {"pk": pk_value, "st": _STAMPED_STATE, "rs": _STAMPED_REASON,
           "ver": _STAMPED_VER, "at": _STAMPED_AT}).scalar_one()
    assert n == 1, f"{table} 已盖章行 {pk_value} 治理四列被同步破坏"


def _assert_null_adopt(db, table: str, pk_col: str, pk_value) -> None:
    n = db.execute(text(f"""
        SELECT count(*) FROM {table}
        WHERE {pk_col} = :pk
          AND adopt_state IS NULL AND adopt_reason IS NULL
          AND adopt_rule_ver IS NULL AND adopted_at IS NULL
    """), {"pk": pk_value}).scalar_one()
    assert n == 1, f"{table} 新增行 {pk_value} 治理四列应为 NULL"


def _call(upsert_fn, db, rows: list[dict]) -> ImportResult:
    """以恒等映射走完整同步函数(df 列名即目标字段名)。"""
    df = pd.DataFrame(rows)
    mapping = {c: c for c in df.columns}
    result = ImportResult()
    upsert_fn(df, mapping, db, result)
    db.flush()
    return result


# ── A 路 refunds: 通用执行器 on_conflict_do_update ───────────────────────────
def test_route_a_refunds_preserve_and_null(db):
    db.execute(text("""
        INSERT INTO ods.refunds (refund_no, status, refund_method, remark)
        VALUES ('RF-G1', '已退款', '线上', '旧备注')
    """))
    _stamp(db, "ods.refunds", "refund_no = 'RF-G1'")

    _call(upsert_refunds, db, [
        # 同主键重放, 改业务备注: 备注应更新, 治理四列不动
        {"refund_no": "RF-G1", "status": "已退款", "refund_method": "线上", "remark": "新备注"},
        # 新主键: 四列 NULL
        {"refund_no": "RF-G2", "status": "已退款", "refund_method": "线上", "remark": "新单"},
    ])
    assert db.execute(text(
        "SELECT remark FROM ods.refunds WHERE refund_no='RF-G1'"
    )).scalar_one() == "新备注"
    _assert_stamped_preserved(db, "ods.refunds", "refund_no", "RF-G1")
    _assert_null_adopt(db, "ods.refunds", "refund_no", "RF-G2")


# ── B 路 wallet: on_conflict_do_nothing —— 冲突整行不动, 天然保留 ────────────
def test_route_b_wallet_preserve_and_null(db):
    db.execute(text("""
        INSERT INTO ods.wallet_withdrawals
            (id, account_id, op_type, op_time, operator, op_amount, remark)
        VALUES (9501, 'ACC-G1', '用户提现', :ts, '张三', 1.00, '旧流水备注')
    """), {"ts": "2026-10-16T18:00:00+08:00"})  # 同步侧 naive 按北京解释, 须同一瞬时
    _stamp(db, "ods.wallet_withdrawals", "id = 9501")

    _call(upsert_wallet, db, [
        {"account_id": "ACC-G1", "op_type": "用户提现",
         "op_time": "2026-10-16 18:00:00", "operator": "张三", "op_amount": 1.00,
         "remark": "重放想改备注但 do_nothing 不会生效"},
        {"account_id": "ACC-G2", "op_type": "订单退费转入",
         "op_time": "2026-10-16 18:05:00", "operator": "张三", "op_amount": 2.00,
         "remark": "新流水"},
    ])
    # do_nothing: 冲突行业务列也不动
    assert db.execute(text(
        "SELECT remark FROM ods.wallet_withdrawals WHERE id=9501"
    )).scalar_one() == "旧流水备注"
    _assert_stamped_preserved(db, "ods.wallet_withdrawals", "id", 9501)
    new_id = db.execute(text(
        "SELECT id FROM ods.wallet_withdrawals WHERE account_id='ACC-G2'"
    )).scalar_one()
    _assert_null_adopt(db, "ods.wallet_withdrawals", "id", new_id)


# ── C 路 shanmao_refunds: 通用执行器 ────────────────────────────────────────
def test_route_c_shanmao_preserve_and_null(db):
    db.execute(text("""
        INSERT INTO ods.shanmao_refunds (refund_no, status, remark)
        VALUES ('TF-G1', '已退款', '旧备注')
    """))
    _stamp(db, "ods.shanmao_refunds", "refund_no = 'TF-G1'")

    _call(upsert_shanmao_refunds, db, [
        {"refund_no": "TF-G1", "status": "已退款", "remark": "新备注"},
        {"refund_no": "TF-G2", "status": "申请中", "remark": "新单"},
    ])
    assert db.execute(text(
        "SELECT remark FROM ods.shanmao_refunds WHERE refund_no='TF-G1'"
    )).scalar_one() == "新备注"
    _assert_stamped_preserved(db, "ods.shanmao_refunds", "refund_no", "TF-G1")
    _assert_null_adopt(db, "ods.shanmao_refunds", "refund_no", "TF-G2")


# ── D 路 manual_refunds: 独立 on_conflict_do_update(部分唯一索引) ───────────
def test_route_d_manual_preserve_and_null(db):
    db.execute(text("""
        INSERT INTO ods.manual_refunds
            (register_time, order_no, registrant, is_refunded,
             refund_amount, remark)
        VALUES (:ts, 'MO-G1', '李四', '是', 10.00, '旧登记备注')
    """), {"ts": "2026-10-16T15:00:00+08:00"})
    _stamp(db, "ods.manual_refunds", "order_no = 'MO-G1'")

    _call(upsert_manual_refunds, db, [
        {"register_time": "2026-10-16 15:00:00", "order_no": "MO-G1",
         "registrant": "李四", "is_refunded": "是", "refund_amount": 10.00,
         "remark": "新登记备注"},
        {"register_time": "2026-10-16 15:00:00", "order_no": "MO-G2",
         "registrant": "李四", "is_refunded": "否", "refund_amount": 0,
         "remark": "新登记"},
    ])
    assert db.execute(text(
        "SELECT remark FROM ods.manual_refunds WHERE order_no='MO-G1'"
    )).scalar_one() == "新登记备注"
    # 主键是自增 id, 用业务键取 id 再校验治理四列
    gid = db.execute(text(
        "SELECT id FROM ods.manual_refunds WHERE order_no='MO-G1'"
    )).scalar_one()
    _assert_stamped_preserved(db, "ods.manual_refunds", "id", gid)
    nid = db.execute(text(
        "SELECT id FROM ods.manual_refunds WHERE order_no='MO-G2'"
    )).scalar_one()
    _assert_null_adopt(db, "ods.manual_refunds", "id", nid)


# ── E 路 refund_record_reports: on_conflict_do_update(三列唯一约束) ─────────
def test_route_e_record_preserve_and_null(db):
    db.execute(text("""
        INSERT INTO ods.refund_record_reports
            (platform_order_no, refund_time, refund_amount, merchant_name)
        VALUES ('PO-G1', :ts, 5.00, '旧商户名')
    """), {"ts": "2026-10-16T19:00:00+08:00"})  # 同步侧 naive 按北京解释, 须同一瞬时
    _stamp(db, "ods.refund_record_reports", "platform_order_no = 'PO-G1'")

    _call(upsert_refund_record_reports, db, [
        {"platform_order_no": "PO-G1", "refund_time": "2026-10-16 19:00:00",
         "refund_amount": 5.00, "merchant_name": "新商户名"},
        {"platform_order_no": "PO-G2", "refund_time": "2026-10-16 19:05:00",
         "refund_amount": 6.00, "merchant_name": "另一商户"},
    ])
    assert db.execute(text(
        "SELECT merchant_name FROM ods.refund_record_reports WHERE platform_order_no='PO-G1'"
    )).scalar_one() == "新商户名"
    eid = db.execute(text(
        "SELECT id FROM ods.refund_record_reports WHERE platform_order_no='PO-G1'"
    )).scalar_one()
    _assert_stamped_preserved(db, "ods.refund_record_reports", "id", eid)
    nid = db.execute(text(
        "SELECT id FROM ods.refund_record_reports WHERE platform_order_no='PO-G2'"
    )).scalar_one()
    _assert_null_adopt(db, "ods.refund_record_reports", "id", nid)


# ── TR-3.2 通用执行器护栏: rows 误带 adopt_* 键, 新增不落值/更新不覆盖 ───────
def test_execute_upsert_strips_adopt_columns(db):
    # 预置已盖章行
    db.execute(text("""
        INSERT INTO ods.refunds (refund_no, status, refund_method)
        VALUES ('RF-H2', '已退款', '线上')
    """))
    _stamp(db, "ods.refunds", "refund_no = 'RF-H2'")

    poisoned_rows = [
        # 新增行: 调用方误把治理列塞进 rows
        {"refund_no": "RF-H1", "status": "已退款", "refund_method": "线上",
         "adopt_state": "adopted", "adopt_reason": "FAKE",
         "adopt_rule_ver": "fake-ver", "adopted_at": "2020-01-01T00:00:00+08:00"},
        # 冲突行: 试图借同步覆盖既有结论
        {"refund_no": "RF-H2", "status": "已退款", "refund_method": "线上",
         "adopt_state": "adopted", "adopt_reason": "FAKE",
         "adopt_rule_ver": "fake-ver", "adopted_at": "2020-01-01T00:00:00+08:00"},
    ]
    _execute_upsert(db, Refund, "refund_no", poisoned_rows, ImportResult())
    db.flush()

    _assert_null_adopt(db, "ods.refunds", "refund_no", "RF-H1")
    _assert_stamped_preserved(db, "ods.refunds", "refund_no", "RF-H2")
    # rows 被护栏原地剔除(后续逻辑也不会再见到这些键)
    assert all("adopt_state" not in r for r in poisoned_rows)

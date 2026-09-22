# -*- coding: utf-8 -*-
"""钱包流水去重回归测试（工单 #161，C-P1-7 治理）

背景（#160 事故）：
`wallet_withdrawals` 的唯一约束含 `device_sn`，而 **PostgreSQL 唯一约束对 NULL 不去重**
（NULL ≠ NULL）→ device_sn 为空的提现行完全不受保护，高频同步（每 30 分钟一轮）
对同一笔反复插入，生产累积 **40603 行重复**（其中 38458 行为「提现申请-冻结」失败提现）。

本测试锁死修复后的行为：
  ① device_sn 为空的同一笔提现，**连续两轮同步只入库 1 行**（部分唯一索引生效）；
  ② device_sn 非空的同一笔提现，仍走原 6 列约束去重（不回归）；
  ③ 同一批次内的重复行依旧被 Python 层去重（原有行为不变）。

为什么要跨「两轮」而不是同批次：
  原实现在**批次内**已有 Python 去重（dict key 含 None 也能相等），所以同批次测不出问题；
  真正的缺陷在**跨批次**的 DB 层 ON CONFLICT —— 本测试据此设计。
"""

from __future__ import annotations

import uuid

import pandas as pd
import pytest
from sqlalchemy import select

from app.models.wallet import WalletWithdrawal
from app.services.upsert import ImportResult, upsert_wallet

_MAPPING = {
    "account_id": "account_id",
    "device_sn": "device_sn",
    "op_type": "op_type",
    "op_amount": "op_amount",
    "remark": "remark",
    "op_time": "op_time",
    "operator": "operator",
}


def _unique_account() -> str:
    return f"test_acct_{uuid.uuid4().hex[:10]}"


def _df(account_id: str, device_sn: str | None, *, remark: str = "提现申请-冻结") -> pd.DataFrame:
    return pd.DataFrame([{
        "account_id": account_id,
        "device_sn": device_sn,
        "op_type": "用户提现",
        "op_amount": 15.0,
        "remark": remark,
        "op_time": "2026-09-19 10:00:00",
        "operator": "用户提现",
    }])


def _count(db, account_id: str) -> int:
    return len(db.execute(
        select(WalletWithdrawal).where(WalletWithdrawal.account_id == account_id)
    ).scalars().all())


def _cleanup(db, *account_ids: str) -> None:
    for acc in account_ids:
        for row in db.execute(
            select(WalletWithdrawal).where(WalletWithdrawal.account_id == acc)
        ).scalars().all():
            db.delete(row)
    db.commit()


# 本文件测试用的设备 SN。
#   ⚠️ `upsert_wallet` 内部会调 `_sync_devices` —— SN 不存在时**自动创建** `devices` 行,
#      且本文件用例里有 `db.commit()`, 会把该行连同 fixture 事务一起提交落库。
#      原实现只 `_cleanup` 提现行、不清理 device → 每跑一次残留一行 `SNTEST0001`。
#      （同类缺陷: test_reconcile.py 的 SN-DUP/SN-NONE, 已一并修复）
_TEST_SNS = ("SNTEST0001",)


def _cleanup_devices(db) -> None:
    """清理 `_sync_devices` 自动创建、并已被 commit 落库的设备行。"""
    from app.models.device import Device

    for sn in _TEST_SNS:
        for row in db.execute(
            select(Device).where(Device.sn == sn)
        ).scalars().all():
            db.delete(row)
    db.commit()


@pytest.fixture(autouse=True)
def _no_device_residue(db):
    """每个用例后清掉本文件自动创建设备, 防止测试库积累残留（见 `_TEST_SNS` 注释）。"""
    yield
    try:
        _cleanup_devices(db)
    except Exception:
        db.rollback()


def test_null_device_sn_not_duplicated_across_rounds(db):
    """★ 核心回归：device_sn 为空的同一笔提现，两轮同步只入库 1 行（#160 事故根因）。"""
    acc = _unique_account()
    try:
        # 第 1 轮同步
        r1 = ImportResult()
        upsert_wallet(_df(acc, None), _MAPPING, db, r1)
        db.commit()
        assert _count(db, acc) == 1, "首轮应入库 1 行"

        # 第 2 轮同步（同一样例，模拟 BMS 页面时间窗口未设置成功导致重复拉取）
        r2 = ImportResult()
        upsert_wallet(_df(acc, None), _MAPPING, db, r2)
        db.commit()
        assert _count(db, acc) == 1, "第二轮不得重复插入（部分唯一索引应拦住）"
    finally:
        _cleanup(db, acc)


def test_null_device_sn_dedup_covers_null_other_columns(db):
    """NULLS NOT DISTINCT：其余列也为 NULL 时同样按「NULL 相等」去重。"""
    acc = _unique_account()
    try:
        df = _df(acc, None)
        df["operator"] = None  # 让 operator 也为 NULL
        r1 = ImportResult()
        upsert_wallet(df, _MAPPING, db, r1)
        db.commit()
        assert _count(db, acc) == 1

        r2 = ImportResult()
        upsert_wallet(df, _MAPPING, db, r2)
        db.commit()
        assert _count(db, acc) == 1, "operator 为 NULL 时也应去重"
    finally:
        _cleanup(db, acc)


def test_non_null_device_sn_still_deduped(db):
    """回归：device_sn 非空仍走原 6 列约束去重（改动不得破坏既有路径）。"""
    acc = _unique_account()
    try:
        r1 = ImportResult()
        upsert_wallet(_df(acc, "SNTEST0001"), _MAPPING, db, r1)
        db.commit()
        assert _count(db, acc) == 1

        r2 = ImportResult()
        upsert_wallet(_df(acc, "SNTEST0001"), _MAPPING, db, r2)
        db.commit()
        assert _count(db, acc) == 1, "device_sn 非空仍应去重"
    finally:
        _cleanup(db, acc)


def test_same_batch_duplicates_deduped(db):
    """回归：同一批次内的重复行仍被 Python 层去重（原有行为不变）。"""
    acc = _unique_account()
    try:
        df = pd.concat([_df(acc, None), _df(acc, None)], ignore_index=True)
        r = ImportResult()
        upsert_wallet(df, _MAPPING, db, r)
        db.commit()
        assert _count(db, acc) == 1, "同批次重复应去重为 1 行"
    finally:
        _cleanup(db, acc)


def test_distinct_withdrawals_both_kept(db):
    """反向：金额不同（真实不同笔）的两行都应保留，避免误杀。"""
    acc = _unique_account()
    try:
        df = _df(acc, None)
        df2 = _df(acc, None)
        df2["op_amount"] = 99.0  # 不同金额 = 不同笔
        r = ImportResult()
        upsert_wallet(pd.concat([df, df2], ignore_index=True), _MAPPING, db, r)
        db.commit()
        assert _count(db, acc) == 2, "不同金额的提现是两笔，均应保留"
    finally:
        _cleanup(db, acc)

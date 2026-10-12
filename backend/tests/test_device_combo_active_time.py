# -*- coding: utf-8 -*-
"""#305: device_combo 导入建档时回填设备 active_time(首次套餐开始)

根因: _sync_devices 此前只同步 merchant_name, device_combo 导入为新 SN 建档时
不带 active_time → 生产实测 68.6 万设备激活时间为 NULL(52%)。

修复: upsert_device_combos 注入批内该 SN 最早 start_date 作 active_time,
经 _sync_devices(fill_fields=["active_time"]) 建档/补 NULL。

本测试锁定:
1. 新 SN 建档 → active_time = 批内该 SN 最早 start_date(min, 非首条出现顺序)
2. 已有 active_time 的设备不被覆盖(BMS 设备档案导入的权威值优先)
3. start_date 缺失的 SN 建档后 active_time 保持 NULL(不造伪日期)

注意: upsert_device_combos 只 flush 不 commit, 可用事务回滚型 db fixture, 无需清理。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import select

from app.models.device import Device
from app.services.data_parser import suggest_mapping
from app.services.upsert._core import ImportResult
from app.services.upsert._masters import upsert_device_combos

_TZ = timezone.utc

_COLS = ["设备SN", "充值订单号", "套餐名称", "开始日期", "结束日期"]


def _import(db, rows: list[dict]):
    mapping = suggest_mapping("device_combo", _COLS)
    df = pd.DataFrame(rows, columns=_COLS)
    result = ImportResult()
    upsert_device_combos(df, mapping, db, result)
    return result


def test_new_sn_gets_min_start_date_as_active_time(db):
    """新 SN 建档: active_time 取批内最早 start_date(后出现的更早日期也要命中)"""
    _import(db, [
        # 同一 SN 两条套餐: 先出现 5 月, 再出现 1 月 → min 应取 1 月
        {"设备SN": "SN305TEST1", "充值订单号": "T305-A",
         "套餐名称": "月套餐", "开始日期": "2026-05-10 00:00:00", "结束日期": "2026-06-10 00:00:00"},
        {"设备SN": "SN305TEST1", "充值订单号": "T305-B",
         "套餐名称": "季套餐", "开始日期": "2026-01-01 12:00:00", "结束日期": "2026-04-01 12:00:00"},
    ])
    dev = db.scalars(select(Device).where(Device.sn == "SN305TEST1")).one()
    # naive 视为北京时间 → UTC: 2026-01-01 12:00 +08:00 = 2026-01-01 04:00 UTC
    assert dev.active_time == datetime(2026, 1, 1, 4, 0, tzinfo=_TZ), dev.active_time


def test_existing_active_time_not_overwritten(db):
    """已有激活时间的设备不被套餐数据覆盖(BMS 设备档案的权威激活时间优先)"""
    official = datetime(2025, 6, 1, 0, 0, tzinfo=_TZ)
    db.add(Device(sn="SN305TEST2", active_time=official))
    db.flush()
    _import(db, [
        {"设备SN": "SN305TEST2", "充值订单号": "T305-C",
         "套餐名称": "月套餐", "开始日期": "2026-03-01 00:00:00", "结束日期": "2026-04-01 00:00:00"},
    ])
    dev = db.scalars(select(Device).where(Device.sn == "SN305TEST2")).one()
    assert dev.active_time == official


def test_missing_start_date_keeps_active_time_null(db):
    """start_date 缺失: 建档但 active_time 保持 NULL(不造伪日期)"""
    _import(db, [
        {"设备SN": "SN305TEST3", "充值订单号": "T305-D",
         "套餐名称": "月套餐", "开始日期": None, "结束日期": None},
    ])
    dev = db.scalars(select(Device).where(Device.sn == "SN305TEST3")).one()
    assert dev.active_time is None

# -*- coding: utf-8 -*-
"""#305: _sync_devices 垃圾 SN 建档防御

实证: 人工登记数据(退款/工单)存在「一格多 SN(换行/斜杠拼接)」「Excel 文本前缀引号」
「字面 x」等脏 SN, _sync_devices 照单建档 → 1.7 万+ 四无空壳档。

锁定:
1. 多 SN 拼接/斜杠/字面占位 → 不建设备档(业务行本身导入不受影响, 本测试只管建档)
2. Excel 引号前缀('69...) → 去引号后正常建档(内里是合法 SN, 不能丢)
3. 正常 SN + fill 字段 → 建档与填充行为不变
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.device import Device
from app.services.upsert._core import _clean_device_sn, _sync_devices


def test_clean_sn_rules():
    """纯函数规则: 脏值判 None, 引号包裹的合法 SN 去壳, 正常值原样"""
    assert _clean_device_sn("864665067682372\n864665067104237") is None  # 多SN换行
    assert _clean_device_sn("867921073637240/867921073626409") is None  # 斜杠拼接
    assert _clean_device_sn("86A，86B") is None  # 全角逗号
    assert _clean_device_sn("x") is None  # 字面占位
    assert _clean_device_sn("NULL") is None
    assert _clean_device_sn("'69142529011493") == "69142529011493"  # Excel 引号壳
    assert _clean_device_sn(" 69142620034770\t") == "69142620034770"  # 常规空白
    assert _clean_device_sn("69142620034770") == "69142620034770"
    assert _clean_device_sn(None) is None
    assert _clean_device_sn("") is None


def test_sync_devices_skips_junk_and_keeps_quoted(db):
    """建档行为: 垃圾 SN 不建壳; 引号壳 SN 落地为干净 SN; 正常 SN 建档+填充不变"""
    _sync_devices(
        [
            {"device_sn": "864665067682372\n864665067104237", "merchant_name": "甲"},
            {"device_sn": "x", "merchant_name": "乙"},
            {"device_sn": "'69142529011493", "merchant_name": "丙"},
            {"device_sn": "SN305GUARD1", "merchant_name": "丁"},
        ],
        "device_sn",
        db,
        fill_fields=["merchant_name"],
    )
    db.flush()
    sns = set(db.scalars(select(Device.sn).where(
        Device.sn.in_(["864665067682372", "x", "69142529011493", "SN305GUARD1"])
    )).all())
    assert sns == {"69142529011493", "SN305GUARD1"}, sns  # 垃圾不建档
    d = db.scalars(select(Device).where(Device.sn == "SN305GUARD1")).one()
    assert d.merchant_name == "丁"  # fill 行为不变

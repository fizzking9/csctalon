# -*- coding: utf-8 -*-
"""device_flow_usage(设备月流量) upsert 幂等与解析测试

覆盖:
1. 正向: 新 SN×月 插入 1 行
2. 幂等: 同 SN×月 重取 → 覆盖更新不新增
3. 不完整月可更新: month_complete 语义(当前月 false / 历史月 true)
4. 缺关键字段(无 sn/无月份) → 记 error 跳过
5. 解析映射: suggest_mapping 对 API 列名正确命中
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests._db_guard import safe_wipe


@pytest.fixture()
def _test_session(_engine):
    """独立 session(非 db fixture 的显式事务), 让 run_upsert 的 commit 生效。

    注意: run_upsert 会真实 commit 且经 _sync_devices 把测试 SN 建进 devices 共享表,
    teardown 必须清理(device_flow_usage + 本文件建的 devices), 否则污染其它测试
    (曾导致 test_external_brief 等偶发失败)。
    """
    engine = _engine
    session = Session(engine, autocommit=False, autoflush=False)
    from app.models.device import Device
    from app.models.device_flow_usage import DeviceFlowUsage

    # 清空目标表 + 本文件用到的测试设备(避免与既有数据互相影响)
    safe_wipe(session, DeviceFlowUsage)
    safe_wipe(session, Device, where=Device.sn.in_(["68772550194237", "69212627276896"]))
    session.commit()
    try:
        yield session
    finally:
        # teardown 清理本文件可能写入的数据
        try:
            safe_wipe(session, DeviceFlowUsage)
            safe_wipe(session, Device, where=Device.sn.in_(["68772550194237", "69212627276896"]))
            session.commit()
        except Exception:
            session.rollback()
        session.rollback()
        session.close()


def _make_df(**overrides):
    import pandas as pd

    row = {
        "sn": "68772550194237",
        "businessId": "258704593",
        "businessName": "2637淘系猛豹行",
        "deviceTypeName": "YKM-M5-GT",
        "deviceGroupName": None,
        "deviceComboName": "单网100G【30天】",
        "flowDate": "2026-08",
        "flowValue": "7.57",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_upsert_insert_new(_test_session):
    from app.models.device_flow_usage import DeviceFlowUsage
    from app.services.data_parser import suggest_mapping
    from app.services.upsert import run_upsert

    df = _make_df()
    mapping = suggest_mapping("device_flow_usage", list(df.columns))
    result = run_upsert(df, "device_flow_usage", mapping, _test_session)

    assert result.inserted == 1
    assert result.failed == 0
    row = _test_session.execute(
        select(DeviceFlowUsage).where(DeviceFlowUsage.device_sn == "68772550194237")
    ).scalar_one()
    assert row.stat_month == "2026-08"
    assert float(row.flow_gb) == pytest.approx(7.57)
    assert row.merchant_name == "2637淘系猛豹行"
    assert row.month_complete is True  # 2026-08 < 当前 2026-09


def test_upsert_idempotent_update(_test_session):
    """同 SN×月 重取 → 覆盖更新不新增(D1/用户需求: 不完整月可反复重取)"""
    from app.models.device_flow_usage import DeviceFlowUsage
    from app.services.data_parser import suggest_mapping
    from app.services.upsert import run_upsert

    df1 = _make_df(flowValue="7.57")
    mapping = suggest_mapping("device_flow_usage", list(df1.columns))
    r1 = run_upsert(df1, "device_flow_usage", mapping, _test_session)
    assert r1.inserted == 1

    # 模拟下月重取: 同 SN×月 流量更新(8 月走完后变准确值)
    df2 = _make_df(flowValue="120.50")
    r2 = run_upsert(df2, "device_flow_usage", mapping, _test_session)
    assert r2.inserted == 0
    assert r2.updated == 1

    cnt = _test_session.execute(select(func.count()).select_from(DeviceFlowUsage)).scalar()
    assert cnt == 1  # 仍只有一行
    row = _test_session.execute(
        select(DeviceFlowUsage).where(DeviceFlowUsage.device_sn == "68772550194237")
    ).scalar_one()
    assert float(row.flow_gb) == pytest.approx(120.50)


def test_upsert_missing_key_error(_test_session):
    """缺 SN 或缺月份 → 无法去重定位, 记 error 跳过"""
    from app.models.device_flow_usage import DeviceFlowUsage
    from app.services.data_parser import suggest_mapping
    from app.services.upsert import run_upsert

    df = _make_df(sn=None, flowDate=None)
    mapping = suggest_mapping("device_flow_usage", list(df.columns))
    result = run_upsert(df, "device_flow_usage", mapping, _test_session)

    assert result.inserted == 0
    assert result.failed >= 1
    cnt = _test_session.execute(select(func.count()).select_from(DeviceFlowUsage)).scalar()
    assert cnt == 0


def test_suggest_mapping_device_flow(_test_session):
    """API 返回列名 → 正确命中模板字段"""
    from app.services.data_parser import suggest_mapping

    cols = ["sn", "businessId", "businessName", "deviceTypeName",
            "deviceGroupName", "deviceComboName", "flowDate", "flowValue"]
    mapping = suggest_mapping("device_flow_usage", cols)
    assert mapping["device_sn"] == "sn"
    assert mapping["merchant_id"] == "businessId"
    assert mapping["stat_month"] == "flowDate"
    assert mapping["flow_gb"] == "flowValue"

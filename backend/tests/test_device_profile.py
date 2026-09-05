# -*- coding: utf-8 -*-
"""device_profile(设备管理档案) upsert 回填 devices 测试(P4 / #18)

覆盖:
1. 新设备回填 → 插入 devices 并带档案字段
2. 已存在设备 → 补全档案字段不新增(幂等)
3. 缺设备号 → error 跳过
4. suggest_mapping 命中真实导出列
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session


@pytest.fixture()
def _test_session(_engine):
    """独立 session(让 run_upsert 的 commit 生效); 清 devices(避免污染其它用例)"""
    session = Session(_engine, autocommit=False, autoflush=False)
    # 注意: devices 是全局共享表, 清空会影响其它用例依赖的设备; 因此不清空,
    # 只用唯一 SN 避免冲突, 结束 rollback 即可
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _device_df(sn="SN-P4-0001", device_type="GT-3K皓月", merchant="26820测试商户",
               main_merchant="云客猫boss", active_time="2026-09-05 15:25:22",
               iccid="89860625780077413121", batch="18108"):
    import pandas as pd

    return pd.DataFrame([{
        "设备号": sn,
        "设备类型": device_type,
        "所属商户": merchant,
        "主商户": main_merchant,
        "是否激活": "已激活",
        "激活时间": active_time,
        "服务有效期": "2027-01-12 15:21:38",
        "当前套餐": "Ai极速任用*{3个月}",
        "启动卡ICCID": iccid,
        "虚拟卡ICCID": None,
        "应用版本": "M5-W907",
        "区域": "华南",
        "创建时间": "2026-08-26 13:58:00",
        "批次号": batch,
    }])


@pytest.fixture()
def _uniq_sn_prefix():
    """每测试唯一 SN 前缀(避免 run_upsert commit 后跨运行残留导致 inserted=0)"""
    import uuid

    return f"SN-P4-{uuid.uuid4().hex[:8]}"


def test_device_profile_insert(_test_session, _uniq_sn_prefix):
    """新设备 → 插入 devices + 档案字段正确"""
    from app.models.device import Device
    from app.services.data_parser import suggest_mapping
    from app.services.upsert import run_upsert

    sn = f"{_uniq_sn_prefix}-INS"
    df = _device_df(sn=sn)
    mapping = suggest_mapping("device_profile", list(df.columns))
    assert mapping["device_sn"] == "设备号"
    assert mapping["main_merchant_name"] == "主商户"

    r = run_upsert(df, "device_profile", mapping, _test_session)
    assert r.inserted == 1
    assert r.failed == 0

    dev = _test_session.execute(select(Device).where(Device.sn == sn)).scalar_one()
    assert dev.device_type == "GT-3K皓月"
    assert dev.merchant_name == "26820测试商户"
    assert dev.main_merchant_name == "云客猫boss"
    assert dev.active_flag == "已激活"
    assert dev.start_iccid == "89860625780077413121"
    assert dev.batch_no == "18108"
    assert dev.active_time is not None


def test_device_profile_update_idempotent(_test_session, _uniq_sn_prefix):
    """已存在设备 → 补全不新增(幂等)"""
    from app.models.device import Device
    from app.services.data_parser import suggest_mapping
    from app.services.upsert import run_upsert

    sn = f"{_uniq_sn_prefix}-UPD"
    df = _device_df(sn=sn)
    mapping = suggest_mapping("device_profile", list(df.columns))
    r1 = run_upsert(df, "device_profile", mapping, _test_session)
    assert r1.inserted == 1

    # 再次导入同 SN(如档案更新)
    df2 = _device_df(sn=sn, device_type="YKM-M5-GT")  # 类型变了
    r2 = run_upsert(df2, "device_profile", mapping, _test_session)
    assert r2.inserted == 0  # 不新增
    cnt = _test_session.execute(select(func.count()).select_from(Device).where(Device.sn == sn)).scalar()
    assert cnt == 1
    dev = _test_session.execute(select(Device).where(Device.sn == sn)).scalar_one()
    assert dev.device_type == "YKM-M5-GT"  # 已更新


def test_device_profile_missing_sn(_test_session):
    """缺设备号 → error 跳过"""
    import pandas as pd

    from app.services.data_parser import suggest_mapping
    from app.services.upsert import run_upsert
    df = pd.DataFrame([{"设备号": None, "设备类型": "GT-3K皓月"}])
    mapping = suggest_mapping("device_profile", list(df.columns))
    r = run_upsert(df, "device_profile", mapping, _test_session)
    assert r.inserted == 0
    assert r.failed >= 1

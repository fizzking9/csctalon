# -*- coding: utf-8 -*-
"""BMS 三源(退款记录报表/订单管理/退货跟踪) upsert + 解析映射测试

覆盖:
1. refund_record_report: 映射真实 CSV 列 → 入库; 同键幂等更新
2. bms_order: 映射真实导出列 → 入库(order_no 主键幂等)
3. return_tracking: 映射真实导出列 → 入库
4. 缺关键字段 → error 跳过
5. suggest_mapping 对新模板正确命中
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session


@pytest.fixture()
def _test_session(_engine):
    """独立 session(非 db fixture 显式事务), 让 run_upsert 的 commit 生效。

    run_upsert 会真实 commit 且经 _sync_devices 建 devices(测试 SN), teardown 需清理,
    否则残留污染其它依赖共享表的测试(曾致 test_external_brief 偶发失败)。
    """
    from app.models import Device

    session = Session(_engine, autocommit=False, autoflush=False)
    _TEST_SNS = ["68772550194237", "69212633321709", "68772612318169", "SN-TEST-1"]
    for model in (
        "RefundRecordReport", "BmsOrder", "ReturnTracking",
    ):
        from app.models import __dict__ as m

        cls = m[model]
        session.execute(cls.__table__.delete())
    session.execute(Device.__table__.delete().where(Device.sn.in_(_TEST_SNS)))
    session.commit()
    try:
        yield session
    finally:
        # teardown: 清理本文件写入的共享表数据
        try:
            for model in ("RefundRecordReport", "BmsOrder", "ReturnTracking"):
                from app.models import __dict__ as m

                session.execute(m[model].__table__.delete())
            session.execute(Device.__table__.delete().where(Device.sn.in_(_TEST_SNS)))
            session.commit()
        except Exception:
            session.rollback()
        session.rollback()
        session.close()


# ---------- refund_record_report ----------

def test_refund_record_report_insert_and_mapping(_test_session):
    """用真实 CSV 列名 → 映射正确 → 入库; 同键重导 = 更新不新增"""
    import pandas as pd

    from app.models import RefundRecordReport
    from app.services.data_parser import suggest_mapping
    from app.services.upsert import run_upsert

    cols = ["平台订单号", "微信订单号", "商户号", "商户名", "设备号",
            "商品名称", "退款时间", "申请退款金额", "退款金额", "退款状态"]
    mapping = suggest_mapping("refund_record_report", cols)
    assert mapping["platform_order_no"] == "平台订单号"
    assert mapping["merchant_id"] == "商户号"
    assert mapping["device_sn"] == "设备号"
    assert mapping["refund_time"] == "退款时间"

    df = pd.DataFrame([{
        "平台订单号": "WX2026090510000000001",
        "微信订单号": "4500000377202609059999999999",
        "商户号": "1611876437",
        "商户名": "深圳市云客猫科技有限公司",
        "设备号": "68772550194237",
        "商品名称": "物联网服务",
        "退款时间": "2026-09-05 10:00:00",
        "申请退款金额": "19.00",
        "退款金额": "19.00",
        "退款状态": "退款成功",
    }])
    r1 = run_upsert(df, "refund_record_report", mapping, _test_session)
    assert r1.inserted == 1

    # 同键(单号+时间+金额)重导 → 更新不新增
    r2 = run_upsert(df, "refund_record_report", mapping, _test_session)
    assert r2.inserted == 0
    assert r2.updated == 1
    cnt = _test_session.execute(select(func.count()).select_from(RefundRecordReport)).scalar()
    assert cnt == 1


def test_refund_record_report_missing_key(_test_session):
    """缺平台订单号/退款时间/金额 → error 跳过"""
    import pandas as pd

    from app.services.data_parser import suggest_mapping
    from app.services.upsert import run_upsert

    cols = ["平台订单号", "退款时间", "退款金额"]
    mapping = suggest_mapping("refund_record_report", cols)
    df = pd.DataFrame([{"平台订单号": None, "退款时间": "2026-09-05 10:00:00", "退款金额": "19"}])
    r = run_upsert(df, "refund_record_report", mapping, _test_session)
    assert r.inserted == 0
    assert r.failed >= 1


# ---------- bms_order ----------

def test_bms_order_insert_and_idempotent(_test_session):
    """真实导出列 → order_no 主键入库; 同 order_no 重导 = 更新"""
    import pandas as pd

    from app.models import BmsOrder
    from app.services.data_parser import suggest_mapping
    from app.services.upsert import run_upsert

    cols = ["订单号", "设备号", "所属商户", "设备类型", "套餐名称", "套餐价格",
            "应付价格", "实际支付价格", "支付时间", "订单状态", "订单类型",
            "退款状态", "创建人", "创建时间", "支付类型", "激活时间", "微信商户Id"]
    mapping = suggest_mapping("bms_order", cols)
    assert mapping["order_no"] == "订单号"
    assert mapping["business_name"] == "所属商户"
    assert mapping["mch_id"] == "微信商户Id"

    df = pd.DataFrame([{
        "订单号": "WX2026090510000000001", "设备号": "69212633321709",
        "所属商户": "26818淘系测试", "设备类型": "GT-3K皓月", "套餐名称": "单网100G【30天】",
        "套餐价格": "29.00", "应付价格": "29.00", "实际支付价格": "29.00",
        "支付时间": "2026-09-05 10:00:00", "订单状态": "支付成功", "订单类型": "续费订单",
        "退款状态": "", "创建人": "WeChat", "创建时间": "2026-09-05 09:59:00",
        "支付类型": "微信支付", "激活时间": "2026-09-01 08:00:00", "微信商户Id": "1611876437",
    }])
    r1 = run_upsert(df, "bms_order", mapping, _test_session)
    assert r1.inserted == 1
    # 同订单号更新金额
    df2 = df.copy()
    df2.loc[0, "实际支付价格"] = "30.00"
    r2 = run_upsert(df2, "bms_order", mapping, _test_session)
    assert r2.inserted == 0
    assert r2.updated == 1
    cnt = _test_session.execute(select(func.count()).select_from(BmsOrder)).scalar()
    assert cnt == 1


def test_bms_order_missing_order_no(_test_session):
    import pandas as pd

    from app.services.data_parser import suggest_mapping
    from app.services.upsert import run_upsert

    cols = ["订单号", "套餐名称"]
    mapping = suggest_mapping("bms_order", cols)
    df = pd.DataFrame([{"订单号": None, "套餐名称": "x"}])
    r = run_upsert(df, "bms_order", mapping, _test_session)
    assert r.inserted == 0
    assert r.failed >= 1


# ---------- return_tracking ----------

def test_return_tracking_insert(_test_session):
    """真实导出列 → 入库"""
    import pandas as pd

    from app.models import ReturnTracking
    from app.services.data_parser import suggest_mapping
    from app.services.upsert import run_upsert

    cols = ["销售订单号", "SN", "设备类型", "店铺", "实付金额", "退货类型", "退款金额",
            "退货签收", "退货快递单号", "退货原因", "退货备注", "下单时间", "退货时间",
            "创建人", "更新人", "创建时间", "更新时间"]
    mapping = suggest_mapping("return_tracking", cols)
    assert mapping["device_sn"] == "SN"
    assert mapping["order_no"] == "销售订单号"
    assert mapping["return_sign_status"] == "退货签收"

    df = pd.DataFrame([{
        "销售订单号": "4502270052157031919", "SN": "68772612318169",
        "设备类型": "YKM-M5-GT", "店铺": "26724钻石VIP", "实付金额": "59.90",
        "退货类型": "已激活退款", "退款金额": "0.00", "退货签收": "退货中",
        "退货快递单号": "", "退货原因": "换货", "退货备注": "以旧换新",
        "下单时间": "2026-04-14 06:36:56", "退货时间": "2026-09-05 16:22:31",
        "创建人": "企微", "更新人": "企微",
        "创建时间": "2026-09-05 16:22:31", "更新时间": "2026-09-05 16:22:31",
    }])
    r = run_upsert(df, "return_tracking", mapping, _test_session)
    assert r.inserted >= 1
    assert r.failed == 0
    row = _test_session.execute(select(ReturnTracking)).scalars().first()
    assert row.device_sn == "68772612318169"
    assert row.return_type == "已激活退款"

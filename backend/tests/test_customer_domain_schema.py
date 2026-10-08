# -*- coding: utf-8 -*-
"""B3-1 (#246) 客户域建表测试:
1. 新表/新列存在于 Base.metadata;
2. SCD 部分唯一索引: 同一设备仅一条当前归属;
3. merchants.merchant_id 部分唯一: 空值允许多行, 非空唯一。
"""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.database import Base
from app.models.device import Device, DimDeviceType
from app.models.merchant import DeviceMerchantLink, Merchant
from app.models.shop import DeviceShopLink, Enterprise, Shop, ShopEnterpriseHistory


class TestCustomerDomainSchema:
    """schema 存在性"""

    def test_new_tables_exist_in_metadata(self):
        tables = Base.metadata.tables
        for name in (
            "dim_device_type",
            "device_merchant_links",
            "shops",
            "enterprises",
            "shop_enterprise_history",
            "device_shop_links",
        ):
            assert name in tables, f"缺表 {name}"

    def test_devices_has_device_type_code(self):
        assert "device_type_code" in Base.metadata.tables["devices"].c

    def test_merchant_id_partial_unique_index(self):
        indexes = Base.metadata.tables["merchants"].indexes
        target = [ix for ix in indexes if ix.name == "ix_merchants_merchant_id_unique"]
        assert target and target[0].unique


class TestDeviceMerchantLinkSCD:
    """设备→商户号 SCD 当前行唯一"""

    def test_one_current_link_per_device(self, db):
        device = Device(sn="SN_B31_001")
        merchant = Merchant(sub_name="测试子商户B31", merchant_id="MCH_B31_001")
        db.add_all([device, merchant])
        db.flush()

        db.add(DeviceMerchantLink(
            device_sn="SN_B31_001", merchant_pk=merchant.id,
            effective_from=date(2026, 1, 1), is_current=True,
            match_source="manual",
        ))
        db.flush()

        # 同设备第二条当前归属 → 触发部分唯一索引
        db.add(DeviceMerchantLink(
            device_sn="SN_B31_001", merchant_pk=merchant.id,
            effective_from=date(2026, 2, 1), is_current=True,
            match_source="auto",
        ))
        with pytest.raises(IntegrityError):
            db.flush()

    def test_history_rows_allowed(self, db):
        device = Device(sn="SN_B31_002")
        merchant = Merchant(sub_name="测试子商户B31-2", merchant_id="MCH_B31_002")
        db.add_all([device, merchant])
        db.flush()

        # 一条历史(is_current=False) + 一条当前 → 合法
        db.add(DeviceMerchantLink(
            device_sn="SN_B31_002", merchant_pk=merchant.id,
            effective_from=date(2025, 1, 1), effective_to=date(2026, 1, 1),
            is_current=False, match_source="auto",
        ))
        db.add(DeviceMerchantLink(
            device_sn="SN_B31_002", merchant_pk=merchant.id,
            effective_from=date(2026, 1, 1), is_current=True,
            match_source="manual",
        ))
        db.flush()  # 不抛异常即通过


class TestMerchantIdUnique:
    """merchant_id 业务键部分唯一(空值允许)"""

    def test_null_merchant_id_allowed_multiple(self, db):
        db.add_all([
            Merchant(sub_name="B31空商户A", merchant_id=None),
            Merchant(sub_name="B31空商户B", merchant_id=None),
        ])
        db.flush()

    def test_duplicate_merchant_id_rejected(self, db):
        db.add(Merchant(sub_name="B31重复商户A", merchant_id="MCH_DUP_001"))
        db.flush()
        db.add(Merchant(sub_name="B31重复商户B", merchant_id="MCH_DUP_001"))
        with pytest.raises(IntegrityError):
            db.flush()


class TestShopEnterpriseSCD:
    """店铺↔企业父子 SCD + 设备→店铺 SCD"""

    def test_shop_one_current_enterprise(self, db):
        ent = Enterprise(name="测试企业B31")
        db.add(ent)
        db.flush()
        shop = Shop(shop_name="测试店铺B31", enterprise_pk=ent.id)
        db.add(shop)
        db.flush()

        db.add(ShopEnterpriseHistory(
            shop_pk=shop.id, enterprise_pk=ent.id,
            effective_from=date(2026, 1, 1), is_current=True,
        ))
        db.flush()
        db.add(ShopEnterpriseHistory(
            shop_pk=shop.id, enterprise_pk=ent.id,
            effective_from=date(2026, 2, 1), is_current=True,
        ))
        with pytest.raises(IntegrityError):
            db.flush()

    def test_device_one_current_shop(self, db):
        device = Device(sn="SN_B31_003")
        shop = Shop(shop_name="测试店铺B31-3")
        db.add_all([device, shop])
        db.flush()

        db.add(DeviceShopLink(
            device_sn="SN_B31_003", shop_pk=shop.id,
            effective_from=date(2026, 1, 1), is_current=True,
            match_source="manual",
        ))
        db.flush()
        db.add(DeviceShopLink(
            device_sn="SN_B31_003", shop_pk=shop.id,
            effective_from=date(2026, 2, 1), is_current=True,
            match_source="manual",
        ))
        with pytest.raises(IntegrityError):
            db.flush()


class TestDimDeviceType:
    """设备类型维度"""

    def test_device_type_code_fk(self, db):
        db.add(DimDeviceType(type_code="B31_4G_CPE", type_name="4G CPE"))
        db.flush()
        device = Device(sn="SN_B31_004", device_type="4G CPE 原始文本", device_type_code="B31_4G_CPE")
        db.add(device)
        db.flush()
        assert device.device_type == "4G CPE 原始文本"  # 原文本保留
        assert device.device_type_code == "B31_4G_CPE"

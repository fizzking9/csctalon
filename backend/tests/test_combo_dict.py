# -*- coding: utf-8 -*-
"""B3-2 (#247) 套餐字典归一测试"""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.combo_dict import ComboDict, ComboNameAlias
from app.models.merchant import Merchant
from app.models.refund import Refund
from app.services.analytics.complaint import category_by_package, package_alias_coverage


class TestComboDictSchema:
    """schema 存在性"""

    def test_tables_exist(self):
        from app.core.database import Base
        assert "combo_dict" in Base.metadata.tables
        assert "combo_name_alias" in Base.metadata.tables


class TestComboNameAliasSCD:
    """别名映射 SCD 唯一约束"""

    def test_one_current_alias_per_raw_merchant(self, db):
        db.add(ComboDict(combo_code="B32_STD", canonical_name="标准套餐B32"))
        db.flush()
        db.add(ComboNameAlias(
            combo_code="B32_STD", raw_package_name="套餐B32原始",
            merchant_pk=None, effective_from=date(2026, 1, 1), is_current=True,
        ))
        db.flush()
        # 同一原始名+全局商户(空值)第二条当前映射 → 触发部分唯一索引
        db.add(ComboNameAlias(
            combo_code="B32_STD", raw_package_name="套餐B32原始",
            merchant_pk=None, effective_from=date(2026, 2, 1), is_current=True,
        ))
        with pytest.raises(IntegrityError):
            db.flush()

    def test_history_alias_allowed(self, db):
        db.add(ComboDict(combo_code="B32_STD2", canonical_name="标准套餐B32-2"))
        db.flush()
        db.add(ComboNameAlias(
            combo_code="B32_STD2", raw_package_name="套餐B32原始2",
            merchant_pk=None, effective_from=date(2025, 1, 1),
            effective_to=date(2026, 1, 1), is_current=False,
        ))
        db.add(ComboNameAlias(
            combo_code="B32_STD2", raw_package_name="套餐B32原始2",
            merchant_pk=None, effective_from=date(2026, 1, 1), is_current=True,
        ))
        db.flush()


class TestCategoryByPackage:
    """归一查询路径"""

    def test_canonical_fallback_to_raw(self, db):
        """无别名时回落原始名"""
        db.add(Refund(
            refund_no="RF_B32_001",
            platform_order_no="B32-001", package_name="原始套餐A",
            refund_amount=100, refund_method="线上", status="已退款",
            quality_status="normal",
        ))
        db.flush()
        result = category_by_package(db, limit=5)
        assert any(r["name"] == "原始套餐A" and r["value"] == 100.0 for r in result)

    def test_canonical_hit_with_alias(self, db):
        """SCD 时点命中时返回归一名"""
        merch = Merchant(sub_name="商户B32", merchant_id="MCH_B32")
        db.add(merch)
        db.flush()
        db.add(ComboDict(combo_code="B32_GOLD", canonical_name="黄金套餐"))
        db.flush()
        db.add(ComboNameAlias(
            combo_code="B32_GOLD", raw_package_name="黄金版(原始)",
            merchant_pk=None, effective_from=date(2025, 1, 1), is_current=True,
        ))
        db.add(Refund(
            refund_no="RF_B32_002",
            platform_order_no="B32-002", package_name="黄金版(原始)",
            refund_amount=200, refund_method="线上", status="已退款",
            quality_status="normal",
        ))
        db.flush()
        result = category_by_package(db, limit=5)
        assert any(r["name"] == "黄金套餐" and r["value"] == 200.0 for r in result)

    def test_coverage_on_empty(self, db):
        assert package_alias_coverage(db) == {
            "total": 0, "matched": 0, "unmatched": 0, "unmatched_ratio": 0.0,
        }

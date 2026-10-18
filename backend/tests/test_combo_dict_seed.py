# -*- coding: utf-8 -*-
"""套餐归一种子加载测试 (#288 套餐簇启用)

覆盖: 种子规模/内容健全性、首次灌入与幂等、不覆盖人工主字典与人工别名、
别名归一端到端生效。注意 apply_combo_seed 内部 commit, 测试结束显式清理并提交。
"""

from datetime import date

import pytest

from app.models.combo_dict import ComboDict, ComboNameAlias
from app.models.refund import Refund
from app.services import combo_dict_seed
from app.services.analytics.complaint import category_by_package


@pytest.fixture()
def clean_seed_tables(db):
    # 测试前后均清空两表并提交(apply 内部 commit 会落数据到测试库);
    # E2E 用例插入的 refunds 一并清理, 避免污染 coverage 统计
    def _clean():
        db.query(Refund).filter(Refund.refund_no.like("RF_SEED_%")).delete()
        db.query(ComboNameAlias).delete()
        db.query(ComboDict).delete()
        db.commit()
    _clean()
    yield
    _clean()


def _pick_multi_alias_code() -> tuple[str, list[str]]:
    # 从种子里找一个 >=2 别名的归一码, 用于验证格式差异原始名被归并
    payload = combo_dict_seed.load_payload()
    by_code: dict[str, list[str]] = {}
    for a in payload["aliases"]:
        by_code.setdefault(a["combo_code"], []).append(a["raw_package_name"])
    for cd, raws in by_code.items():
        if len(raws) >= 2:
            return cd, raws[:2]
    raise AssertionError("种子中无多别名归一码")


class TestSeedPayload:
    """随代码发布的种子 JSON 健全性"""

    def test_payload_shape(self):
        payload = combo_dict_seed.load_payload()
        assert payload["version"] == "20261009"
        assert len(payload["combos"]) == 635
        assert len(payload["aliases"]) == 1161

    def test_alias_references_combo(self):
        # 每条别名必须指向存在的归一码, 否则消费 join 落空
        payload = combo_dict_seed.load_payload()
        codes = {c["combo_code"] for c in payload["combos"]}
        for a in payload["aliases"]:
            assert a["combo_code"] in codes
            assert a["raw_package_name"]
            date.fromisoformat(a["effective_from"])  # 日期可解析

    def test_aliases_unique_per_raw_name(self):
        # 全局别名同一原始名只能出现一次(部分唯一索引约束)
        payload = combo_dict_seed.load_payload()
        raws = [a["raw_package_name"] for a in payload["aliases"]]
        assert len(raws) == len(set(raws))


class TestApplyIdempotent:
    """首次灌入与幂等"""

    def test_apply_twice(self, db, clean_seed_tables):
        first = combo_dict_seed.apply_combo_seed(db)
        assert first["combos_added"] == 635
        assert first["aliases_added"] == 1161
        # 第二次调用全部跳过
        second = combo_dict_seed.apply_combo_seed(db)
        assert second["combos_added"] == 0
        assert second["aliases_added"] == 0
        # 行数与种子一致
        assert db.query(ComboDict).count() == 635
        assert db.query(ComboNameAlias).count() == 1161
        # 全部为全局当前别名
        a = db.query(ComboNameAlias).first()
        assert a.merchant_pk is None and a.is_current and a.source == "seed"


class TestNoOverwriteManual:
    """种子不覆盖人工维护内容"""

    def test_manual_combo_kept(self, db, clean_seed_tables):
        db.add(ComboDict(combo_code="std_1500g_m_s", canonical_name="人工标准名"))
        db.commit()
        combo_dict_seed.apply_combo_seed(db)
        row = db.get(ComboDict, "std_1500g_m_s")
        assert row.canonical_name == "人工标准名"  # 不被种子覆盖

    def test_manual_alias_kept(self, db, clean_seed_tables):
        # 预置一条人工当前别名(指向人工套餐), 种子不应改写其 combo_code
        db.add(ComboDict(combo_code="MANUAL_X", canonical_name="人工套餐X"))
        raw = "【1500GB/月】单通道"
        db.add(ComboNameAlias(
            combo_code="MANUAL_X", raw_package_name=raw,
            merchant_pk=None, effective_from=date(2026, 1, 1),
            is_current=True, source="manual",
        ))
        db.commit()
        combo_dict_seed.apply_combo_seed(db)
        rows = db.query(ComboNameAlias).filter_by(raw_package_name=raw).all()
        assert len(rows) == 1
        assert rows[0].combo_code == "MANUAL_X"  # 不被种子覆盖, 也不新增第二条


class TestUnificationE2E:
    """灌种后格式差异原始名在统计口径中归一"""

    def test_variants_merge_to_canonical(self, db, clean_seed_tables):
        combo_dict_seed.apply_combo_seed(db)
        cd, raws = _pick_multi_alias_code()
        for i, raw in enumerate(raws):
            db.add(Refund(
                refund_no=f"RF_SEED_{i}", platform_order_no=f"PO_SEED_{i}",
                package_name=raw, refund_amount=100,
                refund_method="线上", status="已退款", quality_status="normal",
            ))
        db.commit()
        # #332 T8: 套餐归一取数 std_refund_online —— 先跑采纳作业
        from app.services.governance.refund_adoption import run_refund_adoption

        run_refund_adoption(db, routes="A", commit=False)
        result = category_by_package(db, limit=500)
        # 两个变体应合并为同一标准名一行, 金额合计 200
        hits = [r for r in result if r["name"]
                == db.get(ComboDict, cd).canonical_name]
        assert len(hits) == 1
        assert hits[0]["value"] == 200.0

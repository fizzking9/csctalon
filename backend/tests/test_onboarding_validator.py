# -*- coding: utf-8 -*-
"""AI 数据接入助手·校验器测试（工单 #155）

对应设计: docs/design/AI数据接入助手设计-20260919.md §四（R1~R14 规则表）。

为什么要这组测试:
  校验器是"AI 起草 → 人工确认"链路中**唯一的确定性闸门** —— LLM 可能幻觉，
  但规则必须 100% 可预测。每条规则的正反用例锁死行为，保证：
  ① fail 未清零禁止确认（is_confirmable=False）；
  ② 命名/分组/去重红线在起草阶段即被拦截（而非 CI 阶段才暴露）。

组织方式:
  - _valid_spec() 构造全绿基线声明，每个用例只破坏一条规则 → 断言只命中该规则；
  - RegistrySnapshot 直接手工构造（纯单测），另加一条 db fixture 集成用例验证
    build_registry_snapshot 从真实库取快照。
"""

from __future__ import annotations

from app.models.data_source import DataSource, DataSourceField
from app.services.onboarding_validator import (
    _ALLOWED_PREFIXES,
    RegistrySnapshot,
    build_registry_snapshot,
    is_confirmable,
    validate_spec,
)


def _valid_spec(**overrides) -> dict:
    """全绿基线声明：14 条规则全部通过。"""
    spec = {
        "code": "new_source",
        "table_name": "new_source_tbl",
        "name_cn": "设备·新数据源",
        "category": "基础数据",
        "sort_order": 10,
        "time_field": "fetched_at",
        "sn_field": "device_sn",
        "pk_field": "id",
        "pk_strategy": "primary_key",
        "sync_source": "bms",
        "description": "新数据源样例，供校验器基线测试",
        "field_specs": [
            {
                "field_name": "device_sn",
                "name_cn": "设备SN",
                "semantic_role": "sn",
                "data_type": "string",
                "confidence": 0.99,
            },
            {
                "field_name": "fetched_at",
                "name_cn": "抓取时间",
                "semantic_role": "time",
                "data_type": "timestamp",
                "confidence": 0.99,
                "time_zone_note": "Asia/Shanghai",
            },
            {
                "field_name": "amount",
                "name_cn": "金额",
                "semantic_role": "amount",
                "data_type": "numeric",
                "confidence": 0.95,
                "unit": "分",
            },
        ],
        "dedup_recommendation": None,
    }
    spec.update(overrides)
    return spec


def _empty_snapshot() -> RegistrySnapshot:
    return RegistrySnapshot()


def _fails(report: dict, rule: str) -> list:
    return [i for i in report.get("fail", []) if i["rule"] == rule]


def _warns(report: dict, rule: str) -> list:
    return [i for i in report.get("warn", []) if i["rule"] == rule]


def test_valid_spec_passes_all_rules():
    """基线：全绿声明零 fail 零 warn，可确认。"""
    report = validate_spec(_valid_spec(), _empty_snapshot())
    assert report["fail"] == [], report["fail"]
    assert report["warn"] == [], report["warn"]
    assert is_confirmable(report)


class TestCodeTableNameUniqueness:
    """R1/R2/R3: code / table_name / name_cn 全树唯一。"""

    def test_r1_code_conflict_with_source(self):
        snap = RegistrySnapshot(sources=[{"code": "new_source", "table_name": "x", "name_cn": "x", "category": "x"}])
        assert _fails(validate_spec(_valid_spec(), snap), "R1")

    def test_r1_code_conflict_with_projection(self):
        snap = RegistrySnapshot(projections=[{"code": "new_source", "name_cn": "投影·名"}])
        assert _fails(validate_spec(_valid_spec(), snap), "R1")

    def test_r2_table_conflict(self):
        snap = RegistrySnapshot(sources=[{"code": "other", "table_name": "new_source_tbl", "name_cn": "x", "category": "x"}])
        assert _fails(validate_spec(_valid_spec(), snap), "R2")

    def test_r3_name_cn_conflict(self):
        snap = RegistrySnapshot(sources=[{"code": "other", "table_name": "x", "name_cn": "设备·新数据源", "category": "x"}])
        assert _fails(validate_spec(_valid_spec(), snap), "R3")


class TestNamingRules:
    """R4/R5/R6/R7: 前缀 / code 泄漏 / 分组 / 警示标注。"""

    def test_r4_missing_prefix(self):
        assert _fails(validate_spec(_valid_spec(name_cn="新数据源"), _empty_snapshot()), "R4")

    def test_r4_unlisted_prefix(self):
        assert _fails(validate_spec(_valid_spec(name_cn="自定义·新数据源"), _empty_snapshot()), "R4")

    def test_r4_allows_all_whitelist_prefixes(self):
        """白名单内每个前缀都可用（防前缀表收缩误伤）。"""
        for prefix in _ALLOWED_PREFIXES:
            report = validate_spec(_valid_spec(name_cn=f"{prefix}·新数据源"), _empty_snapshot())
            assert not _fails(report, "R4"), f"前缀 {prefix} 应合法"

    def test_r5_code_leak_in_name(self):
        assert _fails(validate_spec(_valid_spec(name_cn="设备·new_source台账"), _empty_snapshot()), "R5")

    def test_r6_category_not_registered(self):
        assert _fails(validate_spec(_valid_spec(category="未登记分组"), _empty_snapshot()), "R6")

    def test_r7_warns_when_marker_only_in_description(self):
        report = validate_spec(
            _valid_spec(description="本表已停用，不再作统计取数"), _empty_snapshot()
        )
        assert _warns(report, "R7")

    def test_r7_no_warn_when_name_marked(self):
        report = validate_spec(
            _valid_spec(name_cn="⚠️旧·已停用数据", description="本表已停用"), _empty_snapshot()
        )
        assert not _warns(report, "R7")


class TestDedupRedline:
    """R8: 去重键红线 —— upsert 必须给出 dedup 建议。"""

    def test_r8_upsert_without_dedup(self):
        spec = _valid_spec(pk_strategy="upsert", dedup_recommendation=None)
        report = validate_spec(spec, _empty_snapshot())
        assert _fails(report, "R8")

    def test_r8_upsert_with_fields_but_no_rationale(self):
        spec = _valid_spec(
            pk_strategy="upsert",
            dedup_recommendation={"strategy": "upsert", "fields": ["order_no"], "rationale": ""},
        )
        assert _fails(validate_spec(spec, _empty_snapshot()), "R8")

    def test_r8_upsert_without_human_confirm_flag(self):
        spec = _valid_spec(
            pk_strategy="upsert",
            dedup_recommendation={
                "strategy": "upsert",
                "fields": ["order_no"],
                "rationale": "订单号唯一，参照去重规范主键策略",
            },
        )
        assert _fails(validate_spec(spec, _empty_snapshot()), "R8")

    def test_r8_upsert_full_dedup_passes(self):
        spec = _valid_spec(
            pk_strategy="upsert",
            dedup_recommendation={
                "strategy": "upsert",
                "fields": ["order_no"],
                "rationale": "订单号唯一，参照去重规范主键策略",
                "requires_human_confirm": True,
            },
        )
        report = validate_spec(spec, _empty_snapshot())
        assert not _fails(report, "R8")
        assert is_confirmable(report) or report["fail"] == [], report["fail"]


class TestFieldRules:
    """R9~R14: 字段级规则。"""

    def test_r9_sn_not_in_fields(self):
        report = validate_spec(_valid_spec(sn_field="device_no"), _empty_snapshot())
        assert _warns(report, "R9")

    def test_r10_time_no_zone(self):
        fields = [f for f in _valid_spec()["field_specs"] if f["field_name"] != "fetched_at"]
        fields.append({"field_name": "fetched_at", "name_cn": "抓取时间", "semantic_role": "time", "data_type": "timestamp", "confidence": 0.9})
        report = validate_spec(_valid_spec(field_specs=fields), _empty_snapshot())
        assert _warns(report, "R10")

    def test_r11_invalid_role(self):
        fields = [f for f in _valid_spec()["field_specs"]]
        fields.append({"field_name": "weird", "semantic_role": "monetary", "confidence": 0.9})
        assert _fails(validate_spec(_valid_spec(field_specs=fields), _empty_snapshot()), "R11")

    def test_r12_low_confidence_warns(self):
        fields = [f for f in _valid_spec()["field_specs"]]
        fields.append({"field_name": "guess_col", "semantic_role": "category", "confidence": 0.5})
        assert _warns(validate_spec(_valid_spec(field_specs=fields), _empty_snapshot()), "R12")

    def test_r13_amount_without_unit(self):
        fields = [f for f in _valid_spec()["field_specs"] if f["field_name"] != "amount"]
        fields.append({"field_name": "amount", "semantic_role": "amount", "data_type": "numeric", "confidence": 0.95})
        assert _fails(validate_spec(_valid_spec(field_specs=fields), _empty_snapshot()), "R13")

    def test_r14_role_conflict_warns(self):
        snap = RegistrySnapshot(field_roles={"amount": {"amount"}})
        fields = [f for f in _valid_spec()["field_specs"]]
        fields.append({"field_name": "amount", "semantic_role": "category", "confidence": 0.9})
        assert _warns(validate_spec(_valid_spec(field_specs=fields), snap), "R14")


class TestFramework:
    """框架级不变量：可确认性 / 规则覆盖完整性。"""

    def test_is_confirmable_false_when_any_fail(self):
        report = validate_spec(_valid_spec(category="未登记分组"), _empty_snapshot())
        assert not is_confirmable(report)

    def test_all_14_rules_covered_by_tests(self):
        """规则覆盖回归：测试中每个 R 编号至少出现一次（防新增规则无测试）。"""
        import re

        source = open(__file__, encoding="utf-8").read()
        used = set(re.findall(r"\bR(\d+)\b", source))
        # 代码里出现过的 R 编号
        expected = {str(n) for n in range(1, 15)}
        missing = expected - used
        assert not missing, f"规则 R{','.join(sorted(missing))} 无测试引用"


class TestSnapshotFromDb:
    """build_registry_snapshot 集成：从真实库取快照并拦截冲突。"""

    def test_snapshot_catches_existing_source(self, db):
        ds = DataSource(
            code="existing_source",
            table_name="existing_tbl",
            name_cn="设备·已有源",
            category="基础数据",
            status="active",
        )
        db.add(ds)
        db.flush()
        db.add(DataSourceField(source_id=ds.id, field_name="amount", semantic_role="amount", data_type="numeric"))
        db.flush()

        snap = build_registry_snapshot(db)
        assert any(s["code"] == "existing_source" for s in snap.sources)
        assert snap.field_roles["amount"] == {"amount"}

        # 冲突声明应被 R1 拦截
        report = validate_spec(_valid_spec(code="existing_source"), snap)
        assert _fails(report, "R1")
        assert not is_confirmable(report)

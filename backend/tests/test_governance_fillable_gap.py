"""D6 可推断空缺探测器测试（工单 #170，设计 §3.3）

对应工单验收：
  1. 三类判据（A SN→设备属性 / B 订单→SN / C 别名→标准人）各报出；
     **无权威来源的空缺不报**（防「猜」）—— 反例断言
  2. 样本必含**推断来源**（`_source`：哪张表哪一行推出的值），可人工核对
  3. `metrics` 与 `repro_sql` 可对账（逐段跑 repro_sql 的数字 == metrics）
  4. 真实规则清单引用的列在生产库上确实存在（防「规则写了但列名错 → 静默跳过」）

手法：D6 的规则清单是模块常量（写的是**真实表名**）。为了让计数**精确可断言**
（不受其它用例残留数据影响），引擎用例把 `_FILLABLE_RULES` 临时替换为**临时表**版本 ——
判据逻辑同一份，只换表名；真实规则清单另用「列存在性」用例守住（见 TestRealRules）。

临时表建在 public（`_table_exists` 只看 public），测试事务回滚后自动消失（DDL 可回滚）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.governance_detector import (
    _FILLABLE_RULES,
    DETECTOR_FILLABLE_GAP,
    RULE_ALIAS_TO_PERSON,
    RULE_ORDER_TO_SN,
    RULE_SN_TO_DEVICE_TYPE,
    _alias_rule_specs,
    _column_exists,
    _table_exists,
    scan_fillable_gaps,
)


def _t(prefix: str) -> str:
    return f"e3test_{prefix}_{uuid.uuid4().hex[:8]}"


def _mk(db: Session, table: str, ddl: str, cols: str = "", body: str = "") -> None:
    """建表 + 灌数据（`cols` 为 INSERT 列清单，与 `body` 的 VALUES 部分配套）。"""
    db.execute(text(f"CREATE TABLE {table} ({ddl})"))
    if body:
        db.execute(text(f"INSERT INTO {table} ({cols}) VALUES {body}"))
    db.flush()


def _repro_counts(db: Session, sql: str) -> list[int]:
    """逐段执行 repro_sql（丢掉 `--` 注释行），返回每段 count（用于与 metrics 对账）。"""
    out: list[int] = []
    for chunk in sql.split(";"):
        stmt = "\n".join(line for line in chunk.splitlines()
                         if not line.strip().startswith("--")).strip()
        if stmt:
            out.append(int(db.execute(text(stmt)).scalar()))
    return out


# ─────────────────────────────────────────────────────────────────────
# 一、引擎：三类判据（临时表，计数精确）
# ─────────────────────────────────────────────────────────────────────
class TestRuleA:
    """A 类 SN→设备属性：空缺来自**同 SN 在其它表上的设备类型**。"""

    @staticmethod
    def _setup(db: Session) -> tuple[str, dict]:
        dev, src_flow, src_order = _t("dev"), _t("flow"), _t("order")
        _mk(db, dev, "sn varchar(32) PRIMARY KEY, device_type varchar(64)",
            "sn, device_type",
            "('SNA1', NULL), ('SNA2', NULL), ('SNA3', NULL), ('SNA4', NULL), "
            "('SNA5', 'GT-3K皓月'), ('SNA6', '')")
        # 来源一（优先级高）：SN → 设备类型
        _mk(db, src_flow, "id int PRIMARY KEY, device_sn varchar(32), device_type_name varchar(64)",
            "id, device_sn, device_type_name",
            "(1, 'SNA1', 'GT-3K皓月'), (2, 'SNA3', 'AAA'), (3, 'SNA5', 'GT-3K皓月')")
        # 来源二：订单 → SN + 设备类型
        _mk(db, src_order, "order_no varchar(32) PRIMARY KEY, device_sn varchar(32), device_type_name varchar(64)",
            "order_no, device_sn, device_type_name",
            "('O1', 'SNA1', 'GT-3K皓月'), ('O2', 'SNA2', 'YKM-M5-GT'), "
            "('O3', 'SNA3', 'BBB'), ('O4', 'SNA5', 'GT-3K皓月')")
        spec = {
            "rule": RULE_SN_TO_DEVICE_TYPE, "table": dev, "column": "device_type",
            "key_col": "sn", "pk_col": "sn", "text": True,
            "sources": (
                {"table": src_flow, "key_col": "device_sn", "value_col": "device_type_name",
                 "pk_col": "id", "text": True},
                {"table": src_order, "key_col": "device_sn", "value_col": "device_type_name",
                 "pk_col": "order_no", "text": True},
            ),
        }
        return dev, {"spec": spec, "flow": src_flow, "order": src_order}

    def test_reports_only_fillable_rows(self, db, monkeypatch):
        dev, ctx = self._setup(db)
        monkeypatch.setattr(
            "app.services.governance_detector._FILLABLE_RULES", (ctx["spec"],))

        findings = scan_fillable_gaps(db, tables=[dev])
        assert len(findings) == 1, [f["target"] for f in findings]
        f = findings[0]
        m = f["metrics"]
        assert f["detector"] == DETECTOR_FILLABLE_GAP
        assert f["target"] == f"{dev}.device_type"
        # 可补 = SNA1/SNA2/SNA3（SNA4 无来源、SNA6 是空串且无来源 → 不报）
        assert m["fillable_rows"] == 3
        # 空缺 = SNA1~SNA4 + SNA6（空串与 NULL 同义）
        assert m["total_null_rows"] == 5
        assert m["ratio"] == round(3 / 5, 6)
        assert m["rule"] == RULE_SN_TO_DEVICE_TYPE
        assert m["sources"] == {ctx["flow"]: 2, ctx["order"]: 3}
        assert m["writable"] is False  # 本期不自动写值

    def test_samples_carry_inference_source(self, db, monkeypatch):
        dev, ctx = self._setup(db)
        monkeypatch.setattr(
            "app.services.governance_detector._FILLABLE_RULES", (ctx["spec"],))

        samples = {s["_id"]: s for s in scan_fillable_gaps(db, tables=[dev])[0]["samples"]}
        # 反例：无权威来源的空缺**不得**出现在样本里
        assert "SNA4" not in samples and "SNA6" not in samples
        assert "SNA5" not in samples  # 已有值 → 不是空缺

        # 样本必含推断来源（工单验收 2：人工可核对「这个值从哪来」）
        for s in samples.values():
            assert s["_source"]["table"] and s["_source"]["pk"] is not None
            assert s["_source"]["column"] and s["_source"]["key"] is not None

        # 多来源同值 → 取优先级高的来源，且不记备选
        assert samples["SNA1"]["suggested"] == "GT-3K皓月"
        assert samples["SNA1"]["_source"]["table"] == ctx["flow"]
        assert "_alts" not in samples["SNA1"]
        # 仅来源二有值 → 来源记来源二
        assert samples["SNA2"]["_source"]["table"] == ctx["order"]
        # 两来源给出**不同**值 → 必须暴露（人工才知道该信谁，不做静默取舍）
        assert samples["SNA3"]["suggested"] == "AAA"
        assert [a["value"] for a in samples["SNA3"]["_alts"]] == ["BBB"]

    def test_repro_sql_matches_metrics(self, db, monkeypatch):
        """D1 铁律：人工跑 repro_sql 的数字必须与 metrics 逐一对上。"""
        dev, ctx = self._setup(db)
        monkeypatch.setattr(
            "app.services.governance_detector._FILLABLE_RULES", (ctx["spec"],))

        f = scan_fillable_gaps(db, tables=[dev])[0]
        counts = _repro_counts(db, f["repro_sql"])
        assert counts[0] == f["metrics"]["total_null_rows"]
        assert counts[1] == f["metrics"]["fillable_rows"]
        assert counts[2:] == list(f["metrics"]["sources"].values())


class TestRuleB:
    """B 类 订单→SN：退费类表 device_sn 空，但同订单号在其它通道有 SN。"""

    def test_reports_and_ignores_empty_source(self, db, monkeypatch):
        mr, rrr = _t("mr"), _t("rrr")
        _mk(db, mr, "id int PRIMARY KEY, order_no varchar(32), device_sn varchar(32)",
            "id, order_no, device_sn",
            "(1, 'OB1', NULL), (2, 'OB2', NULL), (3, 'OB3', 'SNB3')")
        # OB1 有一行给出 SN；OB2 只有空值（源侧为空 = 没有权威来源）→ 不报
        _mk(db, rrr, "id int PRIMARY KEY, platform_order_no varchar(32), device_sn varchar(32)",
            "id, platform_order_no, device_sn",
            "(11, 'OB1', 'SNB1'), (12, 'OB1', NULL), (13, 'OB2', NULL)")
        spec = {
            "rule": RULE_ORDER_TO_SN, "table": mr, "column": "device_sn",
            "key_col": "order_no", "pk_col": "id", "text": True,
            "sources": ({"table": rrr, "key_col": "platform_order_no",
                         "value_col": "device_sn", "pk_col": "id", "text": True},),
        }
        monkeypatch.setattr(
            "app.services.governance_detector._FILLABLE_RULES", (spec,))

        f = scan_fillable_gaps(db, tables=[mr])[0]
        assert f["metrics"]["fillable_rows"] == 1  # 只有 OB1
        assert f["metrics"]["total_null_rows"] == 2
        assert f["metrics"]["sources"] == {rrr: 1}
        assert f["metrics"]["rule"] == RULE_ORDER_TO_SN
        assert [s["_id"] for s in f["samples"]] == [1]
        assert f["samples"][0]["suggested"] == "SNB1"
        assert f["samples"][0]["_source"] == {
            "table": rrr, "key": "OB1", "key_column": "order_no",
            "pk": 11, "pk_column": "id", "column": "device_sn", "value": "SNB1"}
        assert _repro_counts(db, f["repro_sql"]) == [2, 1, 1]

    def test_no_source_hit_means_no_finding(self, db, monkeypatch):
        """**反例（防「猜」）**：没有任何来源能推出来 → 一条 finding 都不产。"""
        mr, rrr = _t("mr"), _t("rrr")
        _mk(db, mr, "id int PRIMARY KEY, order_no varchar(32), device_sn varchar(32)",
            "id, order_no, device_sn", "(1, 'ONLY1', NULL)")
        _mk(db, rrr, "id int PRIMARY KEY, platform_order_no varchar(32), device_sn varchar(32)",
            "id, platform_order_no, device_sn", "(11, 'OTHER', 'SNX')")
        spec = {
            "rule": RULE_ORDER_TO_SN, "table": mr, "column": "device_sn",
            "key_col": "order_no", "pk_col": "id", "text": True,
            "sources": ({"table": rrr, "key_col": "platform_order_no",
                         "value_col": "device_sn", "pk_col": "id", "text": True},),
        }
        monkeypatch.setattr(
            "app.services.governance_detector._FILLABLE_RULES", (spec,))
        assert scan_fillable_gaps(db, tables=[mr]) == []

    def test_missing_source_table_skips_rule(self, db, monkeypatch):
        """权威来源表不存在 → 推不出，跳过该规则（不是「空缺」而是「无依据」）。"""
        mr = _t("mr")
        _mk(db, mr, "id int PRIMARY KEY, order_no varchar(32), device_sn varchar(32)",
            "id, order_no, device_sn", "(1, 'OB1', NULL)")
        spec = {
            "rule": RULE_ORDER_TO_SN, "table": mr, "column": "device_sn",
            "key_col": "order_no", "pk_col": "id", "text": True,
            "sources": ({"table": _t("nope"), "key_col": "platform_order_no",
                         "value_col": "device_sn", "pk_col": "id", "text": True},),
        }
        monkeypatch.setattr(
            "app.services.governance_detector._FILLABLE_RULES", (spec,))
        assert scan_fillable_gaps(db, tables=[mr]) == []


class TestRuleC:
    """C 类 别名→标准人：人员文本列能在 person_aliases 命中**已归并**的 person_id。"""

    @staticmethod
    def _setup(db: Session) -> tuple[str, dict]:
        op, alias = _t("op"), _t("alias")
        _mk(db, op, "id int PRIMARY KEY, operator_name varchar(64), person_id int",
            "id, operator_name, person_id",
            "(1, '企微-张三C', NULL), (2, '无别名C', NULL), (3, '企微-李四C', 7)")
        # 别名存在但 person_id 为空 = **尚未归并** → 推不出（源里查不到就不许推）
        _mk(db, alias, "id int PRIMARY KEY, alias varchar(128), person_id int",
            "id, alias, person_id", "(1, '企微-张三C', 101), (2, '无别名C', NULL)")
        spec = {
            "rule": RULE_ALIAS_TO_PERSON, "table": op, "column": "person_id",
            "key_col": "operator_name", "pk_col": "id", "text": False,
            "sources": ({"table": alias, "key_col": "alias",
                         "value_col": "person_id", "pk_col": "id", "text": False},),
        }
        return op, {"spec": spec, "alias": alias}

    def test_reports_and_requires_assigned_alias(self, db, monkeypatch):
        op, ctx = self._setup(db)
        monkeypatch.setattr(
            "app.services.governance_detector._FILLABLE_RULES", (ctx["spec"],))

        findings = scan_fillable_gaps(db, tables=[op])
        assert len(findings) == 1
        f = findings[0]
        assert f["target"] == f"{op}.person_id"
        assert f["metrics"]["rule"] == RULE_ALIAS_TO_PERSON
        # 只有「企微-张三C」可推；「无别名C」的别名未归并 → 不报
        assert f["metrics"]["fillable_rows"] == 1
        assert f["metrics"]["total_null_rows"] == 2
        assert f["metrics"]["sources"] == {ctx["alias"]: 1}
        s = f["samples"][0]
        assert s["suggested"] == 101  # 整数列的值也要能落 JSONB
        assert s["_source"]["table"] == ctx["alias"]
        assert s["_source"]["key"] == "企微-张三C"
        assert _repro_counts(db, f["repro_sql"]) == [2, 1, 1]


# ─────────────────────────────────────────────────────────────────────
# 二、真实规则清单与注册（守住「配置写错 → 静默跳过」）
# ─────────────────────────────────────────────────────────────────────
class TestRealRules:
    def test_rule_columns_exist(self, db):
        """规则里写的表/列必须真实存在 —— 否则 `_column_exists` 会静默跳过整条规则。"""
        assert _FILLABLE_RULES, "固定规则清单不得为空"
        for spec in _FILLABLE_RULES:
            assert _table_exists(db, spec["table"]), spec["table"]
            for col in (spec["column"], spec["key_col"], spec["pk_col"]):
                assert _column_exists(db, spec["table"], col), f"{spec['table']}.{col}"
            for s in spec["sources"]:
                for col in (s["key_col"], s["value_col"], s["pk_col"]):
                    assert _column_exists(db, s["table"], col), f"{s['table']}.{col}"

    def test_three_rule_kinds_covered(self):
        rules = {s["rule"] for s in _FILLABLE_RULES}
        assert rules == {RULE_SN_TO_DEVICE_TYPE, RULE_ORDER_TO_SN}

    def test_alias_specs_derive_from_person_field_sources(self, db):
        """C 类字段清单的**唯一来源**是 PERSON_FIELD_SOURCES（不另写一份，防漂移）。"""
        from app.services.person_extractor import PERSON_FIELD_SOURCES

        specs = _alias_rule_specs(db)
        got = {(s["table"], s["key_col"]) for s in specs}
        assert ("scrm_operators", "operator_name") in got
        assert ("scrm_sessions", "operator_name") in got
        assert all(s["rule"] == RULE_ALIAS_TO_PERSON for s in specs)
        # 只保留**有 person_id 列**的表（没有可补的列 → 补了没地方放）
        assert all(_column_exists(db, t, "person_id") for t, _ in got)
        # 不在 PERSON_FIELD_SOURCES 里的表不得进来（如 scrm_staff_profiles 有 person_id 但不在清单内）
        assert "scrm_staff_profiles" not in {t for t, _ in got}
        assert {t for t, _ in got} <= {t for _m, _a, t, _f in PERSON_FIELD_SOURCES}


class TestWiring:
    def test_detector_in_api_whitelist(self):
        from app.api.v1.governance import DETECTORS

        assert DETECTOR_FILLABLE_GAP in DETECTORS

    def test_gather_findings_runs_d6(self, db, monkeypatch):
        """D6 必须进 `gather_findings` 的枚举 —— 它同时决定「重扫收敛」范围（#167）。"""
        import app.services.governance_scan_service as S

        for name in ("scan_unique_index_dups", "scan_row_count_jump", "scan_fk_orphans",
                     "scan_text_anomaly", "scan_impossible_dates"):
            monkeypatch.setattr(S, name, lambda _db: [])
        called: list[int] = []

        def _fake(_db):
            called.append(1)
            return []

        monkeypatch.setattr(S, "scan_fillable_gaps", _fake)
        _findings, ok = S.gather_findings(db)
        assert called == [1]
        assert DETECTOR_FILLABLE_GAP in ok

    def test_spec_rules_injected_by_detector(self):
        """判据段按 detector 注入（设计 §五）：D6 段含「不写值」红线，且不夹带 D4 段。"""
        from app.services.governance_judge_service import build_spec_rules

        txt = build_spec_rules({DETECTOR_FILLABLE_GAP})
        assert "可推断空缺（D6" in txt
        assert "本期一律不写值" in txt
        assert "_source" in txt and "_alts" in txt
        assert "文本异常（D4" not in txt

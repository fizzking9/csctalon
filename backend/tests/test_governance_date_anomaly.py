"""D5 不可能日期探测器 + 导入期留痕测试（工单 #169）

对应设计 §3.2/§四 与工单验收：
  1. D5 正/反用例：年份越界 / 未来日期 / 时间倒挂 / 兜底值聚集 各报出；
     **真实集中日期（大促）不误报**、计划类列（有效期）不误报、系统审计列不扫
  2. 判据同源：导入期 `impossible_kinds`（Python）与扫描期 `sql_predicates`（SQL）
     对同一份数据结论一致（同一组常量）
  3. `metrics` / `repro_sql` 可对账：逐段跑 repro_sql 的数字必须等于 `metrics.counts`
  4. 导入期留痕：越界日期写一条 `IngestRejectedRow(date_impossible)` 且**入库数据不变**

临时表建在 public（探测器只扫 public 的 data_sources 登记表），与 D4 测试同一手法。
"""

from __future__ import annotations

import datetime as _dt
import re
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.data_source import DataSource
from app.services import date_anomaly_rules as DR
from app.services.governance_detector import DETECTOR_IMPOSSIBLE_DATE, scan_impossible_dates

_TZ = _dt.timezone(_dt.timedelta(hours=8))


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _reg(db: Session, table: str, code: str) -> None:
    db.add(DataSource(code=code, table_name=table, name_cn=f"测试-{code}",
                      source_type="business"))
    db.flush()


def _values(cols: str, rows: list[tuple]) -> str:
    """拼 INSERT 的 `(cols) VALUES (..), (..)` 部分（一行一条，避免手写漏逗号）。"""
    body = ", ".join("(" + ", ".join(str(v) for v in r) + ")" for r in rows)
    return f"({cols}) VALUES {body}"


def _mk(db: Session, table: str, ddl: str, insert_body: str, code: str) -> None:
    db.execute(text(f"CREATE TABLE {table} ({ddl})"))
    _reg(db, table, code)
    if insert_body:
        db.execute(text(f"INSERT INTO {table} {insert_body}"))
    db.flush()


def _ts(day: str) -> str:
    """北京时区的 timestamptz 字面量（测试数据一律显式带 +08:00，避免依赖会话时区）。"""
    return f"'{day}T10:00:00+08:00'"


def _one(findings: list[dict], col: str) -> dict:
    hits = [f for f in findings if f["target"].endswith(f".{col}")]
    assert hits, f"应报出 {col}，实际 {[f['target'] for f in findings]}"
    return hits[0]


# ─────────────────────────────────────────────────────────────────────
# 一、判据与纯函数（Python 侧）
# ─────────────────────────────────────────────────────────────────────
class TestRulesSameSource:
    def test_year_bounds(self):
        now = _dt.datetime(2026, 9, 20, tzinfo=_TZ)
        assert DR.max_year(now) == 2027
        assert DR.is_impossible_year(_dt.date(1900, 1, 1), now=now) is True
        assert DR.is_impossible_year(_dt.date(2014, 12, 31), now=now) is True
        assert DR.is_impossible_year(_dt.date(2015, 1, 1), now=now) is False
        assert DR.is_impossible_year(_dt.date(2027, 1, 1), now=now) is False
        assert DR.is_impossible_year(_dt.date(2028, 1, 1), now=now) is True

    def test_future_tolerance(self):
        now = _dt.datetime(2026, 9, 20, 10, 0, tzinfo=_TZ)
        assert DR.is_future(_dt.datetime(2026, 9, 21, 23, 0, tzinfo=_TZ), now=now) is False
        assert DR.is_future(_dt.datetime(2026, 9, 22, 1, 0, tzinfo=_TZ), now=now) is True

    def test_utc_boundary_uses_beijing_date(self):
        """时区铁律：UTC 2014-12-31 16:00 == 北京 2015-01-01 → 不越界（按 UTC 年判会误报）。"""
        now = _dt.datetime(2026, 6, 1, tzinfo=_TZ)
        assert DR.is_impossible_year(
            _dt.datetime(2014, 12, 31, 16, 0, tzinfo=_dt.timezone.utc), now=now) is False

    def test_default_like_date(self):
        assert DR.is_default_like_date(_dt.date(2020, 1, 1)) is True
        assert DR.is_default_like_date(_dt.date(2099, 12, 31)) is True
        assert DR.is_default_like_date(_dt.date(2025, 6, 18)) is False   # 大促

    def test_planned_column_skips_a_b(self):
        """计划类列（有效期/结束日）本就应在未来 → 判据 A/B 跳过。"""
        now = _dt.datetime(2026, 9, 20, tzinfo=_TZ)
        far = _dt.datetime(2030, 1, 1, tzinfo=_TZ)
        assert DR.impossible_kinds(far, column="service_valid_until", now=now) == []
        # 2030 既越界又是未来 → 两类同时命中（与扫描期一致）
        assert set(DR.impossible_kinds(far, column="finish_time", now=now)) == {
            DR.KIND_YEAR_RANGE, DR.KIND_FUTURE}
        assert DR.sql_predicates("x", column="end_date") == []

    def test_reversed_pairs_stage_inference(self):
        """阶段序推断：创建 → 处理 → 完成；同阶段不配对；不在阶段表的列不猜。"""
        cols = ["register_time", "finish_time", "op_time", "device_sn"]
        pairs = DR.reversed_pairs(cols)
        assert ("op_time", "register_time") in pairs
        assert ("finish_time", "register_time") in pairs
        assert ("finish_time", "op_time") in pairs
        assert all("device_sn" not in p for p in pairs)
        # 同阶段（op_time 与 audit_time 都是 1）不配对
        assert ("audit_time", "op_time") not in DR.reversed_pairs(["op_time", "audit_time"])
        # 只有一列 → 无对
        assert DR.reversed_pairs(["op_time"]) == []

    def test_sql_predicates_use_same_constants(self):
        preds = dict(DR.sql_predicates("(\"c\" AT TIME ZONE 'Asia/Shanghai')::date"))
        assert set(preds) == {DR.KIND_YEAR_RANGE, DR.KIND_FUTURE}
        assert str(DR.MIN_YEAR) in preds[DR.KIND_YEAR_RANGE]
        assert str(DR.max_year()) in preds[DR.KIND_YEAR_RANGE]
        assert str(DR.FUTURE_TOLERANCE_DAYS) in preds[DR.KIND_FUTURE]


# ─────────────────────────────────────────────────────────────────────
# 二、D5 探测器（正用例）
# ─────────────────────────────────────────────────────────────────────
def test_d5_reports_year_out_of_range(db):
    tbl, code = _uniq("gov_d5_yr"), _uniq("ds_d5y")
    _mk(db, tbl, "id bigserial PRIMARY KEY, register_time timestamptz, op_time timestamptz",
        _values("register_time", [(_ts(d),) for d in
                                 ("1900-01-01", "1999-12-31",
                                  "2026-01-05", "2026-02-06", "2026-03-07")]),
        code)

    f = _one(scan_impossible_dates(db, tables=[tbl]), "register_time")
    assert f["detector"] == DETECTOR_IMPOSSIBLE_DATE
    assert f["target"] == f"{tbl}.register_time"
    assert f["metrics"]["kinds"] == [DR.KIND_YEAR_RANGE]
    assert f["metrics"]["counts"][DR.KIND_YEAR_RANGE] == 2
    assert f["metrics"]["hit_rows"] == 2
    assert f["metrics"]["min_date"] == "1900-01-01"
    assert f["metrics"]["max_date"] == "2026-03-07"
    assert f["severity_raw"] == "high"
    # 样本带行 id（fix_time 的 pk 来源）与命中类别
    assert f["samples"][0]["_id"] is not None
    assert f["samples"][0]["_kind"] == DR.KIND_YEAR_RANGE


def test_d5_reports_future_date(db):
    tbl, code = _uniq("gov_d5_fu"), _uniq("ds_d5f")
    future = (DR.now_cn() + _dt.timedelta(days=30)).strftime("%Y-%m-%d")
    _mk(db, tbl, "id bigserial PRIMARY KEY, op_time timestamptz",
        _values("op_time", [(_ts(future),), (_ts("2026-01-05"),)]), code)

    f = _one(scan_impossible_dates(db, tables=[tbl]), "op_time")
    assert f["metrics"]["kinds"] == [DR.KIND_FUTURE]
    assert f["metrics"]["counts"][DR.KIND_FUTURE] == 1
    assert f["metrics"]["max_date"] == future


def test_d5_reports_reversed_pair(db):
    tbl, code = _uniq("gov_d5_rv"), _uniq("ds_d5r")
    _mk(db, tbl, "id bigserial PRIMARY KEY, register_time timestamptz, finish_time timestamptz",
        _values("register_time, finish_time", [
            (_ts("2026-03-10"), _ts("2026-03-01")),   # 倒挂（完成 < 登记）
            (_ts("2026-03-11"), _ts("2026-03-12")),   # 正常
        ]), code)

    f = _one(scan_impossible_dates(db, tables=[tbl]), "finish_time")
    assert f["metrics"]["kinds"] == [DR.KIND_REVERSED]
    assert f["metrics"]["counts"][DR.KIND_REVERSED] == 1
    assert f["metrics"]["reversed_pairs"] == [
        {"finish_col": "finish_time", "create_col": "register_time", "count": 1}]
    # 倒挂样本必须同时给「参照列」的值，否则人工看不出谁该改
    s = next(x for x in f["samples"] if x["_kind"] == DR.KIND_REVERSED)
    assert s["ref_column"] == "register_time" and s["ref_value"] == "2026-03-10"
    assert "2026-03-01" in s["value"]


def test_d5_reports_default_like_cluster(db):
    """兜底值聚集：6/10 行落在 2020-01-01（占比 0.6 ≥ 0.30，行数 ≥ 3，且形如兜底值）→ 报。"""
    tbl, code = _uniq("gov_d5_cl"), _uniq("ds_d5c")
    days = ["2020-01-01"] * 6 + ["2026-01-05", "2026-02-06", "2026-03-07", "2026-04-08"]
    _mk(db, tbl, "id bigserial PRIMARY KEY, op_time timestamptz",
        _values("op_time", [(_ts(d),) for d in days]), code)

    f = _one(scan_impossible_dates(db, tables=[tbl]), "op_time")
    assert f["metrics"]["kinds"] == [DR.KIND_CLUSTER]
    assert f["metrics"]["counts"][DR.KIND_CLUSTER] == 6
    assert f["metrics"]["cluster_ratio"] == 0.6
    assert f["metrics"]["nonnull_rows"] == 10
    assert f["metrics"]["top_dates"][0] == {"date": "2020-01-01", "count": 6, "ratio": 0.6}


# ─────────────────────────────────────────────────────────────────────
# 三、D5 探测器（反用例 —— 工单验收 1 的「不误报」）
# ─────────────────────────────────────────────────────────────────────
def test_d5_promo_cluster_not_reported(db):
    """★真实集中日期（大促）：占比 0.6 但月日不是 01-01/12-31 → 不报（防误报核心断言）。"""
    tbl, code = _uniq("gov_d5_pr"), _uniq("ds_d5p")
    days = ["2025-06-18"] * 6 + ["2026-01-05", "2026-02-06", "2026-03-07", "2026-04-08"]
    _mk(db, tbl, "id bigserial PRIMARY KEY, op_time timestamptz",
        _values("op_time", [(_ts(d),) for d in days]), code)

    assert scan_impossible_dates(db, tables=[tbl]) == [], "大促这类真实集中日期不得报出"


def test_d5_tiny_default_cluster_not_reported(db):
    """1 行占比 100% 的「伪聚集」不报（那只是个别坏值，判据 A/B 已能看见）。"""
    tbl, code = _uniq("gov_d5_tiny"), _uniq("ds_d5t")
    _mk(db, tbl, "id bigserial PRIMARY KEY, op_time timestamptz",
        _values("op_time", [(_ts("2020-01-01"),)]), code)

    assert scan_impossible_dates(db, tables=[tbl]) == []


def test_d5_planned_and_audit_columns_not_scanned(db):
    """计划类列（有效期/结束日，本就未来）+ 系统审计列（updated_at）都不扫。"""
    tbl, code = _uniq("gov_d5_pl"), _uniq("ds_d5pl")
    far = (DR.now_cn() + _dt.timedelta(days=400)).strftime("%Y-%m-%d")
    _mk(db, tbl,
        "id bigserial PRIMARY KEY, end_date timestamptz, service_valid_until timestamptz, "
        "updated_at timestamptz",
        _values("end_date, service_valid_until, updated_at",
                [(_ts(far), _ts(far), _ts("2099-12-31"))]),
        code)

    assert scan_impossible_dates(db, tables=[tbl]) == [], "有效期/审计列不得报出"


def test_d5_clean_table_not_reported(db):
    tbl, code = _uniq("gov_d5_ok"), _uniq("ds_d5o")
    _mk(db, tbl, "id bigserial PRIMARY KEY, register_time timestamptz, finish_time timestamptz",
        _values("register_time, finish_time", [
            (_ts("2026-01-05"), _ts("2026-01-06")),
            (_ts("2026-02-06"), _ts("2026-02-07")),
            (_ts("2026-03-07"), _ts("2026-03-08")),
        ]), code)

    assert scan_impossible_dates(db, tables=[tbl]) == []


def test_d5_wide_tables_are_skipped_by_design():
    """物化宽表是派生表（值从源表拷来、改了会被重建覆盖）→ 明确排除，不重复报、不假处置。"""
    from app.services.governance_detector import _SKIP_DATE_COLUMNS, _SKIP_DATE_TABLES

    assert {"refund_wide", "cash_flow_wide"} <= _SKIP_DATE_TABLES
    assert {"created_at", "updated_at", "synced_at"} <= _SKIP_DATE_COLUMNS


# ─────────────────────────────────────────────────────────────────────
# 四、metrics 与 repro_sql 可对账（D1 铁律：数字必须对得上）
# ─────────────────────────────────────────────────────────────────────
def _repro_compare(db: Session, sql: str) -> dict:
    """逐段执行 repro_sql，返回 {kind: 数字} / {'hit_rows': 数字}。

    按注释里的 `metrics.counts.<kind>` / `hit_rows` 标记取键 —— 顺带断言这两类标记存在
    （人工看 SQL 时才知道每段对应哪个指标）；`reversed 明细` / `top_dates` 段不参与对账。
    """
    out: dict = {}
    for part in sql.split("\n\n"):
        lines = part.split("\n")
        head = next((ln for ln in lines if ln.startswith("--")), "")
        body = "\n".join(ln for ln in lines if not ln.startswith("--")).strip().rstrip(";")
        if "metrics.counts." in head:
            kind = re.split(r"[；）]", head.split("metrics.counts.")[1])[0].strip()
            out[kind] = int(db.execute(text(body)).scalar() or 0)
        elif "hit_rows" in head:
            out["hit_rows"] = int(db.execute(text(body)).scalar() or 0)
    return out


def test_d5_metrics_match_repro_sql(db):
    """★可审计：跑 repro_sql 各段得到的数字 == metrics.counts / hit_rows。"""
    tbl, code = _uniq("gov_d5_rp"), _uniq("ds_d5rp")
    _mk(db, tbl, "id bigserial PRIMARY KEY, register_time timestamptz, op_time timestamptz, "
                 "finish_time timestamptz",
        _values("register_time, op_time, finish_time", [
            (_ts("2026-03-10"), _ts("2026-03-11"), _ts("2026-03-09")),   # finish < register 倒挂
            (_ts("2099-12-31"), _ts("2099-12-31"), _ts("2099-12-31")),   # 年份越界
            (_ts("2026-01-05"), _ts("2026-01-06"), _ts("2026-01-07")),
            (_ts("2026-02-06"), _ts("2026-02-07"), _ts("2026-02-08")),
        ]), code)

    findings = scan_impossible_dates(db, tables=[tbl])
    assert findings, "应至少报出 register_time 的年份越界"
    for f in findings:
        got = _repro_compare(db, f["repro_sql"])
        want = dict(f["metrics"]["counts"])
        want["hit_rows"] = f["metrics"]["hit_rows"]
        assert got == want, f"{f['target']} repro_sql 与 metrics 不一致: {got} != {want}"


def test_d5_findings_carry_target_and_time_field_flag(db):
    """`target` = `表.列`（便于按列 dismiss 沉淀规则）；`is_time_field` 标出权威时间列。"""
    tbl, code = _uniq("gov_d5_tf"), _uniq("ds_d5tf")
    _mk(db, tbl, "id bigserial PRIMARY KEY, register_time timestamptz",
        _values("register_time", [(_ts("2099-12-31"),)]), code)
    db.execute(text("UPDATE data_sources SET time_field='register_time' WHERE code=:c"),
               {"c": code})
    db.flush()

    f = _one(scan_impossible_dates(db, tables=[tbl]), "register_time")
    assert f["metrics"]["is_time_field"] is True
    assert f["metrics"]["col"] == "register_time"
    assert f["metrics"]["year_range"] == [DR.MIN_YEAR, DR.max_year()]


# ─────────────────────────────────────────────────────────────────────
# 五、导入期留痕（只看不改）
# ─────────────────────────────────────────────────────────────────────
def test_import_date_suspect_leaves_data_untouched(_engine):
    """导入期检测写留痕，但 **df 一字不改**，原始值保留在 raw_row。"""
    import pandas as pd

    from app.models.ingest_rejected_row import IngestRejectedRow
    from app.services.upsert import _persist_date_suspects

    tbl, code = _uniq("gov_impd"), _uniq("ds_impd")
    with Session(_engine) as s:
        base = s.execute(text("SELECT coalesce(max(id),0) FROM ingest_rejected_rows")).scalar()
        try:
            s.execute(text(f"CREATE TABLE {tbl} (id bigserial PRIMARY KEY, "
                           f"register_time timestamptz, remark text)"))
            _reg(s, tbl, code)
            s.commit()

            df = pd.DataFrame([
                {"登记时间": "2099-12-31 10:00:00", "备注": "越界"},
                {"登记时间": "2026-01-05 10:00:00", "备注": "正常"},
            ])
            mapping = {"register_time": "登记时间", "remark": "备注"}
            n = _persist_date_suspects(s, code, df, mapping)
            s.commit()
            assert n == 1, f"应只写 1 条留痕(越界那条), 实际 {n}"

            rows = s.query(IngestRejectedRow).filter(IngestRejectedRow.id > base).all()
            assert len(rows) == 1
            r = rows[0]
            assert r.reject_category == DR.CATEGORY_DATE_IMPOSSIBLE
            assert r.status == "open" and r.data_type == code
            assert "2099-12-31" in (r.reject_reason or "")
            assert r.raw_row == {"登记时间": "2099-12-31 10:00:00", "备注": "越界"}, \
                "raw_row 必须保留原始行（人工核对要用）"
            assert r.source_row_no == 2
            # 留痕**不改 df**
            assert df.iloc[0]["登记时间"] == "2099-12-31 10:00:00"
        finally:
            s.rollback()
            s.execute(text("DELETE FROM ingest_rejected_rows WHERE id > :b"), {"b": base})
            s.execute(text("DELETE FROM data_sources WHERE code = :c"), {"c": code})
            s.execute(text(f"DROP TABLE IF EXISTS {tbl}"))
            s.commit()


def test_import_date_suspect_skips_sanitizable_year(db):
    """`_sanitize_date` 能自动纠错的年份（2225 → 2025）不留痕 —— 入库后已是正常值。"""
    import pandas as pd

    from app.models.ingest_rejected_row import IngestRejectedRow
    from app.services.upsert import _persist_date_suspects

    tbl, code = _uniq("gov_impd2"), _uniq("ds_impd2")
    _mk(db, tbl, "id bigserial PRIMARY KEY, register_time timestamptz", "", code)

    df = pd.DataFrame([{"登记时间": "2225-01-05 10:00:00"}])
    assert _persist_date_suspects(db, code, df, {"register_time": "登记时间"}) == 0
    assert db.query(IngestRejectedRow).filter(
        IngestRejectedRow.data_type == code).count() == 0


def test_import_date_suspect_skips_planned_column(db):
    """计划类列（end_date）即使在未来也不留痕（与扫描期同一白名单）。"""
    import pandas as pd

    from app.models.ingest_rejected_row import IngestRejectedRow
    from app.services.upsert import _persist_date_suspects

    tbl, code = _uniq("gov_impd3"), _uniq("ds_impd3")
    _mk(db, tbl, "id bigserial PRIMARY KEY, end_date timestamptz", "", code)

    df = pd.DataFrame([{"结束日期": "2099-12-31 00:00:00"}])
    assert _persist_date_suspects(db, code, df, {"end_date": "结束日期"}) == 0
    assert db.query(IngestRejectedRow).filter(
        IngestRejectedRow.data_type == code).count() == 0


def test_import_date_suspect_unknown_data_type_is_noop(db):
    """未登记 data_sources 的类型 → 取不到日期列 → 静默跳过（不猜列类型）。"""
    import pandas as pd

    from app.services.upsert import _persist_date_suspects

    assert _persist_date_suspects(db, _uniq("ds_unknown"), pd.DataFrame([{"a": 1}]), {}) == 0

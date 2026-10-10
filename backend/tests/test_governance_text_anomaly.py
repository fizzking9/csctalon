# -*- coding: utf-8 -*-
"""D4 文本异常探测器 + 导入期留痕测试（工单 #168）

对应设计 §3.1/§四 与工单验收：
  1. D4 正/反用例：含换行报出 / 白名单列不报 / 贴列宽上限报出 / 正常长度不报
  2. 导入期留痕：截断/换行各写一条 `IngestRejectedRow`，且**入库数据不变**
  3. **判据同源**：导入期 `kinds_of`（Python）与扫描期 `sql_predicates`（SQL）
     对同一份入库后的数据结论一致（这是本工单最关键的断言）

临时表建在 public（探测器只扫 public 的 data_sources 登记表）——
D4 的扫描范围是 `data_sources` 登记表，故测试需注册一行 data_sources。
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.data_source import DataSource
from app.services.governance_detector import DETECTOR_TEXT_ANOMALY, scan_text_anomaly
from app.services.text_anomaly_rules import (
    KIND_NEAR_LIMIT,
    KIND_NEWLINE,
    NEAR_LIMIT_RATIO,
    is_newline_allowed,
    kinds_of,
    near_limit_threshold,
    sql_predicates,
)

_TZ = _dt.timezone(_dt.timedelta(hours=8))


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _reg(db: Session, table: str, code: str) -> None:
    db.add(DataSource(code=code, table_name=table, name_cn=f"测试-{code}",
                      source_type="business"))
    db.flush()


# ─────────────────────────────────────────────────────────────────────
# 一、判据同源（纯函数级）
# ─────────────────────────────────────────────────────────────────────
class TestRulesSameSource:
    def test_threshold_is_integer_and_shared(self):
        """阈值必须是整数且 Python/SQL 用同一个数（否则边界不一致）。"""
        assert near_limit_threshold(512) == int(512 * NEAR_LIMIT_RATIO) == 486
        assert near_limit_threshold(200) == 190

    def test_kinds_newline(self):
        assert kinds_of("a\nb", 512, column="remark") == [KIND_NEWLINE]
        assert kinds_of("a b", 512, column="remark") == []

    def test_kinds_near_limit_boundary(self):
        n = 512
        assert kinds_of("x" * (near_limit_threshold(n) - 1), n, column="c") == []
        assert kinds_of("x" * near_limit_threshold(n), n, column="c") == [KIND_NEAR_LIMIT]

    def test_whitelist_column_skips_newline(self):
        assert is_newline_allowed("problem") is True
        assert kinds_of("多行\n投诉", 512, column="problem") == []
        # 白名单只对换行生效；贴上限仍要报
        assert kinds_of("x" * 486, 512, column="problem") == [KIND_NEAR_LIMIT]

    def test_text_column_never_near_limit(self):
        assert kinds_of("x" * 10000, None, column="remark") == []

    def test_sql_predicates_render_same_rules(self):
        """SQL 侧渲染的判据集合 = Python 侧的规则集合（同一白名单 + 同一阈值常量）。

        ⚠️ 注意语义：`sql_predicates` 给的是**可用判据**（用于扫描），
        不是某个值的命中结果 —— 后者由 `test_scan_and_import_judgement_agree` 断言。
        """
        # 非白名单列 + 有列宽 → 两条判据
        assert [k for k, _ in sql_predicates('"remark"', 512)] == [KIND_NEWLINE, KIND_NEAR_LIMIT]
        # 白名单列 → 只渲染贴上限（换行判据不渲染）
        assert [k for k, _ in sql_predicates('"problem"', 512)] == [KIND_NEAR_LIMIT]
        # Text 列（无列宽）→ 只渲染换行
        assert [k for k, _ in sql_predicates('"remark"', None)] == [KIND_NEWLINE]
        # 白名单列 + Text → 无判据（不报）
        assert sql_predicates('"problem"', None) == []
        # 阈值取同一整数常量（Python/SQL 边界必须一致）
        assert "486" in sql_predicates('"remark"', 512)[1][1]


# ─────────────────────────────────────────────────────────────────────
# 二、D4 探测器（正/反用例）
# ─────────────────────────────────────────────────────────────────────
def test_d4_reports_newline_column(db):
    tbl, code = _uniq("gov_d4_nl"), _uniq("ds_d4")
    db.execute(text(f"CREATE TABLE {tbl} (id bigserial PRIMARY KEY, remark text, ok_col text)"))
    _reg(db, tbl, code)
    db.execute(text(
        f"INSERT INTO {tbl} (remark, ok_col) VALUES ('第一行\n第二行', '正常'), ('无换行', '正常')"
    ))
    db.flush()

    findings = scan_text_anomaly(db, tables=[tbl])
    assert len(findings) == 1, f"应只报含换行的 remark 列, 实际 {[f['target'] for f in findings]}"
    f = findings[0]
    assert f["detector"] == DETECTOR_TEXT_ANOMALY
    assert f["target"] == f"{tbl}.remark"
    assert f["metrics"]["kinds"] == [KIND_NEWLINE]
    assert f["metrics"]["hit_rows"] == 1
    assert f["metrics"]["column"] == "remark"
    # 样本把换行渲染成 ⏎（人能看出问题在哪）+ 可定位行 id
    assert "⏎" in f["samples"][0]["value"]
    assert f["samples"][0]["_id"] is not None
    assert "chr(10)" in f["repro_sql"]


def test_d4_whitelist_column_not_reported(db):
    """白名单列（problem：投诉原文多行是业务真实形态）不报换行。"""
    tbl, code = _uniq("gov_d4_wl"), _uniq("ds_d4w")
    db.execute(text(f"CREATE TABLE {tbl} (id bigserial PRIMARY KEY, problem text)"))
    _reg(db, tbl, code)
    db.execute(text(f"INSERT INTO {tbl} (problem) VALUES ('投诉内容\n第二行')"))
    db.flush()

    assert scan_text_anomaly(db, tables=[tbl]) == [], "白名单列不应报换行"


def test_d4_reports_near_limit_column(db):
    """varchar(n) 且长度贴上限 → 报（截断嫌疑）。"""
    tbl, code = _uniq("gov_d4_lim"), _uniq("ds_d4l")
    db.execute(text(f"CREATE TABLE {tbl} (id bigserial PRIMARY KEY, solution varchar(512))"))
    _reg(db, tbl, code)
    long_val = "x" * 500          # >= 486（0.95*512）
    db.execute(text(f"INSERT INTO {tbl} (solution) VALUES (:v)"), {"v": long_val})
    db.execute(text(f"INSERT INTO {tbl} (solution) VALUES ('短值')"))
    db.flush()

    findings = scan_text_anomaly(db, tables=[tbl])
    assert len(findings) == 1
    f = findings[0]
    assert f["metrics"]["kinds"] == [KIND_NEAR_LIMIT]
    assert f["metrics"]["col_max_len"] == 512
    assert f["samples"][0]["_length"] == 500
    assert "length" in f["repro_sql"] and "486" in f["repro_sql"]


def test_d4_no_finding_for_clean_table(db):
    """全正常 → 不报（防误报）。"""
    tbl, code = _uniq("gov_d4_ok"), _uniq("ds_d4o")
    db.execute(text(f"CREATE TABLE {tbl} (id bigserial PRIMARY KEY, remark text, "
                    f"solution varchar(512))"))
    _reg(db, tbl, code)
    db.execute(text(f"INSERT INTO {tbl} (remark, solution) VALUES ('正常备注', '正常方案')"))
    db.flush()

    assert scan_text_anomaly(db, tables=[tbl]) == []


def test_d4_short_varchar_not_scanned(db):
    """短 varchar（<200）不判贴上限 —— 短列贴满多属正常录入，无「截断嫌疑」语义。"""
    tbl, code = _uniq("gov_d4_sh"), _uniq("ds_d4s")
    db.execute(text(f"CREATE TABLE {tbl} (id bigserial PRIMARY KEY, device_type varchar(64))"))
    _reg(db, tbl, code)
    db.execute(text(f"INSERT INTO {tbl} (device_type) VALUES (:v)"), {"v": "x" * 64})
    db.flush()

    assert scan_text_anomaly(db, tables=[tbl]) == []


# ─────────────────────────────────────────────────────────────────────
# 三、判据同源（同一份入库后的数据，Python 与 SQL 结论一致）
# ─────────────────────────────────────────────────────────────────────
def test_scan_and_import_judgement_agree(db):
    """★核心断言：库里已有的值，`kinds_of` 与 `sql_predicates` 结论逐值一致。

    构造：一列 varchar(512)，值覆盖 边界-1 / 边界 / 边界+1 / 含换行 / 空。
    期望：Python 判据集合 == SQL 判据集合（同一阈值常量、同一白名单）。
    """
    tbl, code = _uniq("gov_d4_same"), _uniq("ds_d4same")
    db.execute(text(f"CREATE TABLE {tbl} (id bigserial PRIMARY KEY, solution varchar(512))"))
    _reg(db, tbl, code)
    thr = near_limit_threshold(512)      # 486
    values = ["x" * (thr - 1), "x" * thr, "x" * (thr + 1), "第一行\n第二行", "正常"]
    for v in values:
        db.execute(text(f"INSERT INTO {tbl} (solution) VALUES (:v)"), {"v": v})
    db.flush()

    # SQL 侧：逐判据数命中行
    sql_hits: dict[str, int] = {}
    for kind, cond in sql_predicates('"solution"', 512):
        n = db.execute(text(f'SELECT count(*) FROM "{tbl}" WHERE {cond}')).scalar()
        sql_hits[kind] = int(n)
    # Python 侧：同 5 个值逐个判
    py_hits: dict[str, int] = {}
    for v in values:
        for k in kinds_of(v, 512, column="solution"):
            py_hits[k] = py_hits.get(k, 0) + 1

    assert sql_hits == py_hits, f"判据不同源: SQL={sql_hits} Python={py_hits}"
    assert sql_hits == {KIND_NEAR_LIMIT: 2, KIND_NEWLINE: 1}


# ─────────────────────────────────────────────────────────────────────
# 四、导入期留痕（只看不改）
# ─────────────────────────────────────────────────────────────────────
def test_import_suspect_leaves_data_untouched(_engine):
    """导入期检测写入留痕，但**入库数据一字不改**（截断/换行两类各一条）。"""
    import pandas as pd

    from app.models.ingest_rejected_row import IngestRejectedRow
    from app.services.text_anomaly_rules import CATEGORY_NEWLINE, CATEGORY_TRUNCATE
    from app.services.upsert import _persist_text_suspects

    tbl, code = _uniq("gov_imp"), _uniq("ds_imp")
    with Session(_engine) as s:
        base = s.execute(text("SELECT coalesce(max(id),0) FROM ingest_rejected_rows")).scalar()
        try:
            s.execute(text(f"CREATE TABLE {tbl} (id bigserial PRIMARY KEY, remark text, "
                           f"solution varchar(512))"))
            _reg(s, tbl, code)
            s.commit()

            df = pd.DataFrame([
                {"备注": "第一行\n第二行", "方案": "x" * 600},   # 换行 + 超长（会被截断）
                {"备注": "正常", "方案": "正常方案"},
            ])
            mapping = {"remark": "备注", "solution": "方案"}
            n = _persist_text_suspects(s, code, df, mapping)
            s.commit()
            assert n == 2, f"应写 2 条留痕(换行+截断), 实际 {n}"

            rows = s.query(IngestRejectedRow).filter(IngestRejectedRow.id > base).all()
            cats = sorted(r.reject_category for r in rows)
            assert cats == [CATEGORY_NEWLINE, CATEGORY_TRUNCATE]
            for r in rows:
                assert r.status == "open" and r.data_type == code
                assert r.raw_row, "留痕必须带原始行（人工判定要看原值）"
                assert r.source_row_no is not None
            # 留痕**不改数据**：df 原值未被改动
            assert len(df.iloc[0]["方案"]) == 600
            assert "\n" in df.iloc[0]["备注"]
        finally:
            s.rollback()
            s.execute(text("DELETE FROM ingest_rejected_rows WHERE id > :b"), {"b": base})
            s.execute(text("DELETE FROM data_sources WHERE code = :c"), {"c": code})
            s.execute(text(f"DROP TABLE IF EXISTS {tbl}"))
            s.commit()


def test_run_upsert_full_path_writes_suspect_and_keeps_data(_engine, monkeypatch):
    """★全链路：`run_upsert`（导入真实入口）→ 留痕写入 + **入库数据与源值一致**。

    用 monkeypatch 往 `UPSERT_DISPATCH` 注入一个 fixture 类型（写 fixture 表），
    走真实的 run_upsert 路径（检测钩子的位置、上限、事务顺序都被覆盖）。
    """
    import pandas as pd
    from sqlalchemy import select

    from app.models.ingest_rejected_row import IngestRejectedRow
    from app.services import upsert as U

    tbl, code = _uniq("gov_full"), _uniq("ds_full")
    with Session(_engine) as s:
        base = s.execute(text("SELECT coalesce(max(id),0) FROM ingest_rejected_rows")).scalar()
        try:
            s.execute(text(f"CREATE TABLE {tbl} (id bigserial PRIMARY KEY, remark text, "
                           f"solution varchar(512))"))
            _reg(s, tbl, code)
            s.commit()

            def _fake_upsert(df, mapping, db, result):
                """模拟真实 upsert：超长值经 `_truncate_str`（既有行为），文本原样写。"""
                for rec in df.to_dict("records"):
                    db.execute(text(f"INSERT INTO {tbl} (remark, solution) VALUES (:r, :s)"),
                               {"r": rec.get("备注"),
                                "s": U._truncate_str(rec.get("方案"), 512)})
                    result.inserted += 1
                result.total += len(df)

            monkeypatch.setitem(U.UPSERT_DISPATCH, code, _fake_upsert)
            df = pd.DataFrame([
                {"备注": "第一行\n第二行", "方案": "x" * 600},
                {"备注": "正常", "方案": "正常方案"},
            ])
            res = U.run_upsert(df, code, {"remark": "备注", "solution": "方案"}, s)

            assert res.inserted == 2
            assert res.text_suspects == 2, "换行 + 截断各应留一条痕"
            cats = sorted(r.reject_category for r in s.scalars(
                select(IngestRejectedRow).where(IngestRejectedRow.id > base)))
            from app.services.text_anomaly_rules import CATEGORY_NEWLINE, CATEGORY_TRUNCATE
            assert cats == [CATEGORY_NEWLINE, CATEGORY_TRUNCATE]
            # 留痕里能看到**原始长度**（600），而库里是截断后的 512 —— 这正是要「可见」的东西
            trunc = [r for r in s.scalars(
                select(IngestRejectedRow).where(IngestRejectedRow.id > base))
                if r.reject_category == CATEGORY_TRUNCATE][0]
            assert "600" in (trunc.reject_reason or "")

            # ★ 检测本身不改数据：文本列与源值逐字节一致；超长列是**既有**截断行为（非本检测所致）
            rows = s.execute(text(f"SELECT remark, solution FROM {tbl} ORDER BY id")).all()
            assert rows[0][0] == "第一行\n第二行", "文本列必须与源值一致（检测不改数据）"
            assert len(rows[0][1]) == 512, "超长列按既有 _truncate_str 行为截断"
            assert rows[1][0] == "正常" and rows[1][1] == "正常方案"
        finally:
            s.rollback()
            s.execute(text("DELETE FROM ingest_rejected_rows WHERE id > :b"), {"b": base})
            s.execute(text("DELETE FROM data_sources WHERE code = :c"), {"c": code})
            s.execute(text(f"DROP TABLE IF EXISTS {tbl}"))
            s.commit()


def test_import_suspect_respects_limit_and_whitelist(db):
    """上限生效（防噪音刷爆）；白名单列不产生留痕。"""
    import pandas as pd

    from app.services.text_anomaly_rules import CATEGORY_NEWLINE
    from app.services.upsert import _persist_text_suspects

    tbl, code = _uniq("gov_imp2"), _uniq("ds_imp2")
    db.execute(text(f"CREATE TABLE {tbl} (id bigserial PRIMARY KEY, remark text, problem text)"))
    _reg(db, tbl, code)
    db.flush()

    df = pd.DataFrame([{"备注": f"第{i}行\n第二行", "问题": "投诉\n多行"} for i in range(5)])
    mapping = {"remark": "备注", "problem": "问题"}
    n = _persist_text_suspects(db, code, df, mapping, limit=2)
    assert n == 2, "上限 2 应只写 2 条"
    # 白名单列 problem 不产留痕
    from app.models.ingest_rejected_row import IngestRejectedRow
    rows = db.query(IngestRejectedRow).filter(IngestRejectedRow.data_type == code).all()
    assert all(r.reject_category == CATEGORY_NEWLINE for r in rows)
    assert all("remark" in (r.reject_reason or "") for r in rows)

# -*- coding: utf-8 -*-
"""治理框架 D2/D3 探测器 + 体检编排测试 (工单 #163)

覆盖 `governance_detector.scan_row_count_jump` / `scan_fk_orphans` 及
`governance_scan_service.run_full_scan`（编排 + findings 落库 + system_events 留痕）：

- D2 正/反例：本期 vs 上期窗口行数增幅/跳水超阈值 → 检出；平稳波动 → 不检出。
- D3 正/反例：引用列非空但父表无对应主键 → 检出孤儿；无孤儿 → 不检出。
- 编排：run_full_scan 落 findings + system_events(category=ai_governance)。

临时表 + FK + data_sources 注册均在事务内（db fixture 结束回滚），不污染测试库。
D2 时间依赖 `_now_cn()`，用 monkeypatch 注入固定北京时间。
"""

import datetime as _dt
import uuid

import pytest
from sqlalchemy import text

from app.models.data_source import DataSource
from app.services.governance_detector import (
    DETECTOR_FK_ORPHAN,
    DETECTOR_ROW_COUNT_JUMP,
    scan_fk_orphans,
    scan_row_count_jump,
)
from app.services.governance_scan_service import run_full_scan

_TZ = _dt.timezone(_dt.timedelta(hours=8))
# 注入的固定"当前"北京时间（D2 窗口基准）
FIXED_NOW = _dt.datetime(2026, 9, 20, 2, 30, tzinfo=_TZ)


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _reg_datasource(db, table_name: str, time_field: str, code: str):
    """注册一条 data_sources（D2 时间列权威来源）。source_type 用 business(非 system,非 placeholder)。"""
    db.add(DataSource(code=code, table_name=table_name, name_cn=f"测试-{code}",
                      source_type="business", time_field=time_field))
    db.flush()


# ─────────────────────────────────────────────────────────────
# D2 行数突变
# ─────────────────────────────────────────────────────────────
@pytest.fixture()
def patch_now(monkeypatch):
    """把 governance_detector._now_cn 注入固定时刻。"""
    from app.services import governance_detector as gd

    monkeypatch.setattr(gd, "_now_cn", lambda: FIXED_NOW)


def test_d2_detects_growth(db, patch_now):
    """本期行数是上期 10 倍以上 → 检出 growth。"""
    tbl, code = _uniq("gov_d2_grow"), _uniq("ds")
    db.execute(text(
        f"CREATE TABLE {tbl} (id bigserial PRIMARY KEY, ts timestamptz)"
    ))
    _reg_datasource(db, tbl, "ts", code)
    # 上期 09-07..09-13 每天 1 行 = 7；本期 09-14..09-20 每天 14 行 = 98 → ratio 14
    for day in range(7, 14):
        db.execute(text(f"INSERT INTO {tbl} (ts) VALUES (:t)"),
                   {"t": _dt.datetime(2026, 9, day, 12, 0, tzinfo=_dt.timezone.utc)})
    for day in range(14, 21):
        for _ in range(14):
            db.execute(text(f"INSERT INTO {tbl} (ts) VALUES (:t)"),
                       {"t": _dt.datetime(2026, 9, day, 12, 0, tzinfo=_dt.timezone.utc)})
    db.flush()

    findings = scan_row_count_jump(db, tables=[tbl], window_days=7)
    assert len(findings) == 1, f"D2 应检出 1 个增长, 实际 {len(findings)}"
    f = findings[0]
    assert f["detector"] == DETECTOR_ROW_COUNT_JUMP
    assert f["metrics"]["gap_kind"] == "growth"
    assert f["metrics"]["cur_rows"] == 98 and f["metrics"]["prev_rows"] == 7
    assert f["metrics"]["growth_ratio"] >= 10.0
    assert "curve" in f["metrics"]  # 按日曲线
    assert len(f["samples"]) == 2
    # 严重度：比值 14 倍但绝对变化量 91 < 100 → 仅 warn（严重度需绝对量达标）
    assert f["severity_raw"] == "warn"


def test_d2_low_volume_noise_skipped(db, patch_now):
    """绝对量过小（个位数波动）不检出 —— 生产首次体检的噪音回归。

    实证：`card_complaints` 2→6（3 倍）、`device_tags` 1→15（15 倍）被纯比值判据报成
    high，人工核验均为噪音（低频/配置表，绝对量小、比值天然剧烈）。
    本用例锁定：`max(cur,prev) < _D2_MIN_ABS_ROWS(20)` 一律跳过。
    """
    tbl, code = _uniq("gov_d2_noise"), _uniq("ds")
    db.execute(text(f"CREATE TABLE {tbl} (id bigserial PRIMARY KEY, ts timestamptz)"))
    _reg_datasource(db, tbl, "ts", code)
    # 上期 1 行、本期 15 行（15 倍，但绝对量 < 20）
    db.execute(text(f"INSERT INTO {tbl} (ts) VALUES (:t)"),
               {"t": _dt.datetime(2026, 9, 10, 12, 0, tzinfo=_dt.timezone.utc)})
    for _ in range(15):
        db.execute(text(f"INSERT INTO {tbl} (ts) VALUES (:t)"),
                   {"t": _dt.datetime(2026, 9, 16, 12, 0, tzinfo=_dt.timezone.utc)})
    db.flush()

    findings = scan_row_count_jump(db, tables=[tbl], window_days=7)
    assert findings == [], f"个位数波动(1→15)应被绝对量下限挡掉, 实际 {len(findings)}"


def test_d2_high_severity_requires_abs_delta(db, patch_now):
    """比值翻倍但绝对变化量 < 100 → 只报 warn，不报 high（防严重度虚高）。"""
    tbl, code = _uniq("gov_d2_sev"), _uniq("ds")
    db.execute(text(f"CREATE TABLE {tbl} (id bigserial PRIMARY KEY, ts timestamptz)"))
    _reg_datasource(db, tbl, "ts", code)
    # 上期 25 行、本期 75 行 → ratio 3.0（>=2），但绝对变化量 50 < 100
    for _ in range(25):
        db.execute(text(f"INSERT INTO {tbl} (ts) VALUES (:t)"),
                   {"t": _dt.datetime(2026, 9, 10, 12, 0, tzinfo=_dt.timezone.utc)})
    for _ in range(75):
        db.execute(text(f"INSERT INTO {tbl} (ts) VALUES (:t)"),
                   {"t": _dt.datetime(2026, 9, 16, 12, 0, tzinfo=_dt.timezone.utc)})
    db.flush()

    findings = scan_row_count_jump(db, tables=[tbl], window_days=7)
    assert len(findings) == 1
    assert findings[0]["metrics"]["growth_ratio"] == 3.0
    assert findings[0]["severity_raw"] == "warn", "绝对变化量 50 < 100 不应报 high"


def test_d2_no_find_on_flat(db, patch_now):
    """本期/上期接近(1.1 倍, <1.5) → 不检出（正常波动）。

    窗口(注入 FIXED_NOW=09-20): 本期 09-14..09-20, 上期 09-07..09-13（互不重叠）。
    """
    tbl, code = _uniq("gov_d2_flat"), _uniq("ds")
    db.execute(text(f"CREATE TABLE {tbl} (id bigserial PRIMARY KEY, ts timestamptz)"))
    _reg_datasource(db, tbl, "ts", code)
    # 上期: 09-07..09-13 每天 10 行 = 70
    for day in range(7, 14):
        for _ in range(10):
            db.execute(text(f"INSERT INTO {tbl} (ts) VALUES (:t)"),
                       {"t": _dt.datetime(2026, 9, day, 12, 0, tzinfo=_dt.timezone.utc)})
    # 本期: 09-14..09-20 每天 11 行 = 77 → ratio 1.1 < 1.5，属正常波动
    for day in range(14, 21):
        for _ in range(11):
            db.execute(text(f"INSERT INTO {tbl} (ts) VALUES (:t)"),
                       {"t": _dt.datetime(2026, 9, day, 12, 0, tzinfo=_dt.timezone.utc)})
    db.flush()

    findings = scan_row_count_jump(db, tables=[tbl], window_days=7)
    assert findings == [], f"平稳波动(1.1 倍)不应检出, 实际 {len(findings)}"


def test_d2_detects_drop(db, patch_now):
    """本期只有上期一半以下 → 检出 drop（防整批数据丢失）。"""
    tbl, code = _uniq("gov_d2_drop"), _uniq("ds")
    db.execute(text(f"CREATE TABLE {tbl} (id bigserial PRIMARY KEY, ts timestamptz)"))
    _reg_datasource(db, tbl, "ts", code)
    # 上期 09-07..09-13 每天 14 行 = 98；本期 09-14..09-20 每天 1 行 = 7 → ratio 0.07
    for day in range(7, 14):
        for _ in range(14):
            db.execute(text(f"INSERT INTO {tbl} (ts) VALUES (:t)"),
                       {"t": _dt.datetime(2026, 9, day, 12, 0, tzinfo=_dt.timezone.utc)})
    for day in range(14, 21):
        db.execute(text(f"INSERT INTO {tbl} (ts) VALUES (:t)"),
                   {"t": _dt.datetime(2026, 9, day, 12, 0, tzinfo=_dt.timezone.utc)})
    db.flush()

    findings = scan_row_count_jump(db, tables=[tbl], window_days=7)
    assert len(findings) == 1
    assert findings[0]["metrics"]["gap_kind"] == "drop"
    assert findings[0]["metrics"]["cur_rows"] == 7
    assert findings[0]["metrics"]["prev_rows"] == 98


# ─────────────────────────────────────────────────────────────
# D3 外键孤儿
# ─────────────────────────────────────────────────────────────
def test_d3_detects_fk_orphans(db):
    """`NOT VALID` 外键 + 存量孤儿 → 检出（gap_kind=orphan）。

    ⚠️ 为什么必须用 `NOT VALID`：PG **强制**校验已生效的 FK，`convalidated=true` 的约束上
    孤儿恒为 0（DEFERRABLE 也只延到 commit 时校验）。孤儿真实存在的前提是
    「建约束时 NOT VALID 不校验存量数据」—— 这正是 D3 要抓的场景（生产 100 个 FK 全 validated）。

    场景取自真实库的 `bms_orders.device_sn → devices.sn`（引用列名含 sn → 应脱敏）。
    """
    parent, child = _uniq("gov_d3_dev"), _uniq("gov_d3_ord")
    db.execute(text(f"CREATE TABLE {parent} (sn text PRIMARY KEY)"))
    db.execute(text(f"CREATE TABLE {child} (id bigserial PRIMARY KEY, device_sn text)"))
    db.execute(text(f"INSERT INTO {parent} (sn) VALUES ('SN-OK')"))
    # 1 匹配 + 2 孤儿
    db.execute(text(f"INSERT INTO {child} (device_sn) VALUES ('SN-OK'), ('SN-X'), ('SN-Y')"))
    # NOT VALID：不校验存量数据 → 孤儿得以保留
    db.execute(text(f"ALTER TABLE {child} ADD CONSTRAINT {child}_fk "
                    f"FOREIGN KEY (device_sn) REFERENCES {parent}(sn) NOT VALID"))
    db.flush()

    findings = scan_fk_orphans(db, child_tables=[child])
    assert len(findings) == 1, f"D3 应检出 1 个 finding, 实际 {len(findings)}"
    f = findings[0]
    assert f["detector"] == DETECTOR_FK_ORPHAN
    assert f["table_name"] == child
    assert f["metrics"]["orphan_count"] == 2
    assert f["metrics"]["gap_kind"] == "orphan"
    assert f["metrics"]["fk_validated"] is False
    assert f["metrics"]["parent_table"] == parent
    assert f["metrics"]["ref_cols"] == ["device_sn"]
    assert len(f["samples"]) == 2
    # 样本脱敏：device_sn 含 sn → 哈希；行 id 保留（供人工定位）
    s = f["samples"][0]
    assert str(s.get("device_sn")).startswith("#"), "敏感列(device_sn)应脱敏"
    assert s.get("_id") is not None


def test_d3_reports_unvalidated_constraint_without_orphans(db):
    """`NOT VALID` 但暂无孤儿 → 仍报（gap_kind=unvalidated_constraint）。

    这是「约束看似存在、实际不拦新增」的保护缺口 —— 与 D1 的保护缺口同类。
    """
    parent, child = _uniq("gov_d3_up"), _uniq("gov_d3_uc")
    db.execute(text(f"CREATE TABLE {parent} (id bigint PRIMARY KEY)"))
    db.execute(text(f"CREATE TABLE {child} (id bigserial PRIMARY KEY, parent_id bigint)"))
    db.execute(text(f"INSERT INTO {parent} (id) VALUES (1)"))
    db.execute(text(f"INSERT INTO {child} (parent_id) VALUES (1)"))  # 无孤儿
    db.execute(text(f"ALTER TABLE {child} ADD CONSTRAINT {child}_fk "
                    f"FOREIGN KEY (parent_id) REFERENCES {parent}(id) NOT VALID"))
    db.flush()

    findings = scan_fk_orphans(db, child_tables=[child])
    assert len(findings) == 1, "NOT VALID 约束应被报出(约束未生效)"
    assert findings[0]["metrics"]["gap_kind"] == "unvalidated_constraint"
    assert findings[0]["metrics"]["orphan_count"] == 0
    assert findings[0]["metrics"]["fk_validated"] is False


def test_d3_no_orphans(db):
    """已生效 FK + 全部引用可匹配 → 不检出（健康）。"""
    parent, child = _uniq("gov_d3_nop"), _uniq("gov_d3_noc")
    db.execute(text(f"CREATE TABLE {parent} (id bigint PRIMARY KEY)"))
    db.execute(text(f"CREATE TABLE {child} (id bigint, "
                    f"CONSTRAINT {child}_fk FOREIGN KEY (id) REFERENCES {parent}(id))"))
    db.execute(text(f"INSERT INTO {parent} (id) SELECT g FROM generate_series(1, 5) AS g"))
    db.execute(text(f"INSERT INTO {child} (id) SELECT g FROM generate_series(1, 5) AS g"))
    db.flush()

    findings = scan_fk_orphans(db, child_tables=[child])
    assert findings == [], f"无孤儿且约束已生效不应检出, 实际 {len(findings)}"


# ─────────────────────────────────────────────────────────────
# 体检编排（run_full_scan）
# ─────────────────────────────────────────────────────────────
def test_run_full_scan_persists_and_audits(_engine):
    """run_full_scan：探测 → 落 findings → system_events 留痕。

    用独立 session + 手工清理（run_full_scan 内部会 commit，不能用 db fixture 的事务）。
    清理用「基线 max(id)」而非按表名删 —— 编排会扫全库，可能连带落其它 finding。
    """
    from sqlalchemy.orm import Session

    parent, child = _uniq("gov_fs_p"), _uniq("gov_fs_c")
    with Session(_engine) as s:
        base_find = s.execute(text("SELECT coalesce(max(id),0) FROM governance_findings")).scalar()
        base_ev = s.execute(text("SELECT coalesce(max(id),0) FROM system_events")).scalar()
        try:
            # D3 场景：NOT VALID 外键 + 存量孤儿，保证编排里至少发现 1 条
            s.execute(text(f"CREATE TABLE {parent} (id bigint PRIMARY KEY)"))
            s.execute(text(f"CREATE TABLE {child} (id bigserial PRIMARY KEY, parent_id bigint)"))
            s.execute(text(f"INSERT INTO {parent} (id) VALUES (1)"))
            s.execute(text(f"INSERT INTO {child} (parent_id) VALUES (1), (999), (1000)"))
            s.execute(text(f"ALTER TABLE {child} ADD CONSTRAINT {child}_fk "
                           f"FOREIGN KEY (parent_id) REFERENCES {parent}(id) NOT VALID"))
            s.commit()

            result = run_full_scan(s)
            assert result["status"] == "success"
            assert result["found"] >= 1, "应至少发现孤儿(999/1000)"
            assert "row_count_jump" in result["by_detector"] or "fk_orphan" in result["by_detector"]

            # findings 已落库（本次新增）
            n_find = s.execute(text(
                "SELECT count(*) FROM governance_findings WHERE id > :b AND status='open'"
            ), {"b": base_find}).scalar()
            assert n_find >= 1, "findings 未落库"

            # 孤儿 finding 的具体证据齐备
            row = s.execute(text(
                "SELECT metrics, samples, repro_sql FROM governance_findings "
                "WHERE table_name = :t AND detector = 'fk_orphan'"
            ), {"t": child}).mappings().one_or_none()
            assert row is not None, "孤儿 finding 未落库"
            assert row["metrics"]["orphan_count"] == 2
            assert row["samples"], "样本不应为空"
            assert "NOT EXISTS" in row["repro_sql"]

            # system_events 留痕
            ev = s.execute(text(
                "SELECT category, action, status FROM system_events "
                "WHERE id > :b AND category='ai_governance' AND action='governance_scan' "
                "ORDER BY id DESC LIMIT 1"
            ), {"b": base_ev}).mappings().one_or_none()
            assert ev, "system_events 未留痕"
            assert ev["category"] == "ai_governance"
            assert ev["action"] == "governance_scan"
            assert ev["status"] == "success"
        finally:
            # 先 rollback 清掉可能的中止事务，再按基线清理本次产生的行
            s.rollback()
            s.execute(text("DELETE FROM governance_findings WHERE id > :b"), {"b": base_find})
            s.execute(text("DELETE FROM system_events WHERE id > :b"), {"b": base_ev})
            s.execute(text(f"DROP TABLE IF EXISTS {child}"))
            s.execute(text(f"DROP TABLE IF EXISTS {parent}"))
            s.commit()

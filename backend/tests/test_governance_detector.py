# -*- coding: utf-8 -*-
"""治理框架 D1 探测器测试 (工单 #162)

设计: docs/design/AI数据治理框架-20260919.md §四

覆盖 `governance_detector` 的关键判据（模块文件头「两条铁律」）：

1. **不误报**：复现索引真实表达式（`date()` / `COALESCE` / `WHERE` 部分条件）
   —— #161 误报场景回归。误报根源：没复现 `WHERE order_no IS NOT NULL`，
   把索引**不覆盖**的行也纳入分组。
2. **能检出**：可空列（device_sn）为 NULL 的重复行，唯一索引拦不住 → 应产出 finding
   —— #160 场景回归。
3. **排除「整组全 NULL」组**：索引列全为 NULL 的多行属业务允许，不报重复（铁律②）；
   但同索引下「部分列有值」的重复仍须检出。
4. **persist 落库 + 幂等**：落 finding 后重复扫描不堆叠（同 #166 教训）。

⚠️ 构造重复的关键前提：**唯一索引下只有「含 NULL 列」的组才可能存在重复**
（非 NULL 值相等会被索引直接拒绝）。故所有造重复的用例都在索引列里留一个 NULL 列。

临时表与表达式索引均在事务内创建（PG DDL 可回滚），fixture 结束自动撤销。
"""

import datetime as _dt
import uuid

from sqlalchemy import text

from app.models.governance_finding import FINDING_STATUS_OPEN
from app.services.governance_detector import (
    GAP_PROTECTION_GAP,
    run_d1_scan,
    scan_unique_index_dups,
)


def _uniq(prefix: str) -> str:
    """唯一表名（避免并发/残留冲突）。"""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ─────────────────────────────────────────────────────────────
# 铁律①：复现索引真实表达式（含 WHERE 部分条件）→ 不误报（#161 回归）
# ─────────────────────────────────────────────────────────────
def test_d1_no_false_positive_on_partial_expr_index(db):
    """表达式 + 部分条件索引不误报（#161 误报根因回归）。

    索引与 `manual_refunds.uq_manual_refunds_dedup` 同构：
      ON ((register_time AT TIME ZONE)::date, order_no,
          COALESCE(device_sn,''), COALESCE(registrant,''))
      WHERE order_no IS NOT NULL

    构造：2 行 `order_no IS NULL` 且 `device_sn/registrant` 均为 NULL、同一天
    → 这两个 COALESCE 都归一为 `''`，**若不复现 WHERE** 就会聚成一个「重复组」→ 误报。
    复现 WHERE 后这些行不在索引覆盖范围内，应报 0。
    """
    tbl = _uniq("gov_d1_no_fp")
    # ⚠️ register_time 必须是 timestamptz：`timestamptz AT TIME ZONE 'zone'` 才是 IMMUTABLE
    #    （timestamp 版本是 STABLE，PG 会拒绝建索引：functions in index expression must be
    #     marked IMMUTABLE）。生产 manual_refunds.register_time 正是 timestamptz。
    db.execute(text(f"""
        CREATE TABLE {tbl} (
            id bigserial PRIMARY KEY,
            register_time timestamptz,
            order_no text,
            device_sn text,
            registrant text
        )
    """))
    db.execute(text(f"""
        CREATE UNIQUE INDEX {tbl}_dedup ON {tbl} (
            ((register_time AT TIME ZONE 'Asia/Shanghai')::date),
            order_no,
            COALESCE(device_sn, ''),
            COALESCE(registrant, '')
        ) WHERE order_no IS NOT NULL
    """))
    for _ in range(2):
        db.execute(text(
            f"INSERT INTO {tbl} (register_time, order_no, device_sn, registrant) "
            "VALUES (:t, NULL, NULL, NULL)"
        ), {"t": _dt.datetime(2026, 9, 20, 8, 0)})
    db.flush()

    findings = scan_unique_index_dups(db, tables=[tbl])
    assert findings == [], (
        f"部分索引(WHERE order_no IS NOT NULL)不覆盖的行不应参与分组, "
        f"实际误报 {len(findings)} 个: {findings}"
    )


def test_d1_detects_dups_inside_partial_expr_index(db):
    """部分索引**覆盖范围内**的真重复仍须检出，且 repro_sql 复现表达式而非裸列。"""
    tbl = _uniq("gov_d1_real_dup")
    # reg_dt 用 timestamp（不是 date）：否则 `date(reg_dt)` 是无操作转换，PG 会把它归一掉，
    # 索引定义里就看不到 date() 了，本用例也就测不到「复现表达式」这件事。
    db.execute(text(f"""
        CREATE TABLE {tbl} (
            id bigserial PRIMARY KEY, reg_dt timestamp, order_no text, device_sn text
        )
    """))
    db.execute(text(
        f"CREATE UNIQUE INDEX {tbl}_dedup ON {tbl} (date(reg_dt), order_no, device_sn) "
        "WHERE order_no IS NOT NULL"
    ))
    # 同一天(不同时刻) + 同 order_no + device_sn 均为 NULL → 含 NULL 列，索引拦不住 → 真重复
    # （date() 把 08:00 与 15:00 归到同一天 → 才能成组；若用裸 reg_dt 则拆成两组，报不出）
    for hour in (8, 15):
        db.execute(text(f"INSERT INTO {tbl} (reg_dt, order_no, device_sn) VALUES (:d, :o, NULL)"),
                   {"d": _dt.datetime(2026, 9, 20, hour, 0), "o": "WXALR123"})
    db.flush()

    findings = scan_unique_index_dups(db, tables=[tbl])
    assert len(findings) == 1, f"应检出 1 个真重复, 实际 {len(findings)}"
    assert findings[0]["metrics"]["redundant_rows"] == 1
    # repro_sql 必须复现 date() 表达式（铁律①：裸列 reg_dt 会把不同时刻拆成不同组）
    assert "date(reg_dt)" in findings[0]["repro_sql"]
    assert "order_no IS NOT NULL" in findings[0]["repro_sql"]


# ─────────────────────────────────────────────────────────────
# 铁律② / #160 回归：可空列为 NULL 的重复能检出
# ─────────────────────────────────────────────────────────────
def test_d1_detects_null_column_dups(db):
    """device_sn NULL 重复 → 检出为 protection_gap（#160 场景回归）。"""
    tbl = _uniq("gov_d1_null_dup")
    db.execute(text(f"""
        CREATE TABLE {tbl} (
            id bigserial PRIMARY KEY,
            acct text, op_type text, op_time timestamptz,
            operator text, op_amount numeric, device_sn text
        )
    """))
    db.execute(text(
        f"CREATE UNIQUE INDEX {tbl}_business ON {tbl} "
        "(acct, op_type, op_time, operator, op_amount, device_sn)"
    ))
    payload = {"a": "acct-1", "t": "用户提现",
               "tm": _dt.datetime(2026, 8, 28, 15, 39, tzinfo=_dt.timezone.utc),
               "o": "用户提现", "amt": 34.0}
    for _ in range(2):
        db.execute(text(
            f"INSERT INTO {tbl} (acct, op_type, op_time, operator, op_amount, device_sn) "
            "VALUES (:a, :t, :tm, :o, :amt, NULL)"
        ), payload)
    db.flush()

    findings = scan_unique_index_dups(db, tables=[tbl])
    assert len(findings) == 1, "device_sn NULL 重复应检出(漏报回归)"
    f = findings[0]
    assert f["metrics"]["redundant_rows"] == 1
    assert f["metrics"]["dup_groups"] == 1
    assert f["metrics"]["gap_kind"] == GAP_PROTECTION_GAP, "普通索引(非 NND) → 保护缺口"
    assert f["severity_raw"] == "high"          # 占比 100% ≥ 阈值

    # 样本脱敏：acct 敏感 → 哈希；op_type/op_time 非敏感 → 保留（供研判）
    s = f["samples"][0]
    assert s["acct"].startswith("#"), "敏感列(acct)应脱敏为哈希"
    assert s["op_type"] == "用户提现", "非敏感列应保留原文供研判"
    assert s["op_time"] == "2026-08-28T15:39:00+00:00", "datetime 应转 ISO 字符串(可落 JSONB)"
    assert s["device_sn"] is None
    assert s["_dup_count"] == 2

    # repro_sql 与 metrics 数字必须一致（可审计, 拒绝黑盒结论）
    row = db.execute(text(f["repro_sql"])).one()
    assert int(row.dup_groups) == f["metrics"]["dup_groups"]
    assert int(row.redundant_rows) == f["metrics"]["redundant_rows"]


def test_d1_excludes_all_null_group_but_keeps_partial(db):
    """「整组全 NULL」排除，「部分列有值」仍检出（铁律②的精确边界）。"""
    tbl = _uniq("gov_d1_all_null")
    db.execute(text(f"""
        CREATE TABLE {tbl} (id bigserial PRIMARY KEY, code text, sub text)
    """))
    db.execute(text(f"CREATE UNIQUE INDEX {tbl}_code ON {tbl} (code, sub)"))
    # ① 3 行 (code, sub) 全为 NULL → 组内无任何非 NULL 信息 → 业务允许, 应排除
    for _ in range(3):
        db.execute(text(f"INSERT INTO {tbl} (code, sub) VALUES (NULL, NULL)"))
    # ② 2 行 code 有值、sub 为 NULL → 「部分列有值」→ 真缺口, 应检出
    for _ in range(2):
        db.execute(text(f"INSERT INTO {tbl} (code, sub) VALUES ('DUP-1', NULL)"))
    db.flush()

    findings = scan_unique_index_dups(db, tables=[tbl])
    assert len(findings) == 1, f"应只报『部分列有值』那一组, 实际 {len(findings)}"
    assert findings[0]["metrics"]["redundant_rows"] == 1, "全 NULL 组(3 行)应被排除, 不计冗余"
    assert findings[0]["samples"][0]["code"] == "DUP-1"
    assert findings[0]["samples"][0]["sub"] is None


# ─────────────────────────────────────────────────────────────
# persist 落库 + 幂等
# ─────────────────────────────────────────────────────────────
def test_d1_run_scan_persists_and_is_idempotent(_engine):
    """run_d1_scan(persist=True) 落 open finding；重复扫描刷新证据而非堆叠记录。

    用独立 session + 手工清理（persist_findings 会 commit，不能用 db fixture 的事务）。
    """
    from sqlalchemy.orm import Session

    tbl = _uniq("gov_d1_persist")
    with Session(_engine) as s:
        try:
            s.execute(text(f"CREATE TABLE {tbl} (id bigserial PRIMARY KEY, acct text, device_sn text)"))
            s.execute(text(f"CREATE UNIQUE INDEX {tbl}_dedup ON {tbl} (acct, device_sn)"))
            for _ in range(2):
                s.execute(text(f"INSERT INTO {tbl} (acct, device_sn) VALUES ('acctX', NULL)"))
            s.commit()

            first = run_d1_scan(s, tables=[tbl])
            assert first["found"] == 1 and first["persisted"] == 1
            n1 = s.execute(text(
                "SELECT count(*) FROM governance_findings WHERE table_name = :t AND status = :st"
            ), {"t": tbl, "st": FINDING_STATUS_OPEN}).scalar()
            assert n1 == 1, "应落 1 条 open finding"

            # 第二次扫描：幂等（刷新证据, 不新增行）
            second = run_d1_scan(s, tables=[tbl])
            assert second["persisted"] == 1
            n2 = s.execute(text(
                "SELECT count(*) FROM governance_findings WHERE table_name = :t AND status = :st"
            ), {"t": tbl, "st": FINDING_STATUS_OPEN}).scalar()
            assert n2 == 1, "重复扫描不应堆叠 open finding(同 #166 教训)"

            # 证据三件套齐备
            row = s.execute(text(
                "SELECT metrics, samples, repro_sql, detector FROM governance_findings "
                "WHERE table_name = :t"
            ), {"t": tbl}).mappings().one()
            assert row["detector"] == "unique_index_dup"
            assert row["metrics"]["redundant_rows"] == 1
            assert row["samples"], "样本不应为空"
            assert "GROUP BY" in row["repro_sql"]
        finally:
            s.execute(text("DELETE FROM governance_findings WHERE table_name = :t"), {"t": tbl})
            s.execute(text(f"DROP TABLE IF EXISTS {tbl}"))
            s.commit()

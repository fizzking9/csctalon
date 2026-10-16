# -*- coding: utf-8 -*-
"""退款五路采纳规则引擎测试 (#332, T2: TR-2.1~TR-2.4)

夹具覆盖五路每个机器规则码 + 未知枚举 pending; 不连真实库以外的任何外部服务。
作业以 commit=False 调用, 由 db fixture 事务回滚隔离。
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from app.services.governance.refund_adoption import (
    A_ADOPT_ONLINE,
    A_EX_GARBAGE,
    A_EX_METHOD,
    A_EX_STATUS,
    A_EX_STATUS_NULL,
    ADOPT_RULE_VER,
    B_ADOPT_INFLOW,
    B_ADOPT_OUTFLOW,
    B_EX_FAIL_FROZEN,
    B_EX_NULL_TYPE,
    C_ADOPT,
    C_EX_NOT_REFUNDED,
    D_ADOPT,
    D_EX_DUP,
    D_EX_NO_SN,
    D_EX_NOT_REFUNDED,
    E_ADOPT_ALL,
    PENDING_UNKNOWN,
    ST_ADOPTED,
    ST_EXCLUDED,
    ST_PENDING,
    run_refund_adoption,
)

_TS = "2026-10-16T08:00:00+08:00"
_LONG_GARBAGE = "该订单状态异常请联系管理员处理超长错位文本哦哦哦哦哦"  # >16 字

_ODS_TABLES = ("ods.refunds", "ods.wallet_withdrawals", "ods.shanmao_refunds",
               "ods.manual_refunds", "ods.refund_record_reports")
_STD_TABLES = ("std_refund_online", "std_wallet_flow", "std_refund_shanmao",
               "std_refund_manual", "std_refund_record")


def _purge(db) -> None:
    """清空五路 ods/std 表与覆盖表, 使本用例输入集完全由夹具决定。

    为什么必须清: T4 人工覆盖 API / T6 治理作业用例经**作业内部 commit** 会把行
    (RC-OV1 / RC-JOB1) 与覆盖记录落在持久测试库 csctalon_test, 而本文件多处以
    **绝对计数**断言(total / std 表行数)。残留会使计数偏大、断言假红。
    清理在本用例事务内执行, 随 db fixture 回滚, 不改动其它用例数据。
    """
    for _t in _STD_TABLES + _ODS_TABLES:
        db.execute(text(f"DELETE FROM {_t}"))
    db.execute(text("DELETE FROM adoption_overrides"))


def _seed(db) -> None:
    """构造五路全分类夹具(每路各规则码至少命中 1 行 + 未知枚举 pending 行)。"""
    _purge(db)  # 先清残留, 保证输入集确定(见 _purge 说明)
    # ── A 路 ods.refunds ──
    db.execute(text("""
        INSERT INTO ods.refunds
            (refund_no, status, refund_method, refund_amount, actual_refund_time)
        VALUES
            ('RA1', '已退款', '线上', 11.11, :ts),
            ('RA2', '已退款', NULL,   22.22, :ts),   -- 空方式但已退款 → 采纳(普查 530 行场景)
            ('RA3', '已退款', '转入提现账户', 33.00, :ts),
            ('RA4', '驳回',   '线下', 44.00, :ts),   -- 方式优先级高于状态
            ('RA5', '已退款', '已退款', 55.00, :ts), -- 错位垃圾值
            ('RA6', '申请中', NULL, 66.00, :ts),
            ('RA7', NULL,     NULL, 77.00, :ts),     -- 状态空
            ('RA8', :long_g,  '线上', 88.00, :ts),   -- 长文本垃圾状态
            ('RA9', '火星了', '线上', 99.00, :ts)    -- 未知短枚举 → pending
    """), {"ts": _TS, "long_g": _LONG_GARBAGE})

    # ── B 路 ods.wallet_withdrawals(显式高位 id 避免与序列冲突) ──
    db.execute(text("""
        INSERT INTO ods.wallet_withdrawals
            (id, op_type, op_amount, remark, quality_note, op_time)
        VALUES
            (9101, '订单退费转入', 50.00, NULL, NULL, :ts),
            (9102, '系统扣费',      5.50, NULL, NULL, :ts),
            (9103, '用户提现',     20.00, '提现失败', NULL, :ts),
            (9104, '用户提现',     21.00, NULL, '账户冻结拦截', :ts),
            (9105, '用户提现',      7.00, '正常到账', NULL, :ts),
            (9106, '',             NULL, NULL, NULL, :ts),
            (9107, '未知操作X',     3.00, NULL, NULL, :ts)
    """), {"ts": _TS})

    # ── C 路 ods.shanmao_refunds ──
    db.execute(text("""
        INSERT INTO ods.shanmao_refunds
            (refund_no, status, actual_amount, refund_amount, refund_time)
        VALUES
            ('RC1', '已退款', 9.90, 10.00, :ts),
            ('RC2', '已驳回', 19.00, 19.00, :ts),
            ('RC3', NULL,     29.00, 29.00, :ts),
            ('RC4', '神秘状态', 39.00, 39.00, :ts)
    """), {"ts": _TS})

    # ── D 路 ods.manual_refunds ──
    db.execute(text("""
        INSERT INTO ods.manual_refunds
            (id, is_refunded, quality_status, refund_amount, actual_refund_time)
        VALUES
            (9201, '是', NULL,            88.00, :ts),
            (9202, '否', 'dup_residual',  80.00, :ts),  -- 重复优先于未退款
            (9203, '是', 'sn_unresolvable', 7.00, :ts),
            (9204, '否', NULL,             6.00, :ts),
            (9205, NULL, NULL,             5.00, :ts)
    """), {"ts": _TS})

    # ── E 路 ods.refund_record_reports(全量采纳, 含空 refund_status) ──
    db.execute(text("""
        INSERT INTO ods.refund_record_reports
            (id, refund_status, refund_amount, refund_time, device_sn, merchant_id)
        VALUES
            (9301, '退款成功', 1.00, :ts, NULL, NULL),
            (9302, '成功',     2.00, :ts, NULL, NULL),
            (9303, NULL,       3.00, :ts, NULL, NULL)
    """), {"ts": _TS})
    db.flush()


# ── TR-2.1 五路规则码命中集合与文档 §二 完全一致, 无 NULL state 残留 ─────────
def test_rule_codes_classification(db):
    _seed(db)
    run_refund_adoption(db, commit=False)

    a = {r[0]: (r[1], r[2]) for r in db.execute(text(
        "SELECT refund_no, adopt_state, adopt_reason FROM ods.refunds"
    )).all()}
    assert a["RA1"] == (ST_ADOPTED, A_ADOPT_ONLINE)
    assert a["RA2"] == (ST_ADOPTED, A_ADOPT_ONLINE)
    assert a["RA3"] == (ST_EXCLUDED, A_EX_METHOD)
    assert a["RA4"] == (ST_EXCLUDED, A_EX_METHOD)
    assert a["RA5"] == (ST_EXCLUDED, A_EX_GARBAGE)
    assert a["RA6"] == (ST_EXCLUDED, A_EX_STATUS)
    assert a["RA7"] == (ST_EXCLUDED, A_EX_STATUS_NULL)
    assert a["RA8"] == (ST_EXCLUDED, A_EX_GARBAGE)
    assert a["RA9"] == (ST_PENDING, PENDING_UNKNOWN)

    b = {r[0]: (r[1], r[2]) for r in db.execute(text(
        "SELECT id, adopt_state, adopt_reason FROM ods.wallet_withdrawals"
    )).all()}
    assert b[9101] == (ST_ADOPTED, B_ADOPT_INFLOW)
    assert b[9102] == (ST_ADOPTED, B_ADOPT_OUTFLOW)
    assert b[9103] == (ST_EXCLUDED, B_EX_FAIL_FROZEN)
    assert b[9104] == (ST_EXCLUDED, B_EX_FAIL_FROZEN)
    assert b[9105] == (ST_ADOPTED, B_ADOPT_OUTFLOW)
    assert b[9106] == (ST_EXCLUDED, B_EX_NULL_TYPE)
    assert b[9107] == (ST_PENDING, PENDING_UNKNOWN)

    c = {r[0]: (r[1], r[2]) for r in db.execute(text(
        "SELECT refund_no, adopt_state, adopt_reason FROM ods.shanmao_refunds"
    )).all()}
    assert c["RC1"] == (ST_ADOPTED, C_ADOPT)
    assert c["RC2"] == (ST_EXCLUDED, C_EX_NOT_REFUNDED)
    assert c["RC3"] == (ST_PENDING, PENDING_UNKNOWN)
    assert c["RC4"] == (ST_PENDING, PENDING_UNKNOWN)

    d = {r[0]: (r[1], r[2]) for r in db.execute(text(
        "SELECT id, adopt_state, adopt_reason FROM ods.manual_refunds"
    )).all()}
    assert d[9201] == (ST_ADOPTED, D_ADOPT)
    assert d[9202] == (ST_EXCLUDED, D_EX_DUP)
    assert d[9203] == (ST_EXCLUDED, D_EX_NO_SN)
    assert d[9204] == (ST_EXCLUDED, D_EX_NOT_REFUNDED)
    assert d[9205] == (ST_PENDING, PENDING_UNKNOWN)

    e = {r[0]: (r[1], r[2]) for r in db.execute(text(
        "SELECT id, adopt_state, adopt_reason FROM ods.refund_record_reports"
    )).all()}
    assert all(v == (ST_ADOPTED, E_ADOPT_ALL) for v in e.values())

    # 五路全部盖章: 无 NULL state/reason/rule_ver, adopted_at 已写
    for tbl in ("ods.refunds", "ods.wallet_withdrawals", "ods.shanmao_refunds",
                "ods.manual_refunds", "ods.refund_record_reports"):
        nulls = db.execute(text(f"""
            SELECT count(*) FROM {tbl}
            WHERE adopt_state IS NULL OR adopt_reason IS NULL
               OR adopt_rule_ver IS NULL OR adopted_at IS NULL
        """)).scalar_one()
        assert nulls == 0, f"{tbl} 存在未盖章行"
        vers = db.execute(text(
            f"SELECT DISTINCT adopt_rule_ver FROM {tbl}"
        )).scalars().all()
        assert vers == [ADOPT_RULE_VER]


def test_job_stats_counts(db):
    """返回结构: 每路三态计数/std 行数/std 金额合计/耗时齐全且与夹具一致。"""
    _seed(db)
    res = run_refund_adoption(db, commit=False)
    assert res["rule_ver"] == ADOPT_RULE_VER
    s = res["routes"]
    assert (s["A"]["total"], s["A"]["adopted"], s["A"]["excluded"], s["A"]["pending"]) == (9, 2, 6, 1)
    # B: 排除=失败提现 9103 + 冻结提现 9104 + 空 op_type 9106, 共 3 行
    assert (s["B"]["total"], s["B"]["adopted"], s["B"]["excluded"], s["B"]["pending"]) == (7, 3, 3, 1)
    assert (s["C"]["total"], s["C"]["adopted"], s["C"]["excluded"], s["C"]["pending"]) == (4, 1, 1, 2)
    assert (s["D"]["total"], s["D"]["adopted"], s["D"]["excluded"], s["D"]["pending"]) == (5, 1, 3, 1)
    assert (s["E"]["total"], s["E"]["adopted"], s["E"]["excluded"], s["E"]["pending"]) == (3, 3, 0, 0)
    # 金额 Decimal 定点
    assert s["A"]["std_amount"] == Decimal("33.33")
    assert s["B"]["std_amount"] == Decimal("62.50")
    assert s["C"]["std_amount"] == Decimal("9.90")
    assert s["D"]["std_amount"] == Decimal("88.00")
    assert s["E"]["std_amount"] == Decimal("6.00")
    for st in s.values():
        assert st["elapsed_ms"] >= 0


# ── TR-2.2 std 行数=adopted; B flow_dir 集合; D source_grade ─────────────────
def test_std_tables_only_adopted_with_derived_columns(db):
    _seed(db)
    run_refund_adoption(db, commit=False)

    expected_rows = {"std_refund_online": 2, "std_wallet_flow": 3,
                     "std_refund_shanmao": 1, "std_refund_manual": 1,
                     "std_refund_record": 3}
    for tbl, n in expected_rows.items():
        assert db.execute(text(f"SELECT count(*) FROM {tbl}")).scalar_one() == n

    a_pks = set(db.execute(text(
        "SELECT refund_no FROM std_refund_online"
    )).scalars())
    assert a_pks == {"RA1", "RA2"}

    flow_dirs = set(db.execute(text(
        "SELECT DISTINCT flow_dir FROM std_wallet_flow"
    )).scalars())
    assert flow_dirs == {"inflow", "outflow"}
    inflow_n = db.execute(text(
        "SELECT count(*) FROM std_wallet_flow WHERE flow_dir='inflow'"
    )).scalar_one()
    assert inflow_n == 1
    b_pks = set(db.execute(text("SELECT id FROM std_wallet_flow")).scalars())
    assert b_pks == {9101, 9102, 9105}

    grades = db.execute(text(
        "SELECT DISTINCT source_grade FROM std_refund_manual"
    )).scalars().all()
    assert grades == ["manual"]

    # std 溯源列已写
    assert db.execute(text(
        "SELECT count(*) FROM std_refund_record "
        "WHERE adopt_rule_ver <> :v OR adopted_at IS NULL"
    ), {"v": ADOPT_RULE_VER}).scalar_one() == 0


# ── TR-2.3 未知枚举行 pending 且不进 std ─────────────────────────────────────
def test_pending_rows_not_in_std(db):
    _seed(db)
    run_refund_adoption(db, commit=False)
    assert db.execute(text(
        "SELECT count(*) FROM std_refund_online WHERE refund_no='RA9'"
    )).scalar_one() == 0
    assert db.execute(text(
        "SELECT count(*) FROM std_wallet_flow WHERE id=9107"
    )).scalar_one() == 0
    assert db.execute(text(
        "SELECT count(*) FROM std_refund_shanmao WHERE refund_no IN ('RC3','RC4')"
    )).scalar_one() == 0
    assert db.execute(text(
        "SELECT count(*) FROM std_refund_manual WHERE id=9205"
    )).scalar_one() == 0


# ── 人工覆盖(FR-6 机器部分): state 被覆盖, reason 仍记机器码, std 随 state ───
def test_manual_override_state_wins_reason_stays_machine(db):
    _seed(db)
    db.execute(text("""
        INSERT INTO adoption_overrides
            (route, src_pk, target_state, reason, operator)
        VALUES ('A', 'RA3', 'adopted', '业务核实该单确属线上直退', 'tester')
    """))
    db.flush()
    run_refund_adoption(db, routes="A", commit=False)

    row = db.execute(text(
        "SELECT adopt_state, adopt_reason FROM ods.refunds WHERE refund_no='RA3'"
    )).one()
    assert row.adopt_state == ST_ADOPTED          # 人工目标状态生效
    assert row.adopt_reason == A_EX_METHOD        # 机器码仍保留可对照
    assert db.execute(text(
        "SELECT count(*) FROM std_refund_online WHERE refund_no='RA3'"
    )).scalar_one() == 1                          # 进 std

    # 撤销覆盖后重算回到机器结论(幂等可回退)
    db.execute(text("DELETE FROM adoption_overrides WHERE route='A' AND src_pk='RA3'"))
    db.flush()
    run_refund_adoption(db, routes="A", commit=False)
    row = db.execute(text(
        "SELECT adopt_state, adopt_reason FROM ods.refunds WHERE refund_no='RA3'"
    )).one()
    assert row.adopt_state == ST_EXCLUDED
    assert row.adopt_reason == A_EX_METHOD
    assert db.execute(text(
        "SELECT count(*) FROM std_refund_online WHERE refund_no='RA3'"
    )).scalar_one() == 0


# ── TR-2.4 连跑两次: 五路盖章结果与 std 内容校验和一致(幂等) ─────────────────
def test_idempotent_rerun_checksums(db):
    _seed(db)

    def snapshot():
        ods_grp = {}
        for tbl, pk in (
            ("ods.refunds", "refund_no"),
            ("ods.wallet_withdrawals", "id"),
            ("ods.shanmao_refunds", "refund_no"),
            ("ods.manual_refunds", "id"),
            ("ods.refund_record_reports", "id"),
        ):
            rows = db.execute(text(f"""
                SELECT adopt_state, adopt_reason, count(*) AS c
                FROM {tbl} GROUP BY 1, 2 ORDER BY 1, 2
            """)).all()
            ods_grp[tbl] = [(r[0], r[1], int(r[2])) for r in rows]

        std_sig = {}
        for tbl, pk in (
            ("std_refund_online", "refund_no"),
            ("std_wallet_flow", "id::text"),
            ("std_refund_shanmao", "refund_no"),
            ("std_refund_manual", "id::text"),
            ("std_refund_record", "id::text"),
        ):
            sig = db.execute(text(f"""
                SELECT md5(string_agg(x, '|' ORDER BY x))
                FROM (SELECT {pk} AS x FROM {tbl}) t
            """)).scalar_one()
            std_sig[tbl] = sig
        return ods_grp, std_sig

    run_refund_adoption(db, commit=False)
    ods1, std1 = snapshot()
    run_refund_adoption(db, commit=False)
    ods2, std2 = snapshot()
    assert ods1 == ods2
    assert std1 == std2


def test_batched_keyset_update_covers_all(db, monkeypatch):
    """UPDATE_BATCH=2 时 keyset 多批仍覆盖全部行(游标不重不漏)。"""
    import app.services.governance.refund_adoption as ga

    monkeypatch.setattr(ga, "UPDATE_BATCH", 2)
    _seed(db)
    res = run_refund_adoption(db, commit=False)
    s = res["routes"]
    assert s["A"]["total"] == 9 and s["A"]["adopted"] == 2
    assert s["B"]["total"] == 7 and s["B"]["adopted"] == 3
    assert s["E"]["total"] == 3 and s["E"]["adopted"] == 3
    # 未盖章行为 0
    assert db.execute(text(
        "SELECT (SELECT count(*) FROM ods.refunds WHERE adopt_state IS NULL)"
        " + (SELECT count(*) FROM ods.wallet_withdrawals WHERE adopt_state IS NULL)"
        " + (SELECT count(*) FROM ods.shanmao_refunds WHERE adopt_state IS NULL)"
        " + (SELECT count(*) FROM ods.manual_refunds WHERE adopt_state IS NULL)"
        " + (SELECT count(*) FROM ods.refund_record_reports WHERE adopt_state IS NULL)"
    )).scalar_one() == 0


def test_invalid_route_raises(db):
    try:
        run_refund_adoption(db, routes="Z", commit=False)
    except ValueError:
        return
    raise AssertionError("非法路别必须抛 ValueError")

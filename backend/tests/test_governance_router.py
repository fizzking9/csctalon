# -*- coding: utf-8 -*-
"""AI 数据治理路由 + 分级执行测试（工单 #165）

对应设计 §六（分级执行/红线）与 §八（接口契约）。

覆盖面：
  1. findings 列表/详情（筛选、分页、服务端算出的 auto_level/requires_confirm）
  2. **L2 未人工确认时执行返回 400**（验收第 2 条红线断言）
  3. **L2 已确认执行：强制备份存在 + 备份行数 == 待删行数 + 表行数正确减少**（验收第 2 条）
  4. L0 report_only 执行不改任何数据；create_index 明确拒绝（须走 Alembic）
  5. confirm 只允许已研判 finding；dismiss 沉淀规则
  6. 规则 CRUD + 红线（规则不得配置不可逆动作）
  7. summary 概览结构
  8. scans/judge 异步入口：202 + 占位事件可轮询（后台任务被 mock，避免测试期扫全库）

策略：admin client 用 `get_current_user`/`get_db` override（同 #157 手法），
真实走路由与 DB（测试库 csctalon_test）；产生的行按基线 id 清理。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.v1 import governance as gov_router
from app.core.database import get_db
from app.main import app
from app.models.governance_finding import (
    DETECTOR_IMPOSSIBLE_DATE,
    FINDING_STATUS_JUDGED,
    FINDING_STATUS_RESOLVED,
    GovernanceFinding,
)
from app.models.user import RoleEnum, User
from app.services.governance_detector import (
    persist_findings,
    scan_impossible_dates,
    scan_unique_index_dups,
)

_ADMIN_ID = 9101


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def admin_client(_engine):
    """admin TestClient（override 认证与 db，与路由用同一测试库）。"""
    user = User(id=_ADMIN_ID, username="gov_admin", role=RoleEnum.ADMIN.value, is_active=True)

    def _override_user():
        return user

    def _override_db():
        session = Session(_engine, autocommit=False, autoflush=False)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _mk_dup_table(db: Session, tbl: str, index_name: str) -> None:
    """造一张含真重复的表 + **部分**唯一索引（覆盖可空列，D1 能检出）。

    为什么用部分索引（`WHERE acct IS NULL`）：普通唯一索引会拦住非 NULL 的重复插入，
    造不出「非 NULL 重复」；部分索引还能顺带覆盖「判据必须带上 indpred」这条铁律 ①
    —— 非索引范围的行（('B',2) 两行）不该被删除，测试里会断言它们仍在。
    """
    db.execute(text(f"CREATE TABLE {tbl} (id bigserial PRIMARY KEY, acct text, amt int)"))
    db.execute(text(
        f"CREATE UNIQUE INDEX {index_name} ON {tbl} (acct, amt) WHERE acct IS NULL"
    ))
    # 索引覆盖范围（acct IS NULL）内：3 行同组 → 2 行冗余
    # 范围外：('B',2) 两行（部分索引不覆盖）→ **不属于本 finding 的处置对象**
    db.execute(text(
        f"INSERT INTO {tbl} (acct, amt) VALUES (NULL, 100), (NULL, 100), (NULL, 100),"
        f" ('A', 1), ('B', 2), ('B', 2)"
    ))
    db.commit()


def _cleanup(db: Session, *, base_find: int, base_ev: int, base_rule: int, tables: list[str]):
    db.rollback()
    db.execute(text("DELETE FROM governance_findings WHERE id > :b"), {"b": base_find})
    db.execute(text("DELETE FROM system_events WHERE id > :b"), {"b": base_ev})
    db.execute(text("DELETE FROM governance_rules WHERE id > :b"), {"b": base_rule})
    for t in tables:
        db.execute(text(f"DROP TABLE IF EXISTS {t}"))
    db.commit()


def _now_utc():
    import datetime as _d

    return _d.datetime.now(_d.timezone.utc)


def _baselines(db: Session) -> tuple[int, int, int]:
    return (
        db.execute(text("SELECT coalesce(max(id),0) FROM governance_findings")).scalar(),
        db.execute(text("SELECT coalesce(max(id),0) FROM system_events")).scalar(),
        db.execute(text("SELECT coalesce(max(id),0) FROM governance_rules")).scalar(),
    )


# ─────────────────────────────────────────────────────────────────────
# 一、findings 列表 / 详情
# ─────────────────────────────────────────────────────────────────────
def test_findings_list_filters_and_detail(admin_client, _engine):
    with Session(_engine) as s:
        base_find, base_ev, base_rule = _baselines(s)
        tbl = _uniq("gov_api_dup")
        try:
            _mk_dup_table(s, tbl, _uniq("uq_api"))
            findings = scan_unique_index_dups(s, tables=[tbl])
            assert findings, "D1 应检出重复"
            f = persist_findings(s, findings)[0]

            res = admin_client.get("/api/v1/governance/findings", params={"table_name": tbl})
            body = res.json()
            assert body["code"] == 0
            assert body["data"]["total"] == 1
            item = body["data"]["items"][0]
            assert item["id"] == f.id and item["detector"] == "unique_index_dup"
            assert item["metrics"]["redundant_rows"] == 2
            assert item["metrics"]["index_where"] == "(acct IS NULL)", "部分索引判据必须带 WHERE"

            det = admin_client.get(f"/api/v1/governance/findings/{f.id}").json()["data"]
            assert det["repro_sql"] and "HAVING count(*) > 1" in det["repro_sql"]
            assert det["samples"], "样本不应为空（AI 研判依据）"
            # 未研判 → 无建议 → 按最严级别（L2）处理，需确认
            assert det["auto_level"] == "L2_irreversible"
            assert det["requires_confirm"] is True

            assert admin_client.get("/api/v1/governance/findings/99999999").status_code == 404
        finally:
            _cleanup(s, base_find=base_find, base_ev=base_ev, base_rule=base_rule, tables=[tbl])


# ─────────────────────────────────────────────────────────────────────
# 二、分级执行（红线）
# ─────────────────────────────────────────────────────────────────────
def _make_judged_finding(s: Session, tbl: str, action: str) -> tuple[int, str]:
    """造真表 + D1 真扫描 + 落库，并把 finding 置为已研判（suggestion.action=action）。"""
    _mk_dup_table(s, tbl, _uniq("uq_l2"))
    findings = scan_unique_index_dups(s, tables=[tbl])
    assert findings and findings[0]["metrics"]["redundant_rows"] == 2
    fid = persist_findings(s, findings)[0].id
    f = s.get(GovernanceFinding, fid)
    f.status = FINDING_STATUS_JUDGED
    f.verdict = "real_issue"
    f.verdict_reason = "测试研判结论"
    f.confidence = 0.9
    f.suggestion = {"action": action, "params": {}, "impact": "测试", "risk": "high",
                    "requires_human_confirm": action in ("delete_dups", "create_index")}
    s.commit()
    return fid, findings[0]["target"]


def test_execute_l2_without_confirm_returns_400(admin_client, _engine):
    """验收 2（红线）：L2 不可逆动作未人工确认 → 400，且**未删任何行**。"""
    with Session(_engine) as s:
        base_find, base_ev, base_rule = _baselines(s)
        tbl = _uniq("gov_l2_nc")
        try:
            fid, _ = _make_judged_finding(s, tbl, "delete_dups")
            res = admin_client.post(f"/api/v1/governance/findings/{fid}/execute")
            assert res.status_code == 400, res.text
            assert "确认" in res.json()["detail"]
            # 数据未动
            assert s.execute(text(f"SELECT count(*) FROM {tbl}")).scalar() == 6
            assert s.execute(text(
                "SELECT status FROM governance_findings WHERE id=:i"), {"i": fid}).scalar() == "judged"
        finally:
            _cleanup(s, base_find=base_find, base_ev=base_ev, base_rule=base_rule, tables=[tbl])


def test_execute_l2_after_confirm_backs_up_and_deletes(admin_client, _engine):
    """验收 2：确认后执行 —— **备份表存在且行数 == 待删行数**，数据正确减少，写凭证。"""
    with Session(_engine) as s:
        base_find, base_ev, base_rule = _baselines(s)
        tbl = _uniq("gov_l2_ok")
        bak = f"gov_bak_{tbl}"
        try:
            fid, _ = _make_judged_finding(s, tbl, "delete_dups")
            c = admin_client.post(f"/api/v1/governance/findings/{fid}/confirm",
                                  json={"note": "测试确认"})
            assert c.status_code == 200, c.text
            assert c.json()["data"]["confirmed_by"] == "gov_admin"

            res = admin_client.post(f"/api/v1/governance/findings/{fid}/execute")
            assert res.status_code == 200, res.text
            data = res.json()["data"]
            er = data["execute_result"]
            assert data["auto_level"] == "L2_irreversible"
            assert er["affected_rows"] == 2
            assert er["before_rows"] == 6 and er["after_rows"] == 4
            # ★ 备份存在且行数 == 待删行数
            assert er["backup_table"] == f"{bak}_{fid}"
            bak_rows = s.execute(text(f'SELECT count(*) FROM "{er["backup_table"]}"')).scalar()
            assert bak_rows == 2, "备份行数必须等于待删行数"
            assert er["backup_rows"] == 2
            # 真删了 2 行，索引范围内的重复消失
            assert s.execute(text(f"SELECT count(*) FROM {tbl}")).scalar() == 4
            dup_left = s.execute(text(
                f"SELECT count(*) FROM (SELECT acct, amt FROM {tbl} WHERE acct IS NULL "
                f"GROUP BY acct, amt HAVING count(*) > 1) x"
            )).scalar()
            assert dup_left == 0
            # ★ 部分索引范围外的行不得被删（判据同源的反向验证）
            assert s.execute(text(
                f"SELECT count(*) FROM {tbl} WHERE acct = 'B'")).scalar() == 2
            # finding 终态 + 凭证落库
            f = s.get(GovernanceFinding, fid)
            assert f.status == FINDING_STATUS_RESOLVED and f.executed_at is not None
            assert f.execute_result["backup_table"] == er["backup_table"]
            # 留痕
            ev = s.execute(text(
                "SELECT count(*) FROM system_events WHERE category='ai_governance' "
                "AND action='governance_execute' AND id > :b"
            ), {"b": base_ev}).scalar()
            assert ev >= 2, "确认与执行各应留痕一条"
            s.execute(text(f'DROP TABLE IF EXISTS "{er["backup_table"]}"'))
            s.commit()
        finally:
            _cleanup(s, base_find=base_find, base_ev=base_ev, base_rule=base_rule,
                     tables=[tbl])


def test_execute_rejects_when_counts_differ(admin_client, _engine):
    """安全设计②：待删行数与 finding 报出的冗余行数不一致 → 拒绝执行（不按过期证据删数据）。"""
    with Session(_engine) as s:
        base_find, base_ev, base_rule = _baselines(s)
        tbl = _uniq("gov_l2_drift")
        try:
            fid, _ = _make_judged_finding(s, tbl, "delete_dups")
            # 篡改判据证据（模拟「数据已变」）→ 执行必须拒绝
            f = s.get(GovernanceFinding, fid)
            f.metrics = {**(f.metrics or {}), "redundant_rows": 99}
            s.commit()
            admin_client.post(f"/api/v1/governance/findings/{fid}/confirm")
            res = admin_client.post(f"/api/v1/governance/findings/{fid}/execute")
            assert res.status_code == 400
            assert "不一致" in res.json()["detail"]
            assert s.execute(text(f"SELECT count(*) FROM {tbl}")).scalar() == 6
        finally:
            _cleanup(s, base_find=base_find, base_ev=base_ev, base_rule=base_rule, tables=[tbl])


def test_execute_l0_report_only_does_not_touch_data(admin_client, _engine):
    """L0 只读：无需确认即可执行，但不改任何数据，只出报告。"""
    with Session(_engine) as s:
        base_find, base_ev, base_rule = _baselines(s)
        tbl = _uniq("gov_l0")
        try:
            fid, _ = _make_judged_finding(s, tbl, "report_only")
            res = admin_client.post(f"/api/v1/governance/findings/{fid}/execute")
            assert res.status_code == 200, res.text
            data = res.json()["data"]
            assert data["auto_level"] == "L0_readonly"
            assert data["execute_result"]["report"]["repro_sql"]
            assert "post_execute" not in data["execute_result"], "L0 无需重建宽表/失效缓存"
            assert s.execute(text(f"SELECT count(*) FROM {tbl}")).scalar() == 6
        finally:
            _cleanup(s, base_find=base_find, base_ev=base_ev, base_rule=base_rule, tables=[tbl])


def test_execute_create_index_rejected_with_alembic_hint(admin_client, _engine):
    """create_index 属 L2 且必须走 Alembic 迁移 —— 明确拒绝而非静默跳过。"""
    with Session(_engine) as s:
        base_find, base_ev, base_rule = _baselines(s)
        tbl = _uniq("gov_ci")
        try:
            fid, _ = _make_judged_finding(s, tbl, "create_index")
            admin_client.post(f"/api/v1/governance/findings/{fid}/confirm")
            res = admin_client.post(f"/api/v1/governance/findings/{fid}/execute")
            assert res.status_code == 400
            assert "Alembic" in res.json()["detail"]
        finally:
            _cleanup(s, base_find=base_find, base_ev=base_ev, base_rule=base_rule, tables=[tbl])


def test_confirm_requires_judged(admin_client, _engine):
    """未研判（open）的 finding 不能确认。"""
    with Session(_engine) as s:
        base_find, base_ev, base_rule = _baselines(s)
        tbl = _uniq("gov_cf")
        try:
            _mk_dup_table(s, tbl, _uniq("uq_cf"))
            fid = persist_findings(s, scan_unique_index_dups(s, tables=[tbl]))[0].id
            res = admin_client.post(f"/api/v1/governance/findings/{fid}/confirm", json={})
            assert res.status_code == 400
            assert "尚未研判" in res.json()["detail"]
        finally:
            _cleanup(s, base_find=base_find, base_ev=base_ev, base_rule=base_rule, tables=[tbl])


def test_execute_twice_rejected(admin_client, _engine):
    """已执行（resolved）的 finding 不可重复执行。"""
    with Session(_engine) as s:
        base_find, base_ev, base_rule = _baselines(s)
        tbl = _uniq("gov_twice")
        try:
            fid, _ = _make_judged_finding(s, tbl, "report_only")
            assert admin_client.post(f"/api/v1/governance/findings/{fid}/execute").status_code == 200
            res = admin_client.post(f"/api/v1/governance/findings/{fid}/execute")
            assert res.status_code == 400
            assert "已执行过" in res.json()["detail"]
        finally:
            _cleanup(s, base_find=base_find, base_ev=base_ev, base_rule=base_rule, tables=[tbl])


# ─────────────────────────────────────────────────────────────────────
# 三、dismiss → 规则沉淀
# ─────────────────────────────────────────────────────────────────────
def test_dismiss_endpoint_sinks_rule(admin_client, _engine):
    with Session(_engine) as s:
        base_find, base_ev, base_rule = _baselines(s)
        tbl = _uniq("gov_dis")
        try:
            _mk_dup_table(s, tbl, _uniq("uq_dis"))
            fid = persist_findings(s, scan_unique_index_dups(s, tables=[tbl]))[0].id
            res = admin_client.post(f"/api/v1/governance/findings/{fid}/dismiss",
                                    json={"reason": "业务允许（测试）"})
            assert res.status_code == 200, res.text
            data = res.json()["data"]
            assert data["created"] is True
            rule = s.execute(text(
                "SELECT verdict, auto_level, disposition, source_finding_id, created_by "
                "FROM governance_rules WHERE id=:i"), {"i": data["rule_id"]}).one()
            assert rule.verdict == "allowed_by_business"
            assert rule.auto_level == "L0_readonly" and rule.disposition == "report_only"
            assert rule.source_finding_id == fid and rule.created_by == "gov_admin"
        finally:
            _cleanup(s, base_find=base_find, base_ev=base_ev, base_rule=base_rule, tables=[tbl])


# ─────────────────────────────────────────────────────────────────────
# 四、规则 CRUD + 红线
# ─────────────────────────────────────────────────────────────────────
def test_rules_crud_and_redline(admin_client, _engine):
    with Session(_engine) as s:
        base_find, base_ev, base_rule = _baselines(s)
        tbl = _uniq("gov_rule")
        try:
            # 红线：规则不得配置不可逆动作
            bad = admin_client.post("/api/v1/governance/rules", json={
                "detector": "unique_index_dup", "table_name": tbl, "target_pattern": "*",
                "verdict": "real_issue", "reason": "x", "disposition": "delete_dups",
            })
            assert bad.status_code == 400 and "不可逆" in bad.json()["detail"]

            ok = admin_client.post("/api/v1/governance/rules", json={
                "detector": "row_count_jump", "table_name": tbl, "target_pattern": "ts*",
                "verdict": "allowed_by_business", "reason": "测试规则", "disposition": "report_only",
            })
            assert ok.status_code == 200, ok.text
            rid = ok.json()["data"]["id"]
            assert ok.json()["data"]["enabled"] is True

            lst = admin_client.get("/api/v1/governance/rules", params={"detector": "row_count_jump"})
            assert any(r["id"] == rid for r in lst.json()["data"]["items"])

            patched = admin_client.patch(f"/api/v1/governance/rules/{rid}", json={"enabled": False})
            assert patched.status_code == 200 and patched.json()["data"]["enabled"] is False

            assert admin_client.patch("/api/v1/governance/rules/99999999",
                                      json={"enabled": True}).status_code == 404
        finally:
            _cleanup(s, base_find=base_find, base_ev=base_ev, base_rule=base_rule, tables=[])


# ─────────────────────────────────────────────────────────────────────
# 五、概览 + 异步入口
# ─────────────────────────────────────────────────────────────────────
def test_summary_shape(admin_client):
    res = admin_client.get("/api/v1/governance/summary")
    assert res.status_code == 200
    data = res.json()["data"]
    for k in ("by_status", "by_detector_open", "by_verdict", "rules_enabled",
              "rules_hits", "recent_events", "disposition_levels"):
        assert k in data, f"概览缺字段 {k}"
    assert data["disposition_levels"]["delete_dups"] == "L2_irreversible"


def test_scans_and_judge_async_placeholder(admin_client, _engine, monkeypatch):
    """异步入口：202 + 占位事件可轮询（后台任务 mock，避免测试期扫全库）。"""
    started: list[tuple[str, int]] = []
    monkeypatch.setattr(gov_router, "start_scan_task", lambda eid: started.append(("scan", eid)))
    monkeypatch.setattr(gov_router, "start_judge_task", lambda eid: started.append(("judge", eid)))

    with Session(_engine) as s:
        base_find, base_ev, base_rule = _baselines(s)
        try:
            r1 = admin_client.post("/api/v1/governance/scans")
            assert r1.status_code == 202, r1.text
            sid = r1.json()["data"]["scan_id"]
            poll = admin_client.get(f"/api/v1/governance/scans/{sid}").json()["data"]
            assert poll["task_id"] == sid and poll["status"] == "running"
            assert poll["finished"] is False

            r2 = admin_client.post("/api/v1/governance/judge")
            assert r2.status_code == 202
            jid = r2.json()["data"]["judge_id"]
            jpoll = admin_client.get(f"/api/v1/governance/judges/{jid}").json()["data"]
            assert jpoll["action"] == "governance_judge"

            assert started == [("scan", sid), ("judge", jid)]
            # 占位事件（running）不应让当日自动体检被跳过
            from app.services.governance_scan_service import _event_done_today
            assert _event_done_today(s, "governance_scan") is False
            assert admin_client.get("/api/v1/governance/scans/99999999").status_code == 404
        finally:
            _cleanup(s, base_find=base_find, base_ev=base_ev, base_rule=base_rule, tables=[])


# ─────────────────────────────────────────────────────────────────────
# 三、L2 fix_time（工单 #169 验收 2/3）+ L1 mark_pending（验收 4）
# ─────────────────────────────────────────────────────────────────────
def _make_date_finding(s: Session, tbl: str, rows_sql: str) -> tuple[int, int, str]:
    """造真表（finish_time/register_time）+ **D5 真扫描** + 落库 + 置为已研判。

    返回 (finding_id, 首行 id, 原值字符串)。走真探测器而非手写 finding ——
    保证「探测出的 metrics/samples」与执行层实际用到的东西一致。
    """
    s.execute(text(f"CREATE TABLE {tbl} (id bigserial PRIMARY KEY, "
                   f"finish_time timestamptz, register_time timestamptz)"))
    s.execute(text(f"INSERT INTO {tbl} (finish_time, register_time) {rows_sql}"))
    s.commit()
    findings = scan_impossible_dates(s, tables=[tbl])
    assert findings, "D5 应检出不可能日期"
    f = persist_findings(s, findings)[0]
    f.status = FINDING_STATUS_JUDGED
    f.verdict = "real_issue"
    f.verdict_reason = "测试研判结论：日期不可能"
    f.confidence = 0.9
    s.commit()
    rid = s.execute(text(f"SELECT id FROM {tbl} ORDER BY id LIMIT 1")).scalar()
    old = s.execute(text(f"SELECT finish_time FROM {tbl} WHERE id=:i"), {"i": rid}).scalar()
    return f.id, rid, str(old)


def _set_fix_time_suggestion(s: Session, fid: int, updates: list[dict]) -> None:
    f = s.get(GovernanceFinding, fid)
    f.suggestion = {"action": "fix_time", "params": {"updates": updates},
                    "impact": "测试影响面", "risk": "high", "requires_human_confirm": True}
    s.commit()


def test_execute_fix_time_without_confirm_returns_400(admin_client, _engine):
    """验收 2（红线）：fix_time 属 L2，未人工确认 → 400，且**未改任何数据、未建备份表**。"""
    with Session(_engine) as s:
        base_find, base_ev, base_rule = _baselines(s)
        tbl = _uniq("gov_ft_nc")
        try:
            fid, rid, old = _make_date_finding(
                s, tbl, "VALUES ('2099-12-31T10:00:00+08:00', '2026-03-10T10:00:00+08:00')")
            _set_fix_time_suggestion(s, fid, [{"pk": rid, "col": "finish_time",
                                               "old": old, "new": "2026-09-19 12:00:00"}])
            res = admin_client.post(f"/api/v1/governance/findings/{fid}/execute")
            assert res.status_code == 400, res.text
            assert "确认" in res.json()["detail"]
            # 数据未动 + 无备份表
            assert str(s.execute(text(f"SELECT finish_time FROM {tbl} WHERE id=:i"),
                                 {"i": rid}).scalar()) == old
            bak = s.execute(text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name = :t"), {"t": f"gov_bak_{tbl}_{fid}"}).scalar()
            assert bak == 0, "未确认不得建备份表（更不得改数据）"
        finally:
            _cleanup(s, base_find=base_find, base_ev=base_ev, base_rule=base_rule,
                     tables=[tbl])


def test_execute_fix_time_after_confirm_backs_up_and_updates(admin_client, _engine):
    """验收 2/3：确认后执行 —— 备份表存在且行数相等、值已改、凭证含 old/new 对照、finding 终态。"""
    with Session(_engine) as s:
        base_find, base_ev, base_rule = _baselines(s)
        tbl = _uniq("gov_ft_ok")
        bak = f"gov_bak_{tbl}"
        try:
            fid, rid, old = _make_date_finding(
                s, tbl, "VALUES ('2099-12-31T10:00:00+08:00', '2026-03-10T10:00:00+08:00'),"
                        " ('2026-03-11T10:00:00+08:00', '2026-03-10T10:00:00+08:00')")
            _set_fix_time_suggestion(s, fid, [{"pk": rid, "col": "finish_time",
                                               "old": old, "new": "2026-09-19 12:00:00"}])
            c = admin_client.post(f"/api/v1/governance/findings/{fid}/confirm",
                                  json={"note": "已与业务确认该日期为笔误"})
            assert c.status_code == 200, c.text
            assert c.json()["data"]["params_overridden"] is False

            res = admin_client.post(f"/api/v1/governance/findings/{fid}/execute")
            assert res.status_code == 200, res.text
            data = res.json()["data"]
            er = data["execute_result"]
            assert data["auto_level"] == "L2_irreversible"
            assert er["affected_rows"] == 1
            assert er["params_source"] == "ai_suggestion"
            # ★ 备份表存在且行数 == 待改行数
            assert er["backup_table"] == f"{bak}_{fid}"
            assert s.execute(text(f'SELECT count(*) FROM "{er["backup_table"]}"')).scalar() == 1
            assert er["backup_rows"] == 1
            # ★ 凭证含 old/new 对照（old 为库中实际值，new 为改后实际值）
            ch = er["changes"][0]
            assert ch["pk"] == rid and ch["col"] == "finish_time"
            assert "2099-12-31" in ch["old"] and ch["new"].startswith("2026-09-19")
            # 值真的改了
            now_val = str(s.execute(text(f"SELECT finish_time FROM {tbl} WHERE id=:i"),
                                    {"i": rid}).scalar())
            assert now_val.startswith("2026-09-19") and "2099" not in now_val
            # 另一行未被误改
            other = str(s.execute(text(f"SELECT finish_time FROM {tbl} ORDER BY id DESC LIMIT 1")
                                  ).scalar())
            assert other.startswith("2026-03-11")
            # finding 终态 + 凭证落库
            f = s.get(GovernanceFinding, fid)
            assert f.status == FINDING_STATUS_RESOLVED and f.executed_at is not None
            assert f.execute_result["changes"][0]["new"].startswith("2026-09-19")
            s.execute(text(f'DROP TABLE IF EXISTS "{er["backup_table"]}"'))
            s.commit()
        finally:
            _cleanup(s, base_find=base_find, base_ev=base_ev, base_rule=base_rule,
                     tables=[tbl])


def test_execute_fix_time_rejects_stale_old(admin_client, _engine):
    """安全设计②：库中现值与 AI 给的 old 不一致 → 拒绝执行（不按过期证据改数据）。"""
    with Session(_engine) as s:
        base_find, base_ev, base_rule = _baselines(s)
        tbl = _uniq("gov_ft_stale")
        try:
            fid, rid, _ = _make_date_finding(
                s, tbl, "VALUES ('2099-12-31T10:00:00+08:00', '2026-03-10T10:00:00+08:00')")
            _set_fix_time_suggestion(s, fid, [{"pk": rid, "col": "finish_time",
                                               "old": "2020-01-01", "new": "2026-09-19 12:00:00"}])
            admin_client.post(f"/api/v1/governance/findings/{fid}/confirm", json={})
            res = admin_client.post(f"/api/v1/governance/findings/{fid}/execute")
            assert res.status_code == 400, res.text
            assert "不一致" in res.json()["detail"]
            assert str(s.execute(text(f"SELECT finish_time FROM {tbl} WHERE id=:i"),
                                 {"i": rid}).scalar()).startswith("2099")
        finally:
            _cleanup(s, base_find=base_find, base_ev=base_ev, base_rule=base_rule,
                     tables=[tbl])


def test_confirm_params_override_wins_and_suggestion_untouched(admin_client, _engine):
    """验收 2 + 设计 §六 第 4 条：人工确认时可覆盖 AI 的 updates ——
    执行用**人工的**值，凭证记 `confirmed_params`，且 `suggestion`（AI 产物）不被改。"""
    with Session(_engine) as s:
        base_find, base_ev, base_rule = _baselines(s)
        tbl = _uniq("gov_ft_ov")
        try:
            fid, rid, old = _make_date_finding(
                s, tbl, "VALUES ('2099-12-31T10:00:00+08:00', '2026-03-10T10:00:00+08:00')")
            # AI 给的新值（故意是「不想用的」）
            _set_fix_time_suggestion(s, fid, [{"pk": rid, "col": "finish_time",
                                               "old": old, "new": "2030-01-01 00:00:00"}])
            human = {"updates": [{"pk": rid, "col": "finish_time",
                                  "old": old, "new": "2026-05-06 09:00:00"}]}
            c = admin_client.post(f"/api/v1/governance/findings/{fid}/confirm",
                                  json={"note": "人工改正为 5 月 6 日", "params": human})
            assert c.status_code == 200, c.text
            assert c.json()["data"]["params_overridden"] is True

            res = admin_client.post(f"/api/v1/governance/findings/{fid}/execute")
            assert res.status_code == 200, res.text
            er = res.json()["data"]["execute_result"]
            assert er["params_source"] == "human_confirm"
            assert er["confirmed_params"] == human
            # ★ 执行的是人工的值（不是 AI 的 2030-01-01）
            assert er["changes"][0]["new"].startswith("2026-05-06")
            # ★ suggestion 未被改（AI 产物可追溯）
            f = s.get(GovernanceFinding, fid)
            assert f.suggestion["params"]["updates"][0]["new"] == "2030-01-01 00:00:00"
            s.execute(text(f'DROP TABLE IF EXISTS "{er["backup_table"]}"'))
            s.commit()
        finally:
            _cleanup(s, base_find=base_find, base_ev=base_ev, base_rule=base_rule,
                     tables=[tbl])


def test_execute_fix_time_requires_updates_and_detector(admin_client, _engine):
    """无 updates → 拒绝（绝不猜「该改成什么」）；非 impossible_date 探测器 → 拒绝。"""
    with Session(_engine) as s:
        base_find, base_ev, base_rule = _baselines(s)
        tbl = _uniq("gov_ft_noup")
        try:
            fid, rid, _ = _make_date_finding(
                s, tbl, "VALUES ('2099-12-31T10:00:00+08:00', '2026-03-10T10:00:00+08:00')")
            _set_fix_time_suggestion(s, fid, [])
            admin_client.post(f"/api/v1/governance/findings/{fid}/confirm", json={})
            res = admin_client.post(f"/api/v1/governance/findings/{fid}/execute")
            assert res.status_code == 400
            assert "updates" in res.json()["detail"]

            # 非 impossible_date：即使确认也拒绝（判据不同源不得套用改值动作）
            _set_fix_time_suggestion(s, fid, [{"pk": rid, "col": "finish_time",
                                               "old": "x", "new": "2026-01-01"}])
            f = s.get(GovernanceFinding, fid)
            f.detector = "unique_index_dup"
            s.commit()
            res2 = admin_client.post(f"/api/v1/governance/findings/{fid}/execute")
            assert res2.status_code == 400
            assert "impossible_date" in res2.json()["detail"]
        finally:
            _cleanup(s, base_find=base_find, base_ev=base_ev, base_rule=base_rule,
                     tables=[tbl])


def _make_mark_pending_finding(s: Session, table_name: str, samples: list[dict],
                               target: str) -> int:
    """造一条 mark_pending 建议的已研判 finding（执行层动作测试，不必走探测器）。"""
    f = GovernanceFinding(
        detector=DETECTOR_IMPOSSIBLE_DATE, table_name=table_name, target=target,
        severity_raw="warn", metrics={"kinds": ["future"], "counts": {"future": 1}},
        samples=samples, repro_sql="-- 测试用", detected_at=_now_utc(),
        status=FINDING_STATUS_JUDGED, verdict="real_issue",
        verdict_reason="测试：疑似日期笔误但拿不准正确值",
        suggestion={"action": "mark_pending", "params": {}, "impact": "测试",
                    "risk": "medium", "requires_human_confirm": False},
        confidence=0.6,
    )
    s.add(f)
    s.commit()
    s.refresh(f)
    return f.id


def test_mark_pending_works_on_quality_covered_table(admin_client, _engine, monkeypatch):
    """验收 4 前半：QC 覆盖表（manual_refunds）可用；写 `quality_status='pending'`（**仍计入统计**）。"""
    from app.models import ManualRefund
    from app.services import governance_execute_service as GES

    # 跳过宽表重建（manual_refunds 会触发 cash_flow_wide 全量重建，测试无此必要）
    monkeypatch.setattr(GES, "_post_execute", lambda db, t: {"wide_skipped": "test"})

    with Session(_engine) as s:
        base_find, base_ev, base_rule = _baselines(s)
        try:
            m = ManualRefund(order_no=f"GOV-MP-{uuid.uuid4().hex[:8]}", merchant_id="1",
                             refund_amount="19.00", is_refunded="是", registrant="测试员",
                             quality_status=None)
            s.add(m)
            s.commit()
            s.refresh(m)
            fid = _make_mark_pending_finding(
                s, "manual_refunds",
                [{"_id": m.id, "_kind": "future", "column": "op_time", "value": "2099-12-31"}],
                "manual_refunds.op_time")

            # L1 可逆 → 无需确认即可执行
            res = admin_client.post(f"/api/v1/governance/findings/{fid}/execute")
            assert res.status_code == 200, res.text
            data = res.json()["data"]
            assert data["auto_level"] == "L1_reversible"
            er = data["execute_result"]
            assert er["affected_rows"] == 1 and er["marked_ids"] == [m.id]
            assert er["marked_status"] == "pending"
            assert "仍计入经营统计" in er["note"]
            s.expire_all()
            got = s.get(ManualRefund, m.id)
            assert got.quality_status == "pending"
            assert f"finding #{fid}" in (got.quality_note or "")
            s.delete(got)
            s.commit()
        finally:
            _cleanup(s, base_find=base_find, base_ev=base_ev, base_rule=base_rule, tables=[])


def test_mark_pending_rejects_uncovered_table(admin_client, _engine):
    """验收 4 后半：未覆盖表 → **明确报错**（不静默成功、不写任何标记）。"""
    with Session(_engine) as s:
        base_find, base_ev, base_rule = _baselines(s)
        tbl = _uniq("gov_mp_un")
        try:
            fid, rid, _ = _make_date_finding(
                s, tbl, "VALUES ('2099-12-31T10:00:00+08:00', '2026-03-10T10:00:00+08:00')")
            f = s.get(GovernanceFinding, fid)
            f.suggestion = {"action": "mark_pending", "params": {}, "impact": "测试",
                            "risk": "medium", "requires_human_confirm": False}
            f.samples = [{"_id": rid, "_kind": "year_range"}]
            s.commit()
            res = admin_client.post(f"/api/v1/governance/findings/{fid}/execute")
            assert res.status_code == 400, res.text
            assert "未纳入行级质量处置配置" in res.json()["detail"]
            assert s.get(GovernanceFinding, fid).status == FINDING_STATUS_JUDGED
        finally:
            _cleanup(s, base_find=base_find, base_ev=base_ev, base_rule=base_rule, tables=[tbl])


def test_mark_pending_is_l1_reversible_and_not_excluding():
    """`mark_pending` 的级别与口径：L1 可逆（可 restore 回滚），且**不改经营口径**。

    口径差异是本项目的红线（AGENTS.md 绝对禁止第 10 条）：`mark_ignored`/`mark_dup_residual`
    会把行排除出经营统计，`mark_pending` 不会 —— 三者都是 L1，级别相同、口径不同。
    """
    from app.models.governance_rule import (
        AUTO_LEVEL_REVERSIBLE,
        DISPOSITION_LEVELS,
        DISPOSITION_MARK_PENDING,
        disposition_level,
    )
    assert disposition_level(DISPOSITION_MARK_PENDING) == AUTO_LEVEL_REVERSIBLE
    assert DISPOSITION_LEVELS[DISPOSITION_MARK_PENDING] == "L1_reversible"
    # 新探测器进入路由白名单（否则前端下拉/规则校验都不认它）
    assert DETECTOR_IMPOSSIBLE_DATE in gov_router.DETECTORS


def test_rescan_converges_after_date_fixed(admin_client, _engine):
    """验收 3：修正后重扫**不再命中** → 该 finding 走重扫收敛（`auto_closed`）。

    `auto_closed` ≠ `resolved`：前者是「不再命中」，后者才是「已执行动作并核销」
    （见 `close_stale_findings` 的语义边界说明）。
    """
    from app.services.governance_detector import close_stale_findings

    with Session(_engine) as s:
        base_find, base_ev, base_rule = _baselines(s)
        tbl = _uniq("gov_d5_conv")
        pre: list[int] = []
        try:
            fid, rid, _ = _make_date_finding(
                s, tbl, "VALUES ('2099-12-31T10:00:00+08:00', '2026-03-10T10:00:00+08:00')")
            # 记下库中其它 open/judged 的 D5 finding（收敛是全探测器级的，跑完要还原，避免误伤）
            pre = [r.id for r in s.query(GovernanceFinding).filter(
                GovernanceFinding.detector == DETECTOR_IMPOSSIBLE_DATE,
                GovernanceFinding.status.in_(("open", "judged")),
                GovernanceFinding.id != fid).all()]

            # 模拟「已修正」（直接改值，不走 L2 路径 —— 本用例验的是收敛，不是执行）
            s.execute(text(f"UPDATE {tbl} SET finish_time = '2026-05-06T10:00:00+08:00' "
                           f"WHERE id = :i"), {"i": rid})
            s.commit()
            assert scan_impossible_dates(s, tables=[tbl]) == [], "修正后不应再命中"

            closed = close_stale_findings(s, detectors={DETECTOR_IMPOSSIBLE_DATE}, seen_keys=set())
            assert fid in [c.id for c in closed]
            assert s.get(GovernanceFinding, fid).status == "auto_closed"
        finally:
            for i in pre:
                s.get(GovernanceFinding, i).status = "open"
            s.commit()
            _cleanup(s, base_find=base_find, base_ev=base_ev, base_rule=base_rule, tables=[tbl])


def test_judge_accepts_new_actions_and_derives_redline():
    """研判层枚举由分级表**派生**：新增动作自动进入白名单；L2 动作自动进入红线集合。"""
    from app.services import governance_judge_service as J

    assert "mark_pending" in J._VALID_ACTIONS and "fix_time" in J._VALID_ACTIONS
    assert J._IRREVERSIBLE_ACTIONS == {"delete_dups", "create_index", "fix_time"}


def test_spec_rules_injected_by_detector():
    """判据段按 detector 注入（设计 §五）：只有命中该探测器时才注入其专用段。"""
    from app.services.governance_judge_service import build_spec_rules

    base = build_spec_rules()
    assert "不可能日期" not in base and "文本异常" not in base
    d5 = build_spec_rules({DETECTOR_IMPOSSIBLE_DATE})
    assert "不可能日期" in d5 and "禁止编造正确值" in d5 and "文本异常" not in d5
    both = build_spec_rules({DETECTOR_IMPOSSIBLE_DATE, "text_anomaly"})
    assert "不可能日期" in both and "文本异常" in both
    assert "7)" in both and "8)" in both, "多探测器同时命中时编号不得重号"


def test_default_prompt_renders_without_brace_error():
    """★回归：默认提示词里的**字面花括号必须转义**（`{{ }}`）。

    它会被 `str.format` 渲染；写成单个 `{` 会让 format 抛错 → `_safe_format` 静默退回
    **未替换**的模板 → AI 拿不到判据段（`{spec_rules}` 原样出现在提示词里）。
    2026-09-20 E2 加 `[{pk, col, old, new}]` 时实测踩过：既有测试只断言
    「去重键红线 in content」才把它暴露出来。
    """
    from app.services.governance_judge_service import (
        _DEFAULT_SYSTEM_PROMPT,
        _OUTPUT_SCHEMA,
        build_spec_rules,
    )
    out = _DEFAULT_SYSTEM_PROMPT.format(spec_rules=build_spec_rules(),
                                       output_schema=_OUTPUT_SCHEMA)
    assert "{spec_rules}" not in out and "{output_schema}" not in out
    assert "去重键红线" in out and "不可能日期" not in out
    assert "[{pk, col, old, new}]" in out, "转义后应渲染为单花括号形态"

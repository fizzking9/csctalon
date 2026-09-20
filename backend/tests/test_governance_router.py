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
    FINDING_STATUS_JUDGED,
    FINDING_STATUS_RESOLVED,
    GovernanceFinding,
)
from app.models.user import RoleEnum, User
from app.services.governance_detector import persist_findings, scan_unique_index_dups

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
                     tables=[tbl, bak])


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

# -*- coding: utf-8 -*-
"""TR-4.1 / TR-4.2 采纳人工覆盖 API (#332 T4)

覆盖点:
- 无权限调用 → 401;
- 提交覆盖 + rerun → ods.adopt_state 按人工目标刷新、adopt_reason 仍记机器码、
  std 表成员资格翻转、override 留痕(operator/reason/created_at)完整、审计事件可查;
- 重复提交同 (route,src_pk) → 更新而非重复行; 非法枚举 → 422。

夹具说明: API 与测试共用 db fixture session(经 dependency_overrides),
作业/审计内部 commit 会落测试库, 故主键统一带 OV 前缀避免跨用例干扰。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.user import RoleEnum, User

_TS = "2026-10-16 10:00:00+08"


@pytest.fixture()
def admin_client(db):
    """带 admin 身份的 TestClient(与测试共用同一 db session)。"""
    fake = User(id=1, username="admin_t4", role=RoleEnum.ADMIN.value, is_active=True)

    def _override_db():
        yield db

    app.dependency_overrides[get_current_user] = lambda: fake
    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _seed_c_row(db) -> None:
    """C 路一行: status=已驳回 → 机器规则应判 excluded。
    防御性清理: API rerun 内部 commit 会落持久测试库, 先删自身键保证可重跑。"""
    db.execute(text("DELETE FROM ods.shanmao_refunds WHERE refund_no = 'RC-OV1'"))
    db.execute(text("DELETE FROM adoption_overrides WHERE src_pk LIKE 'R_-OV%'"))
    db.execute(text("""
        INSERT INTO ods.shanmao_refunds
            (refund_no, status, actual_amount, refund_amount, refund_time)
        VALUES ('RC-OV1', '已驳回', 19.00, 19.00, :ts)
    """), {"ts": _TS})
    db.flush()


def _c_state(db, pk: str = "RC-OV1"):
    return db.execute(text(
        "SELECT adopt_state, adopt_reason FROM ods.shanmao_refunds WHERE refund_no = :p"
    ), {"p": pk}).one()


def _c_in_std(db, pk: str = "RC-OV1") -> int:
    return db.execute(text(
        "SELECT count(*) FROM std_refund_shanmao WHERE refund_no = :p"
    ), {"p": pk}).scalar()


# ── TR-4.1 无权限 → 401 ─────────────────────────────────────────────────────
def test_override_requires_admin(client):
    r = client.post("/api/v1/admin/refund-adoption/overrides", json={
        "route": "C", "src_pk": "RC-OV1", "target_state": "adopted", "reason": "无权限测试",
    })
    assert r.status_code in (401, 403)


# ── TR-4.1 有权限写入 + 重跑: 人工状态生效、机器码保留、std 翻转、留痕完整 ──
def test_override_submit_and_rerun(db, admin_client):
    _seed_c_row(db)

    # 基线: 机器采纳 → excluded, 不进 std
    from app.services.governance.refund_adoption import run_refund_adoption
    run_refund_adoption(db, routes="C", commit=False)
    st, reason = _c_state(db)
    assert (st, reason) == ("excluded", "C_EX_NOT_REFUNDED")
    assert _c_in_std(db) == 0

    # 提交人工覆盖: excluded → adopted, 并立即重跑该路
    r = admin_client.post("/api/v1/admin/refund-adoption/overrides", json={
        "route": "C", "src_pk": "RC-OV1", "target_state": "adopted",
        "reason": "业务确认: 该行线下已实际退款, 普查后补采纳(凭证#332)",
        "rerun": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0
    ov = body["data"]["override"]
    assert ov["route"] == "C" and ov["src_pk"] == "RC-OV1"
    assert ov["target_state"] == "adopted"
    assert ov["operator"] == "admin_t4" and ov["reason"].startswith("业务确认")
    assert ov["created_at"]  # 留痕时间完整
    assert body["data"]["rerun"]["routes"]["C"]["adopted"] >= 1

    # 重跑后: adopt_state=人工目标; adopt_reason 仍机器码(对照用); 进入 std
    st, reason = _c_state(db)
    assert st == "adopted"
    assert reason == "C_EX_NOT_REFUNDED"  # 机器结论不被人工覆盖
    assert _c_in_std(db) == 1

    # 审计事件可查
    ev = db.execute(text("""
        SELECT action, title, status FROM system_events
        WHERE category = 'governance' AND action = 'adoption_override'
          AND title LIKE '%RC-OV1%'
        ORDER BY id DESC LIMIT 1
    """)).one()
    assert ev.status == "success" and "adopted" in ev.title


# ── TR-4.2 重复提交 = 更新(不产生重复生效行) ────────────────────────────────
def test_override_upsert_not_duplicate(db, admin_client):
    for target in ("excluded", "adopted"):
        r = admin_client.post("/api/v1/admin/refund-adoption/overrides", json={
            "route": "C", "src_pk": "RC-OV2", "target_state": target,
            "reason": f"两次提交之 {target}",
        })
        assert r.status_code == 200, r.text
        assert r.json()["data"]["override"]["target_state"] == target
    rows = db.execute(text("""
        SELECT target_state, reason FROM adoption_overrides
        WHERE route = 'C' AND src_pk = 'RC-OV2'
    """)).all()
    assert len(rows) == 1  # 唯一键生效: 更新而非第二条
    assert rows[0].target_state == "adopted"
    assert "adopted" in rows[0].reason


# ── TR-4.2 非法枚举 → 422 ───────────────────────────────────────────────────
@pytest.mark.parametrize("payload", [
    {"route": "Z", "src_pk": "X1", "target_state": "adopted", "reason": "非法路别"},
    {"route": "C", "src_pk": "X1", "target_state": "keep", "reason": "非法三态"},
    {"route": "C", "src_pk": "X1", "target_state": "adopted", "reason": "   "},
    {"route": "C", "src_pk": "", "target_state": "adopted", "reason": "空主键"},
])
def test_override_invalid_enum_422(db, admin_client, payload):
    r = admin_client.post("/api/v1/admin/refund-adoption/overrides", json=payload)
    assert r.status_code == 422, r.text


# ── 列表与撤销(一期最小实现) ────────────────────────────────────────────────
def test_override_list_and_revoke(db, admin_client):
    r = admin_client.post("/api/v1/admin/refund-adoption/overrides", json={
        "route": "A", "src_pk": "RA-OV9", "target_state": "excluded",
        "reason": "列表/撤销用例",
    })
    ov_id = r.json()["data"]["override"]["id"]

    r = admin_client.get("/api/v1/admin/refund-adoption/overrides?route=A")
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    assert any(i["id"] == ov_id for i in items)

    r = admin_client.delete(f"/api/v1/admin/refund-adoption/overrides/{ov_id}")
    assert r.status_code == 200
    assert db.execute(text(
        "SELECT count(*) FROM adoption_overrides WHERE id = :i"
    ), {"i": ov_id}).scalar() == 0
    # 撤销审计可查(持久测试库可能含上轮残留事件, 故不断言精确条数)
    n = db.execute(text("""
        SELECT count(*) FROM system_events
        WHERE category = 'governance' AND action = 'adoption_override_revoke'
          AND title LIKE '%RA-OV9%'
    """)).scalar()
    assert n >= 1

    # 撤销不存在的 id → 404
    r = admin_client.delete(f"/api/v1/admin/refund-adoption/overrides/{ov_id}")
    assert r.status_code == 404

# -*- coding: utf-8 -*-
"""B4-5 (#261) 统一变更审计: audit_changes 表 + record_change + persons 接入 + 保留期夜批"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.audit_change import AuditChange
from app.models.person import Person
from app.models.user import RoleEnum, User


@pytest.fixture()
def authed_client(db):
    """管理员鉴权客户端(db fixture 共享事务; API commit 真实落测试库, 前后清审计表)"""
    fake = User(id=1, username="tester", role=RoleEnum.ADMIN.value, is_active=True)

    def _override_user():
        return fake

    def _override_db():
        yield db

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    db.execute(delete(AuditChange))
    db.commit()
    try:
        with TestClient(app) as c:
            yield c
    finally:
        db.execute(delete(AuditChange))
        db.commit()
        app.dependency_overrides.clear()


def _mk_person(db) -> Person:
    # Person 主显示名是 display_name(无 name 字段, #261 修正)
    p = Person(display_name=f"审计测试-{uuid4().hex[:6]}", is_active=True)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _cleanup_persons(db, p: Person) -> None:
    db.delete(p)
    db.commit()


def test_diff_model_changes_only_changed_fields(db):
    """diff 只输出实际变化的字段; date/None 转 JSONB 可存形态。"""
    from app.services.audit_change import diff_model_changes

    p = _mk_person(db)
    try:
        diffs = diff_model_changes(p, {"display_name": p.display_name, "is_active": False})  # display_name 无变化
        assert "display_name" not in diffs
        assert diffs["is_active"] == [True, False]
    finally:
        _cleanup_persons(db, p)


def test_record_change_persists_row(db):
    """record_change 落库: 字段级 before/after + actor 冗余 + 缺省摘要。"""
    from app.services.audit_change import record_change

    record_change(
        db, entity_type="persons", entity_id=42, action="update",
        changes={"is_active": [True, False]}, actor=None, source="api",
    )
    row = db.scalar(
        select(AuditChange).where(AuditChange.entity_type == "persons", AuditChange.entity_id == "42")
    )
    assert row is not None
    assert row.changes == {"is_active": [True, False]}
    assert row.action == "update"
    assert "is_active" in row.summary
    # 独立事务已提交 → 用新会话验证持久化
    from app.core.database import SessionLocal
    s2 = SessionLocal()
    try:
        assert s2.get(AuditChange, row.id) is not None
    finally:
        s2.close()


def test_person_update_writes_audit(db, authed_client):
    """e2e: PUT /persons/{id} 字段级变更落 audit_changes(同 actor, source=api)。"""
    p = _mk_person(db)
    try:
        new_name = f"改名-{uuid4().hex[:4]}"
        r = authed_client.put(
            f"/api/v1/persons/{p.id}",
            json={"display_name": new_name, "remark": "#261 审计接入测试"},
        )
        assert r.status_code == 200
        row = db.scalar(
            select(AuditChange).where(
                AuditChange.entity_type == "persons", AuditChange.entity_id == str(p.id)
            )
        )
        assert row is not None
        assert row.action == "update"
        assert row.actor_name == "tester"
        assert set(row.changes) == {"display_name", "remark"}
        assert row.changes["display_name"][1] == new_name
    finally:
        _cleanup_persons(db, p)


def test_purge_expired_logs_respects_retention(db):
    """夜批 purge_expired_logs: 保留期内不删, 超期删除; detail 报告 cutoff/行数。"""
    from app.core.config import settings
    from app.models.operation_log import OperationLog
    from app.services.batch_runner import _purge_expired_logs

    db.execute(delete(AuditChange))
    db.execute(delete(OperationLog))
    db.commit()
    fresh = OperationLog(method="PUT", path="/x", action="t.fresh")
    stale = OperationLog(method="PUT", path="/x", action="t.stale")
    db.add_all([fresh, stale])
    db.commit()
    # 直改 created_at 造超期行(绕 server_default)
    stale_created = datetime.now(timezone.utc) - timedelta(
        days=settings.OPERATION_LOG_RETENTION_DAYS + 5
    )
    db.execute(
        OperationLog.__table__.update()
        .where(OperationLog.id == stale.id)
        .values(created_at=stale_created)
    )
    db.commit()

    # purge 内部 commit 会 expire 实例, 已删行再访问属性会抛 ObjectDeletedError → 先缓存 id
    fresh_id, stale_id = fresh.id, stale.id

    detail = _purge_expired_logs(db)
    assert detail["operation_logs"]["deleted"] == 1
    assert db.scalar(select(OperationLog.id).where(OperationLog.id == fresh_id)) is not None
    assert db.scalar(select(OperationLog.id).where(OperationLog.id == stale_id)) is None
    # audit_changes 分支空删不报错
    assert detail["audit_changes"]["deleted"] == 0
    db.execute(delete(OperationLog))
    db.commit()

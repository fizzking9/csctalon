# -*- coding: utf-8 -*-
"""AI 数据接入助手·路由接口测试（工单 #157）

对应设计 §七 接口契约 + §八 验收第 5 条（confirm/apply 流程）与第 2 条
（apply 只产 JSON，测试断言未写业务库、未改代码）。

覆盖面：
  1. 起草异步编排：POST → status=drafting（code=NULL）+ draft_id；LLM 完成后 status=draft。
  2. 权限：admin 看全部/普通用户只见自己的（越权 403）；confirm/apply 仅 admin。
  3. 人工编辑 PUT → 重跑校验；已确认草稿不可编辑。
  4. confirm：fail 未清零拒绝；warn 未逐条处置拒绝；全通过 → confirmed + issue_no。
  5. apply：生成落地包（含 registry_seed/field_overrides/cache_keys），status=applied，
     且**未变更 data_sources / data_source_fields / 任何代码文件**（断言）。
  6. 列表筛选、registry-snapshot 快照。

带 LLM 环节的起草（POST）用 monkeypatch 假 LLM（复用 #156 手法）；confirm/apply/
编辑/列表为纯编排，走真实校验器与落库。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.data_source import DataSource, DataSourceField
from app.models.data_source_spec import (
    SPEC_STATUS_APPLIED,
    SPEC_STATUS_CONFIRMED,
    SPEC_STATUS_DRAFT,
    DataSourceSpec,
)
from app.models.system_event import SystemEvent
from app.models.user import RoleEnum, User
from app.services import llm_client
from tests.test_onboarding_draft_service import (
    _cleanup,
    _llm_output,
    _samples,
    _unique_code,
)

# ─────────────────────────────────────────────────────────────────────
# Fixtures：admin / 普通用户两个 TestClient（override 认证与 db）
# ─────────────────────────────────────────────────────────────────────
# 测试用固定用户 id（远离真实数据，且 data_source_specs.created_by 有 FK 到 users，
# 必须落真实用户行，否则 IntegrityError）
_ADMIN_ID = 9001
_USER_ID = 9002


@pytest.fixture(scope="module")
def _onb_users(_engine):
    """建两个持久化测试用户（admin + 普通），用例结束删除。"""
    from sqlalchemy.orm import Session

    s = Session(_engine)
    created: list[int] = []
    for uid, role in ((_ADMIN_ID, RoleEnum.ADMIN.value), (_USER_ID, RoleEnum.SPECIALIST.value)):
        if s.get(User, uid) is None:
            s.add(User(id=uid, username=f"onb_{uid}", display_name=f"onb{uid}",
                       hashed_password="x", role=role, is_active=True))
            created.append(uid)
    s.commit()
    yield
    for uid in created:
        row = s.get(User, uid)
        if row is not None:
            s.delete(row)
    s.commit()
    s.close()


class _ClientCtx:
    """按用户角色造 TestClient：override get_current_user/get_db。"""

    def __init__(self, engine, user_id, role):
        self._ctx = None
        self.client = None
        self.user = User(id=user_id, username=f"u{user_id}", role=role, is_active=True)
        self._engine = engine

    def __enter__(self):
        def _override_user():
            return self.user

        def _override_db():
            from sqlalchemy.orm import Session
            session = Session(self._engine, autocommit=False, autoflush=False)
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_current_user] = _override_user
        app.dependency_overrides[get_db] = _override_db
        self._ctx = self
        self.client = TestClient(app)
        return self.client

    def __exit__(self, *exc):
        app.dependency_overrides.clear()
        self.client = None
        self._ctx = None


@pytest.fixture()
def admin_client(_engine, _onb_users):
    with _ClientCtx(_engine, user_id=_ADMIN_ID, role=RoleEnum.ADMIN.value) as c:
        yield c


@pytest.fixture()
def user_client(_engine, _onb_users):
    with _ClientCtx(_engine, user_id=_USER_ID, role=RoleEnum.SPECIALIST.value) as c:
        yield c


def _app_code_fingerprint() -> tuple:
    """backend/app 下 .py 文件的 (相对路径, 大小, mtime) 指纹。

    用于 apply 的「未改任何代码」断言（验收第 2 条）—— 落地包只产 JSON，
    不应触碰任何源码文件。
    """
    root = Path(__file__).resolve().parents[1] / "app"
    return tuple(
        sorted(
            (str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime)
            for p in root.rglob("*.py")
        )
    )


def _cleanup_ids(db, *ids: int) -> None:
    """按 id 清理草稿行（占位行 code=NULL，按 code 删不到）。"""
    for sid in ids:
        row = db.get(DataSourceSpec, sid)
        if row is not None:
            db.delete(row)
    db.commit()


def _warn_resolutions_all(client, spec_id: int) -> dict:
    """先触发一次校验拿到 warn 列表，为每条生成处置理由。

    warn 条数由规则实际决定（R9/R10/R12/R14 都可能命中），测试不硬编码下标 ——
    否则新增一条规则就会让用例误红。
    """
    r = client.put(f"/api/v1/ai-onboarding/drafts/{spec_id}", json={})
    warns = (r.json()["data"]["validator_report"] or {}).get("warn") or []
    return {str(i): f"已人工核对({w['rule']})并确认可放行" for i, w in enumerate(warns)}


def _make_draft_row(db, *, code, status=SPEC_STATUS_DRAFT, created_by=_ADMIN_ID, name_cn=None,
                    **overrides) -> DataSourceSpec:
    """直接落一条草稿行（跳过 LLM），供 confirm/apply/编辑/权限类用例。"""
    spec = DataSourceSpec(
        code=code,
        table_name=overrides.get("table_name", f"{code}_tbl"),
        name_cn=name_cn or "订单·接入测试台账",
        category=overrides.get("category", "交易与售后"),
        sort_order=overrides.get("sort_order", 10),
        time_field=overrides.get("time_field", "create_date"),
        sn_field=overrides.get("sn_field", "device_sn"),
        pk_field=overrides.get("pk_field", "order_no"),
        pk_strategy=overrides.get("pk_strategy", "primary_key"),
        sync_source=overrides.get("sync_source", "bms"),
        description=overrides.get("description", "BMS 支付管理订单台账"),
        status=status,
        issue_no=overrides.get("issue_no"),
        field_specs=overrides.get(
            "field_specs",
            [
                {"field_name": "order_no", "name_cn": "订单号", "data_type": "string",
                 "semantic_role": "dimension", "agg_func": None, "is_filterable": True,
                 "is_pk": True, "confidence": 0.98},
                {"field_name": "amount", "name_cn": "金额", "data_type": "numeric",
                 "semantic_role": "amount", "agg_func": "sum", "is_filterable": False,
                 "is_pk": False, "unit": "分", "confidence": 0.96},
            ],
        ),
        dedup_recommendation=overrides.get(
            "dedup_recommendation",
            {"strategy": "primary_key", "fields": ["order_no"],
             "rationale": "订单号唯一", "requires_human_confirm": True},
        ),
        created_by=created_by,
    )
    db.add(spec)
    db.commit()
    db.refresh(spec)
    return spec


# ─────────────────────────────────────────────────────────────────────
# 起草异步编排（POST 202 + 轮询）
# ─────────────────────────────────────────────────────────────────────
def test_create_draft_async_then_ready(admin_client, db, monkeypatch):
    """POST → 202 + draft_id（占位行 drafting）→ 后台任务完成后轮询到 draft。

    ★ 同时验证「异步 202 + 轮询」契约：POST 不等 LLM，前端靠 GET 轮询状态。
    """
    import time

    code = _unique_code()

    async def _fake(*_a, **_kw):
        return json.dumps(_llm_output(code))

    monkeypatch.setattr(llm_client, "chat_completion", _fake)
    payload = {"table_hint": f"{code}_tbl", "business_note": "测试台账",
               "samples": _samples(3)}
    r = admin_client.post("/api/v1/ai-onboarding/drafts", json=payload)
    assert r.status_code == 202, r.text
    draft_id = r.json()["data"]["draft_id"]
    row = db.get(DataSourceSpec, draft_id)
    assert row is not None
    assert row.table_name == f"{code}_tbl"

    # 轮询直到后台起草完成（TestClient 的 portal loop 会在请求间推进后台任务）
    final = None
    for _ in range(60):
        db.expire_all()
        final = admin_client.get(f"/api/v1/ai-onboarding/drafts/{draft_id}").json()["data"]
        if final["status"] in (SPEC_STATUS_DRAFT, "failed"):
            break
        time.sleep(0.1)
    assert final is not None
    assert final["status"] == SPEC_STATUS_DRAFT, final.get("error_message")
    assert final["code"] == code  # AI 建议 code 已写入
    assert final["validator_report"] is not None
    _cleanup(db, code)


def test_create_draft_rejects_bad_samples(admin_client):
    """空样例 / 非对象行 → 400，不建行。"""
    r = admin_client.post("/api/v1/ai-onboarding/drafts", json={"samples": []})
    assert r.status_code == 400
    r2 = admin_client.post("/api/v1/ai-onboarding/drafts",
                           json={"samples": [[1, 2], {"a": 1}]})
    assert r2.status_code == 400


def test_draft_too_many_rows(admin_client):
    """>50 行样例 → 400。"""
    r = admin_client.post("/api/v1/ai-onboarding/drafts",
                          json={"samples": _samples(51)})
    assert r.status_code == 400


def test_background_task_marks_failed_on_llm_error(db, _engine, monkeypatch):
    """后台起草任务异常 → 占位行被兜底置 failed + error_message（不留 drafting 僵尸行）。"""
    import asyncio

    from sqlalchemy.orm import Session

    from app.services.onboarding_draft_service import (
        create_draft_placeholder,
        start_draft_task,
    )

    async def _boom(*_a, **_kw):
        raise RuntimeError("llm down")

    monkeypatch.setattr(llm_client, "chat_completion", _boom)
    spec = create_draft_placeholder(
        db, table_hint="t_onb_fail_tbl", samples=_samples(1), created_by=_ADMIN_ID,
    )

    async def _drive():
        start_draft_task(
            session_factory=lambda: Session(_engine), spec_id=spec.id,
            table_hint="t_onb_fail_tbl", samples=_samples(1), created_by=_ADMIN_ID,
        )
        for _ in range(60):  # 等后台任务收尾（失败路径含重试）
            await asyncio.sleep(0.05)
            s = Session(_engine)
            try:
                st = s.get(DataSourceSpec, spec.id).status
            finally:
                s.close()
            if st != "drafting":
                return

    asyncio.run(_drive())
    db.expire_all()
    row = db.get(DataSourceSpec, spec.id)
    try:
        assert row.status == "failed", f"实际 {row.status}"
        assert "llm down" in (row.error_message or "")
    finally:
        _cleanup_ids(db, spec.id)


# ─────────────────────────────────────────────────────────────────────
# 列表 / 详情 / 权限
# ─────────────────────────────────────────────────────────────────────
def test_list_drafts_admin_sees_all(admin_client, db):
    """admin 看全部草稿（含他人创建的）。"""
    a = _make_draft_row(db, code=_unique_code(), created_by=_ADMIN_ID)
    b = _make_draft_row(db, code=_unique_code(), created_by=_USER_ID)
    try:
        ra = admin_client.get("/api/v1/ai-onboarding/drafts").json()["data"]
        admin_codes = {i["code"] for i in ra["items"]}
        assert {a.code, b.code} <= admin_codes
    finally:
        _cleanup(db, a.code, b.code)


def test_list_drafts_user_sees_only_own(user_client, db):
    """普通用户只见自己创建的草稿（他人创建的不出现）。"""
    a = _make_draft_row(db, code=_unique_code(), created_by=_ADMIN_ID)
    b = _make_draft_row(db, code=_unique_code(), created_by=_USER_ID)
    try:
        ru = user_client.get("/api/v1/ai-onboarding/drafts").json()["data"]
        user_codes = {i["code"] for i in ru["items"]}
        assert b.code in user_codes
        assert a.code not in user_codes
    finally:
        _cleanup(db, a.code, b.code)


def test_list_drafts_filter_by_status(user_client, db):
    code = _unique_code()
    spec = _make_draft_row(db, code=code, status=SPEC_STATUS_CONFIRMED, created_by=_USER_ID)
    try:
        r = user_client.get("/api/v1/ai-onboarding/drafts",
                            params={"status": SPEC_STATUS_CONFIRMED}).json()["data"]
        assert {i["code"] for i in r["items"]} == {code}
        r2 = user_client.get("/api/v1/ai-onboarding/drafts",
                             params={"status": SPEC_STATUS_DRAFT}).json()["data"]
        assert code not in {i["code"] for i in r2["items"]}
    finally:
        _cleanup(db, spec.code)


def test_get_draft_cross_owner_forbidden(user_client, db):
    """普通用户 A 看 B 的草稿 → 403。"""
    code = _unique_code()
    spec = _make_draft_row(db, code=code, created_by=_ADMIN_ID)  # 非当前用户创建
    try:
        r = user_client.get(f"/api/v1/ai-onboarding/drafts/{spec.id}")
        assert r.status_code == 403
    finally:
        _cleanup(db, spec.code)


def test_registry_snapshot_exposes_rules(user_client):
    """快照含前缀白名单/角色枚举/分组顺序，供前端边填边提示。"""
    data = user_client.get("/api/v1/ai-onboarding/registry-snapshot").json()["data"]
    assert "allowed_prefixes" in data and "订单" in data["allowed_prefixes"]
    assert "valid_semantic_roles" in data and "amount" in data["valid_semantic_roles"]
    assert "category_order" in data


def test_confirm_and_apply_require_admin(user_client, db):
    """普通用户调 confirm/apply → 403（设计 §七：confirm/apply 仅 admin）。"""
    code = _unique_code()
    spec = _make_draft_row(db, code=code, created_by=_USER_ID)
    try:
        assert user_client.post(
            f"/api/v1/ai-onboarding/drafts/{spec.id}/confirm", json={}
        ).status_code == 403
        assert user_client.post(
            f"/api/v1/ai-onboarding/drafts/{spec.id}/apply"
        ).status_code == 403
    finally:
        _cleanup(db, code)


# ─────────────────────────────────────────────────────────────────────
# 人工编辑（PUT → 重跑校验）
# ─────────────────────────────────────────────────────────────────────
def test_update_draft_reruns_validation_and_rejects_confirmed(user_client, db):
    code = _unique_code()
    spec = _make_draft_row(db, code=code, created_by=_USER_ID)
    try:
        # 编辑 name_cn 触发校验（改后重跑，报告仍可确认）
        r = user_client.put(f"/api/v1/ai-onboarding/drafts/{spec.id}",
                            json={"name_cn": "订单·接入测试台账2"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["name_cn"] == "订单·接入测试台账2"

        # 已确认草稿不可编辑
        spec.status = SPEC_STATUS_CONFIRMED
        db.commit()
        r2 = user_client.put(f"/api/v1/ai-onboarding/drafts/{spec.id}",
                             json={"name_cn": "订单·改"})
        assert r2.status_code == 400
    finally:
        _cleanup(db, code)


def test_update_draft_code_clash_rejected(user_client, db):
    code = _unique_code()
    other = _make_draft_row(db, code=code, created_by=_USER_ID)
    c2 = _unique_code()
    spec2 = _make_draft_row(db, code=c2, created_by=_USER_ID)
    try:
        # 把 spec2 改成 other 的 code → 冲突 400
        r = user_client.put(f"/api/v1/ai-onboarding/drafts/{spec2.id}",
                            json={"code": other.code})
        assert r.status_code == 400
    finally:
        _cleanup(db, code, c2)


# ─────────────────────────────────────────────────────────────────────
# confirm（fail 清零 + warn 逐条处置）
# ─────────────────────────────────────────────────────────────────────
def test_confirm_rejects_when_fail_present(admin_client, db):
    code = _unique_code()
    spec = _make_draft_row(db, code=code, name_cn="非法前缀·无前缀名")  # R4 fail
    try:
        r = admin_client.post(f"/api/v1/ai-onboarding/drafts/{spec.id}/confirm", json={})
        assert r.status_code == 400
        assert "fail" in r.json()["detail"]
        db.refresh(spec)
        assert spec.status != SPEC_STATUS_CONFIRMED
    finally:
        _cleanup(db, code)


def test_confirm_requires_warn_resolutions(admin_client, db):
    """存在 warn 且未逐条处置 → 拒绝。"""
    code = _unique_code()
    spec = _make_draft_row(db, code=code)
    try:
        # 强制造一条 warn: 加一个低置信字段触发 R12
        spec.field_specs = [
            {"field_name": "order_no", "name_cn": "订单号", "data_type": "string",
             "semantic_role": "dimension", "confidence": 0.5},
        ]
        db.commit()
        r = admin_client.post(f"/api/v1/ai-onboarding/drafts/{spec.id}/confirm", json={})
        assert r.status_code == 400
        assert "warn" in r.json()["detail"]
    finally:
        _cleanup(db, code)


def test_confirm_success_sets_confirmed_and_issue(admin_client, db):
    """fail=0 + warn 全处置 → confirmed + issue_no。"""
    code = _unique_code()
    spec = _make_draft_row(db, code=code)
    # 构造一条 warn（低置信 R12）并在 confirm 时处置
    spec.field_specs = [
        {"field_name": "order_no", "name_cn": "订单号", "data_type": "string",
         "semantic_role": "dimension", "confidence": 0.95},
        {"field_name": "best_guess", "name_cn": "拿不准列", "data_type": "string",
         "semantic_role": "dimension", "confidence": 0.5},  # R12 warn
    ]
    db.commit()
    try:
        resolutions = _warn_resolutions_all(admin_client, spec.id)
        assert resolutions  # 该样例至少有 R12 低置信 warn
        r = admin_client.post(
            f"/api/v1/ai-onboarding/drafts/{spec.id}/confirm",
            json={"issue_no": "T1", "warn_resolutions": resolutions},
        )
        assert r.status_code == 200, r.text
        # client 用自己的 session commit；db fixture 的 identity map 需先失效再重读
        db.expire_all()
        row = db.get(DataSourceSpec, spec.id)
        assert row.status == SPEC_STATUS_CONFIRMED
        assert row.issue_no == "T1"
        assert row.validator_report["warn_resolutions"]
        # 留痕
        ev = db.execute(
            select(SystemEvent).where(SystemEvent.action == "onboarding_confirm")
            .order_by(SystemEvent.id.desc()).limit(1)
        ).scalars().first()
        assert ev is not None
    finally:
        _cleanup(db, code)


# ─────────────────────────────────────────────────────────────────────
# apply（落地包生成，不写业务库不改码）
# ─────────────────────────────────────────────────────────────────────
def test_apply_requires_confirmed_first(admin_client, db):
    code = _unique_code()
    spec = _make_draft_row(db, code=code, status=SPEC_STATUS_DRAFT)
    try:
        r = admin_client.post(f"/api/v1/ai-onboarding/drafts/{spec.id}/apply")
        assert r.status_code == 400
        assert "confirm" in r.json()["detail"]
    finally:
        _cleanup(db, code)


def test_apply_generates_package_without_writing_biz(admin_client, db):
    """confirm + apply 全流程：落地包含 registry_seed/field_overrides/cache_keys，
    status=applied，且 data_sources / data_source_fields 未新增记录（未写业务库）。"""
    code = _unique_code()
    spec = _make_draft_row(db, code=code, created_by=_USER_ID)
    spec.field_specs = [
        {"field_name": "order_no", "name_cn": "订单号", "data_type": "string",
         "semantic_role": "dimension", "agg_func": None, "confidence": 0.98},
        {"field_name": "amount", "name_cn": "金额", "data_type": "numeric",
         "semantic_role": "amount", "agg_func": "sum", "unit": "分", "confidence": 0.96},
    ]
    db.commit()

    src_count = db.execute(select(DataSource)).scalars().all()
    fld_count = db.execute(select(DataSourceField)).scalars().all()
    # 代码文件指纹（验收第 2 条：apply 未改任何代码文件）
    code_fp = _app_code_fingerprint()
    try:
        rc = admin_client.post(
            f"/api/v1/ai-onboarding/drafts/{spec.id}/confirm",
            json={"warn_resolutions": _warn_resolutions_all(admin_client, spec.id)},
        )
        assert rc.status_code == 200, rc.text

        ra = admin_client.post(f"/api/v1/ai-onboarding/drafts/{spec.id}/apply")
        assert ra.status_code == 200, ra.text
        pkg = ra.json()["data"]["apply_package"]
        assert pkg["registry_seed"]["code"] == code
        assert pkg["field_overrides"] and pkg["field_overrides"][0]["field"] == "order_no"
        assert pkg["cache_keys"]  # 数据源接入缓存铁律
        assert "CREATE TABLE" in pkg["migration"]

        db.expire_all()
        row = db.get(DataSourceSpec, spec.id)
        assert row.status == SPEC_STATUS_APPLIED
        assert row.apply_package and row.apply_package["registry_seed"]["code"] == code

        # ★ 验收第 2 条断言：未写任何业务表（data_sources / data_source_fields 行数不变）
        assert len(db.execute(select(DataSource)).scalars().all()) == len(src_count)
        assert len(db.execute(select(DataSourceField)).scalars().all()) == len(fld_count)
        # ★ 未建表（落地包只是建议文本）
        tbl_exists = db.execute(
            text("SELECT count(*) FROM information_schema.tables WHERE table_name = :t"),
            {"t": row.table_name},
        ).scalar()
        assert tbl_exists == 0
        # ★ 未改任何代码文件
        assert _app_code_fingerprint() == code_fp
    finally:
        _cleanup(db, code)

# -*- coding: utf-8 -*-
"""B4-1 (#257) 指标字典: metric_dictionary 表 + /metrics/dict CRUD + 种子校验"""

import importlib.util
import pathlib
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.metric import MetricDictionary
from app.models.user import RoleEnum, User


@pytest.fixture()
def authed_client(db):
    """带鉴权的 TestClient(管理员): 覆盖 get_current_user + get_db, 与 db fixture 共享事务。

    注意: API 内 db.commit() 会真实落测试库(外层 rollback 救不回),
    字典表在 fixture 前后各清一次, 防止用例间/历史脏数据串扰。
    测试库由 create_all 建表(无迁移), 种子不在 —— 种子校验单独走迁移文件 import。
    """
    fake = User(id=1, username="tester", role=RoleEnum.ADMIN.value, is_active=True)

    def _override_user():
        return fake

    def _override_db():
        yield db

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    db.execute(delete(MetricDictionary))
    db.commit()
    try:
        with TestClient(app) as c:
            yield c
    finally:
        db.execute(delete(MetricDictionary))
        db.commit()
        app.dependency_overrides.clear()


def _payload(**kw) -> dict:
    p = {
        "code": f"test.{_uuid.uuid4().hex[:8]}",
        "name_cn": f"测试指标-{_uuid.uuid4().hex[:6]}",
        "definition": "测试口径",
        "category": "reception",
        "unit": "次",
    }
    p.update(kw)
    return p


def test_create_and_list(db, authed_client):
    """登记 → 列表可见; category/keyword 过滤生效。"""
    p1 = _payload(category="refund")
    p2 = _payload(category="refund")
    p3 = _payload(category="reception")
    assert authed_client.post("/api/v1/metrics/dict", json=p1).status_code == 200
    assert authed_client.post("/api/v1/metrics/dict", json=p2).status_code == 200
    assert authed_client.post("/api/v1/metrics/dict", json=p3).status_code == 200

    r = authed_client.get("/api/v1/metrics/dict", params={"category": "refund"})
    codes = {it["code"] for it in r.json()["data"]["items"]}
    assert {p1["code"], p2["code"]} <= codes and p3["code"] not in codes

    # keyword 模糊命中 code
    r2 = authed_client.get("/api/v1/metrics/dict", params={"keyword": p1["code"]})
    assert {it["code"] for it in r2.json()["data"]["items"]} == {p1["code"]}


def test_create_validation(db, authed_client):
    """校验: 必填缺失 / code 无域前缀 / code 重复 / name_cn 全站唯一。"""
    # 缺 definition
    r1 = authed_client.post("/api/v1/metrics/dict", json=_payload(definition=""))
    assert r1.status_code == 400
    # code 不带 域.名
    r2 = authed_client.post("/api/v1/metrics/dict", json=_payload(code="nodot"))
    assert r2.status_code == 400
    # code 重复
    p = _payload()
    assert authed_client.post("/api/v1/metrics/dict", json=p).status_code == 200
    r3 = authed_client.post("/api/v1/metrics/dict", json=_payload(code=p["code"]))
    assert r3.status_code == 409
    # name_cn 全站唯一
    r4 = authed_client.post("/api/v1/metrics/dict", json=_payload(name_cn=p["name_cn"]))
    assert r4.status_code == 409


def test_update_and_conflict(db, authed_client):
    """更新口径字段; name_cn 撞名 409; 未登记 404。"""
    p1, p2 = _payload(), _payload()
    for p in (p1, p2):
        authed_client.post("/api/v1/metrics/dict", json=p)

    # 正常更新 definition + remark(留变更背景)
    r = authed_client.put(f"/api/v1/metrics/dict/{p1['code']}", json={
        "definition": "更新后的口径", "remark": "#257 变更测试",
    })
    assert r.status_code == 200
    item = db.get(MetricDictionary, p1["code"])
    assert item.definition == "更新后的口径" and item.remark == "#257 变更测试"

    # name_cn 撞已登记名
    r2 = authed_client.put(
        f"/api/v1/metrics/dict/{p2['code']}", json={"name_cn": p1["name_cn"]})
    assert r2.status_code == 409
    # 未登记 code
    r3 = authed_client.put("/api/v1/metrics/dict/no.such", json={"definition": "x"})
    assert r3.status_code == 404


def test_list_inactive_hidden(db, authed_client):
    """停用口径默认不可见, include_inactive=true 可见; category 非法 400。"""
    p = _payload()
    authed_client.post("/api/v1/metrics/dict", json=p)
    authed_client.put(f"/api/v1/metrics/dict/{p['code']}", json={"is_active": False})

    r = authed_client.get("/api/v1/metrics/dict")
    assert p["code"] not in {it["code"] for it in r.json()["data"]["items"]}
    r2 = authed_client.get("/api/v1/metrics/dict", params={"include_inactive": "true"})
    assert p["code"] in {it["code"] for it in r2.json()["data"]["items"]}
    # category 非法
    assert authed_client.get(
        "/api/v1/metrics/dict", params={"category": "nope"}).status_code == 400


def test_seed_registry_consistency():
    """种子校验(验收: ≥15 条与代码口径一致): 从迁移文件读 _SEEDS,
    校验条数 / code 唯一 / name_cn 全站唯一 / 必填字段 / 实现指针 remark 存在。"""
    mig = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "b4_1_metric_dictionary.py"
    spec = importlib.util.spec_from_file_location("b4_1_metric_dictionary", mig)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    seeds = mod._SEEDS

    assert len(seeds) >= 15
    codes = [s["code"] for s in seeds]
    names = [s["name_cn"] for s in seeds]
    assert len(codes) == len(set(codes)), "code 重复"
    assert len(names) == len(set(names)), "name_cn 全站必须唯一"
    for s in seeds:
        assert "." in s["code"], f"{s['code']} 须为 域.名"
        assert s.get("definition") and s.get("category")
        assert s["category"] in {"reception", "refund", "complaint", "cash", "performance"}
        # 口径可追溯: 实现指针(remark)必填 —— 字典与代码不漂移的抓手
        assert s.get("remark"), f"{s['code']} 缺实现指针 remark"

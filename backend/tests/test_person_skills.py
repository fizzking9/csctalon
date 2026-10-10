# -*- coding: utf-8 -*-
"""B3-7 (#255) 人员技能矩阵: person_skill_tags 表 + 4 个技能 API"""

import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.person import Person, PersonSkillTag
from app.models.user import RoleEnum, User


@pytest.fixture()
def authed_client(db):
    """带鉴权的 TestClient: 覆盖 get_current_user + get_db, 与 db fixture 共享事务。

    注意: API 内 db.commit() 会把数据真实落测试库(外层 rollback 救不回),
    技能表在 fixture 前后各清一次, 防止用例间/历史脏数据串扰。
    """
    fake = User(id=1, username="tester", role=RoleEnum.ADMIN.value, is_active=True)

    def _override_user():
        return fake

    def _override_db():
        yield db

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    db.execute(delete(PersonSkillTag))
    db.commit()
    try:
        with TestClient(app) as c:
            yield c
    finally:
        db.execute(delete(PersonSkillTag))
        db.commit()
        app.dependency_overrides.clear()


def _mk_person(db, name: str | None = None, **kw) -> Person:
    p = Person(display_name=name or f"测试-{_uuid.uuid4().hex[:8]}", **kw)
    db.add(p)
    db.flush()
    return p


def test_put_skills_full_replace(db, authed_client):
    """全量替换: 首次新建 → 二次覆盖+删多余+改熟练度。"""
    p = _mk_person(db)

    # 首次: 写入 2 个技能
    r = authed_client.put(
        f"/api/v1/persons/{p.id}/skills",
        json={"skills": [{"skill_tag": "投诉处理", "level": 3}, {"skill_tag": "套餐咨询", "level": 4}]},
    )
    assert r.status_code == 200
    assert r.json()["code"] == 0
    tags = {st.skill_tag: st.level for st in db.query(PersonSkillTag).filter_by(person_id=p.id)}
    assert tags == {"投诉处理": 3, "套餐咨询": 4}

    # 二次: 保留套餐咨询(改级), 删投诉处理, 新增外呼
    r2 = authed_client.put(
        f"/api/v1/persons/{p.id}/skills",
        json={"skills": [{"skill_tag": "套餐咨询", "level": 5}, {"skill_tag": "外呼", "level": 1}]},
    )
    assert r2.status_code == 200
    tags2 = {st.skill_tag: st.level for st in db.query(PersonSkillTag).filter_by(person_id=p.id)}
    assert tags2 == {"套餐咨询": 5, "外呼": 1}


def test_put_skills_validation(db, authed_client):
    """校验: 熟练度越界 / 标签重复 / 人员不存在。"""
    p = _mk_person(db)
    # level 越界
    r1 = authed_client.put(f"/api/v1/persons/{p.id}/skills", json={"skills": [{"skill_tag": "x", "level": 6}]})
    assert r1.status_code == 400
    # 标签重复
    r2 = authed_client.put(
        f"/api/v1/persons/{p.id}/skills",
        json={"skills": [{"skill_tag": "a", "level": 1}, {"skill_tag": "a", "level": 2}]},
    )
    assert r2.status_code == 400
    # 人员不存在
    r3 = authed_client.put("/api/v1/persons/999999/skills", json={"skills": []})
    assert r3.status_code == 404
    # 失败后不留脏数据
    assert db.query(PersonSkillTag).filter_by(person_id=p.id).count() == 0


def test_batch_skills_add_and_remove(db, authed_client):
    """批量打标: add 追加+覆盖已有, remove 删除。"""
    p1 = _mk_person(db)
    p2 = _mk_person(db)
    db.add(PersonSkillTag(person_id=p1.id, skill_tag="投诉处理", level=2))
    db.flush()

    # add: p1 已有(覆盖为 4), p2 新建
    r = authed_client.post(
        "/api/v1/persons/batch-skills",
        json={"person_ids": [p1.id, p2.id], "skill_tag": "投诉处理", "level": 4, "mode": "add"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["affected"] == 2
    lv = {st.person_id: st.level for st in db.query(PersonSkillTag).filter_by(skill_tag="投诉处理")}
    assert lv == {p1.id: 4, p2.id: 4}

    # remove: 两条都删掉
    r2 = authed_client.post(
        "/api/v1/persons/batch-skills",
        json={"person_ids": [p1.id, p2.id], "skill_tag": "投诉处理", "mode": "remove"},
    )
    assert r2.status_code == 200
    assert r2.json()["data"]["affected"] == 2
    assert db.query(PersonSkillTag).filter_by(skill_tag="投诉处理").count() == 0


def test_matrix_and_skill_tags_and_filter(db, authed_client):
    """矩阵聚合 + 标签字典 + list_persons 按技能过滤 + 详情挂技能。"""
    p1 = _mk_person(db, department="企微")
    p2 = _mk_person(db, department="店铺", is_active=False)  # 停用人员不入矩阵
    db.add_all([
        PersonSkillTag(person_id=p1.id, skill_tag="投诉处理", level=3),
        PersonSkillTag(person_id=p1.id, skill_tag="外呼", level=1),
        PersonSkillTag(person_id=p2.id, skill_tag="外呼", level=2),
    ])
    db.flush()

    # 矩阵: 在职 p1 在列, 停用 p2 不在; 残留无标签人员不影响断言
    m = authed_client.get("/api/v1/persons/skills/matrix").json()["data"]
    assert set(m["tags"]) == {"外呼", "投诉处理"}
    ids = [row["person_id"] for row in m["persons"]]
    assert p1.id in ids and p2.id not in ids
    row = next(r for r in m["persons"] if r["person_id"] == p1.id)
    assert {s["skill_tag"]: s["level"] for s in row["skills"]} == {"外呼": 1, "投诉处理": 3}

    # 标签字典: 外呼 2 人 > 投诉处理 1 人
    tags = authed_client.get("/api/v1/persons/skill-tags").json()["data"]
    by_tag = {t["skill_tag"]: t["person_count"] for t in tags}
    assert by_tag == {"外呼": 2, "投诉处理": 1}

    # 列表按技能过滤(含停用的 p2)
    fl = authed_client.get("/api/v1/persons", params={"skill_tag": "外呼", "page_size": 100}).json()["data"]
    assert {it["id"] for it in fl["items"]} == {p1.id, p2.id}
    # 列表行带 skill_tags
    mine = next(it for it in fl["items"] if it["id"] == p1.id)
    assert {"skill_tag": "外呼", "level": 1} in mine["skill_tags"]

    # 详情挂技能
    d = authed_client.get(f"/api/v1/persons/{p1.id}").json()["data"]
    assert {"skill_tag": "投诉处理", "level": 3} in d["skill_tags"]

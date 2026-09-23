# -*- coding: utf-8 -*-
"""接入助手起草提示词版本化服务测试 (2026-09-23 提示词全量版本化)。

覆盖:
- load_effective_prompt: DB 生效优先 / 无生效行回退 None（调用方回退模块级默认）
- create_new_version: version+1 / 不激活 / 空内容复制生效行 / change_reason 留痕 / 非法 code 抛错
- activate_version: 同 code 旧生效自动取消 / 留痕(activated_at/by)
- rollback_to_version: 回滚 = 激活历史版本（账本完整）
- list_versions: 版本列表含生效标记

注意: create/activate 会 commit，跨用例残留用 _clean 清理。
"""

import pytest
from sqlalchemy import text

from app.services.onboarding_prompt_service import (
    activate_version,
    create_new_version,
    get_version,
    list_versions,
    load_effective_prompt,
    rollback_to_version,
)


def _clean(db):
    db.execute(text("DELETE FROM onboarding_prompts"))
    db.commit()


@pytest.fixture(autouse=True)
def _cleanup_committed(db):
    yield
    db.execute(text("DELETE FROM onboarding_prompts"))
    db.commit()


def _base_content(code: str) -> str:
    return f"这是 {code} 的基础提示词 v1 内容"


def test_load_effective_prompt_fallback(db):
    _clean(db)
    assert load_effective_prompt(db, "draft_system") is None
    v1 = create_new_version(db, "draft_system", _base_content("draft_system"), user_id=1)
    assert load_effective_prompt(db, "draft_system") is None
    activate_version(db, 1, v1.id)
    assert load_effective_prompt(db, "draft_system") == _base_content("draft_system")


def test_load_effective_prompt_invalid_code(db):
    assert load_effective_prompt(db, "not_exist") is None


def test_create_new_version_increments_inactive(db):
    _clean(db)
    v1 = create_new_version(db, "draft_system", _base_content("draft_system"), user_id=1)
    assert v1.version == 1 and v1.is_active is False
    v2 = create_new_version(db, "draft_system", _base_content("draft_system") + " v2", user_id=2)
    assert v2.version == 2 and v2.is_active is False and v2.created_by == 2
    rows = list_versions(db, "draft_system")
    assert len(rows) == 2 and {r["version"] for r in rows} == {1, 2}


def test_create_new_version_change_reason_tracked(db):
    """change_reason/references 留痕（用户核心需求：附注解决什么问题/影响什么）。"""
    _clean(db)
    v = create_new_version(
        db, "draft_system", _base_content("draft_system"), user_id=1,
        change_reason="金额列识别优化：拿不准时 confidence<0.7",
        references="评估中心建议 A-20260920-003",
    )
    assert v.change_reason == "金额列识别优化：拿不准时 confidence<0.7"
    assert v.references == "评估中心建议 A-20260920-003"
    row = list_versions(db, "draft_system")[0]
    assert row["change_reason"] == "金额列识别优化：拿不准时 confidence<0.7"


def test_create_new_version_empty_copies_active(db):
    _clean(db)
    v1 = create_new_version(db, "draft_system", _base_content("draft_system"), user_id=1)
    activate_version(db, 1, v1.id)
    v2 = create_new_version(db, "draft_system", "", user_id=1)
    assert v2.version == 2 and v2.content == _base_content("draft_system")


def test_create_new_version_invalid_code(db):
    with pytest.raises(ValueError):
        create_new_version(db, "bad_code", "x", user_id=1)


def test_activate_version_switches_only_one_active(db):
    _clean(db)
    v1 = create_new_version(db, "draft_system", _base_content("draft_system"), user_id=1)
    v2 = create_new_version(db, "draft_system", _base_content("draft_system") + " v2", user_id=1)
    activate_version(db, 1, v1.id)
    assert load_effective_prompt(db, "draft_system") == _base_content("draft_system")
    activate_version(db, 1, v2.id)
    assert load_effective_prompt(db, "draft_system") == _base_content("draft_system") + " v2"
    active = [r for r in list_versions(db, "draft_system") if r["is_active"]]
    assert len(active) == 1 and active[0]["version"] == 2


def test_activate_version_trails_who_when(db):
    _clean(db)
    v1 = create_new_version(db, "draft_system", _base_content("draft_system"), user_id=1)
    activate_version(db, 42, v1.id)
    row = list_versions(db, "draft_system")[0]
    assert row["activated_by"] == 42 and row["activated_at"] is not None


def test_activate_version_unknown_id(db):
    with pytest.raises(ValueError):
        activate_version(db, 1, 999999)


def test_rollback_to_version(db):
    _clean(db)
    v1 = create_new_version(db, "draft_system", _base_content("draft_system"), user_id=1)
    v2 = create_new_version(db, "draft_system", _base_content("draft_system") + " v2", user_id=1)
    activate_version(db, 1, v2.id)
    assert load_effective_prompt(db, "draft_system") == _base_content("draft_system") + " v2"
    row = rollback_to_version(db, 1, v1.id)
    assert row["version"] == 1 and row["is_active"] is True
    assert load_effective_prompt(db, "draft_system") == _base_content("draft_system")
    assert len(list_versions(db, "draft_system")) == 2


def test_draft_system_and_user_independent(db):
    _clean(db)
    s = create_new_version(db, "draft_system", _base_content("draft_system"), user_id=1)
    u = create_new_version(db, "draft_user", _base_content("draft_user"), user_id=1)
    activate_version(db, 1, s.id)
    assert load_effective_prompt(db, "draft_user") is None
    activate_version(db, 1, u.id)
    assert load_effective_prompt(db, "draft_user") == _base_content("draft_user")
    assert load_effective_prompt(db, "draft_system") == _base_content("draft_system")


def test_get_version_missing(db):
    assert get_version(db, 999999) is None

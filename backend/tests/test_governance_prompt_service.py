# -*- coding: utf-8 -*-
"""治理研判提示词版本化服务测试 (2026-09-23 提示词全量版本化)。

覆盖:
- load_effective_prompt: DB 生效优先 / 无生效行回退 None（调用方回退模块级默认）
- create_new_version: version+1 / 不激活 / 空内容复制生效行 / change_reason 留痕 / 非法 code 抛错
- activate_version: 同 code 旧生效自动取消（部分唯一索引）/ 留痕(activated_at/by)
- rollback_to_version: 回滚 = 激活历史版本（账本完整）
- list_versions: 版本列表含生效标记

注意: create/activate 会 commit（版本账本需持久留痕），跨用例残留用 _clean 清理。
"""

import pytest
from sqlalchemy import text

from app.services.governance_prompt_service import (
    activate_version,
    create_new_version,
    get_version,
    list_versions,
    load_effective_prompt,
    rollback_to_version,
)


def _clean(db):
    db.execute(text("DELETE FROM governance_prompts"))
    db.commit()


@pytest.fixture(autouse=True)
def _cleanup_committed(db):
    yield
    db.execute(text("DELETE FROM governance_prompts"))
    db.commit()


def _base_content(code: str) -> str:
    return f"这是 {code} 的基础提示词 v1 内容"


def test_load_effective_prompt_fallback(db):
    """无生效行 → None（调用方回退模块级默认）；激活后 → DB 内容优先。"""
    _clean(db)
    assert load_effective_prompt(db, "judge_system") is None
    v1 = create_new_version(db, "judge_system", _base_content("judge_system"), user_id=1)
    assert load_effective_prompt(db, "judge_system") is None  # 未激活仍回退
    activate_version(db, 1, v1.id)
    assert load_effective_prompt(db, "judge_system") == _base_content("judge_system")


def test_load_effective_prompt_invalid_code(db):
    assert load_effective_prompt(db, "not_exist") is None


def test_create_new_version_increments_inactive(db):
    """新增版本 version+1、不激活；旧版本行保留（可回滚）。"""
    _clean(db)
    v1 = create_new_version(db, "judge_system", _base_content("judge_system"), user_id=1)
    assert v1.version == 1 and v1.is_active is False
    v2 = create_new_version(db, "judge_system", _base_content("judge_system") + " v2", user_id=2)
    assert v2.version == 2 and v2.is_active is False and v2.created_by == 2
    rows = list_versions(db, "judge_system")
    assert len(rows) == 2 and {r["version"] for r in rows} == {1, 2}


def test_create_new_version_change_reason_tracked(db):
    """change_reason/references 留痕（用户核心需求：附注解决什么问题/影响什么）。"""
    _clean(db)
    v = create_new_version(
        db, "judge_system", _base_content("judge_system"), user_id=1,
        change_reason="修复 allowed_by_business 误建议 mark_ignored",
        references="工单 #171 E1 生产实测",
    )
    assert v.change_reason == "修复 allowed_by_business 误建议 mark_ignored"
    assert v.references == "工单 #171 E1 生产实测"
    row = list_versions(db, "judge_system")[0]
    assert row["change_reason"] == "修复 allowed_by_business 误建议 mark_ignored"


def test_create_new_version_empty_copies_active(db):
    _clean(db)
    v1 = create_new_version(db, "judge_system", _base_content("judge_system"), user_id=1)
    activate_version(db, 1, v1.id)
    v2 = create_new_version(db, "judge_system", "", user_id=1)
    assert v2.version == 2 and v2.content == _base_content("judge_system")


def test_create_new_version_invalid_code(db):
    with pytest.raises(ValueError):
        create_new_version(db, "bad_code", "x", user_id=1)


def test_activate_version_switches_only_one_active(db):
    """激活新版本 → 同 code 旧生效自动取消；每 code 至多一个生效。"""
    _clean(db)
    v1 = create_new_version(db, "judge_system", _base_content("judge_system"), user_id=1)
    v2 = create_new_version(db, "judge_system", _base_content("judge_system") + " v2", user_id=1)
    activate_version(db, 1, v1.id)
    assert load_effective_prompt(db, "judge_system") == _base_content("judge_system")
    activate_version(db, 1, v2.id)
    assert load_effective_prompt(db, "judge_system") == _base_content("judge_system") + " v2"
    active = [r for r in list_versions(db, "judge_system") if r["is_active"]]
    assert len(active) == 1 and active[0]["version"] == 2


def test_activate_version_trails_who_when(db):
    """激活留痕：activated_at/activated_by 记录。"""
    _clean(db)
    v1 = create_new_version(db, "judge_system", _base_content("judge_system"), user_id=1)
    activate_version(db, 42, v1.id)
    row = list_versions(db, "judge_system")[0]
    assert row["activated_by"] == 42
    assert row["activated_at"] is not None


def test_activate_version_unknown_id(db):
    with pytest.raises(ValueError):
        activate_version(db, 1, 999999)


def test_rollback_to_version(db):
    """回滚 = 激活历史版本；当前版本行保留（账本完整）。"""
    _clean(db)
    v1 = create_new_version(db, "judge_system", _base_content("judge_system"), user_id=1)
    v2 = create_new_version(db, "judge_system", _base_content("judge_system") + " v2", user_id=1)
    activate_version(db, 1, v2.id)
    assert load_effective_prompt(db, "judge_system") == _base_content("judge_system") + " v2"
    row = rollback_to_version(db, 1, v1.id)
    assert row["version"] == 1 and row["is_active"] is True
    assert load_effective_prompt(db, "judge_system") == _base_content("judge_system")
    assert len(list_versions(db, "judge_system")) == 2  # v2 仍在账本


def test_judge_system_and_user_independent(db):
    """两个 code 的生效版本互不影响。"""
    _clean(db)
    s = create_new_version(db, "judge_system", _base_content("judge_system"), user_id=1)
    u = create_new_version(db, "judge_user", _base_content("judge_user"), user_id=1)
    activate_version(db, 1, s.id)
    assert load_effective_prompt(db, "judge_user") is None
    activate_version(db, 1, u.id)
    assert load_effective_prompt(db, "judge_user") == _base_content("judge_user")
    assert load_effective_prompt(db, "judge_system") == _base_content("judge_system")


def test_get_version_missing(db):
    assert get_version(db, 999999) is None

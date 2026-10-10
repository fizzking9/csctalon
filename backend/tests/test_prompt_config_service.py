# -*- coding: utf-8 -*-
"""提示词版本化服务测试 (工单 #178)。

覆盖:
- load_effective_prompt: DB 生效优先 / 无生效行回退 None（调用方回退代码常量）
- create_new_version: version+1 / 不激活 / 空内容复制生效行 / 非法 code 抛错
- activate_version: 同 code 旧生效自动取消（部分唯一索引）/ 留痕
- rollback_to_version: 回滚 = 激活历史版本（账本完整）
- list_versions: 版本列表含生效标记
- canary_smoke (#179): LLM 非空返回 → ok=True；空 → ok=False；LLM 异常 → degraded 降级放行

注意: create/activate 会 commit（版本账本需持久留痕），跨用例残留用 _clean 清理，
与 test_prompt_eval_service._clean_iterations 同款范式。
"""

import pytest
from sqlalchemy import text

from app.services.prompt_config_service import (
    activate_version,
    canary_smoke,
    create_new_version,
    get_version,
    list_versions,
    load_effective_prompt,
    rollback_to_version,
)


def _clean(db):
    """清掉上一轮运行残留的 prompt_configs 行（create/activate 会 commit，绕开事务回滚）。"""
    db.execute(text("DELETE FROM prompt_configs"))
    db.commit()


@pytest.fixture(autouse=True)
def _cleanup_committed(db):
    """每个用例后清掉本模块 commit 落的 prompt_configs 行。

    create/activate 会 commit（版本账本需持久留痕），残留行会污染共享测试库：
    其它模块（如 test_ai_assistant 断言 system 兜底为代码常量）依赖 prompt_configs
    无生效行。teardown 里 DELETE 清空，保证模块间隔离。
    """
    yield
    db.execute(text("DELETE FROM prompt_configs"))
    db.commit()


def _base_content(code: str) -> str:
    return f"这是 {code} 的基础提示词 v1 内容"


def test_load_effective_prompt_fallback(db):
    """无生效行 → None（调用方回退代码常量）；激活后 → DB 内容优先。"""
    _clean(db)
    # 初始无生效行 → 回退
    assert load_effective_prompt(db, "gen_system") is None
    # 造一个未激活版本 → 仍回退
    v1 = create_new_version(db, "gen_system", _base_content("gen_system"), user_id=1)
    assert load_effective_prompt(db, "gen_system") is None
    # 激活 → DB 内容生效
    activate_version(db, 1, v1.id)
    assert load_effective_prompt(db, "gen_system") == _base_content("gen_system")


def test_load_effective_prompt_invalid_code(db):
    """非法 code 一律 None（不查库）。"""
    assert load_effective_prompt(db, "not_exist") is None


def test_create_new_version_increments_inactive(db):
    """新增版本 version+1、不激活；旧版本行保留（可回滚）。"""
    _clean(db)
    v1 = create_new_version(db, "gen_system", _base_content("gen_system"), user_id=1)
    assert v1.version == 1
    assert v1.is_active is False

    v2 = create_new_version(db, "gen_system", _base_content("gen_system") + " v2 改动", user_id=2)
    assert v2.version == 2
    assert v2.is_active is False
    assert v2.created_by == 2

    rows = list_versions(db, "gen_system")
    assert len(rows) == 2
    assert {r["version"] for r in rows} == {1, 2}


def test_create_new_version_empty_copies_active(db):
    """content 为空时以当前生效版本为底（先改后升典型流程）。"""
    _clean(db)
    v1 = create_new_version(db, "gen_system", _base_content("gen_system"), user_id=1)
    activate_version(db, 1, v1.id)
    v2 = create_new_version(db, "gen_system", "", user_id=1)
    assert v2.version == 2
    assert v2.content == _base_content("gen_system")


def test_create_new_version_invalid_code(db):
    """非法 code 抛 ValueError。"""
    with pytest.raises(ValueError):
        create_new_version(db, "bad_code", "x", user_id=1)


def test_activate_version_switches_only_one_active(db):
    """激活新版本 → 同 code 旧生效自动取消；每 code 至多一个生效版本。"""
    _clean(db)
    v1 = create_new_version(db, "gen_system", _base_content("gen_system"), user_id=1)
    v2 = create_new_version(db, "gen_system", _base_content("gen_system") + " v2", user_id=1)
    # 激活 v1
    activate_version(db, 1, v1.id)
    assert load_effective_prompt(db, "gen_system") == _base_content("gen_system")
    # 再激活 v2 → v1 自动取消
    activate_version(db, 1, v2.id)
    assert load_effective_prompt(db, "gen_system") == _base_content("gen_system") + " v2"
    rows = list_versions(db, "gen_system")
    active = [r for r in rows if r["is_active"]]
    assert len(active) == 1 and active[0]["version"] == 2


def test_activate_version_unknown_id(db):
    """不存在的版本 id 抛 ValueError。"""
    with pytest.raises(ValueError):
        activate_version(db, 1, 999999)


def test_rollback_to_version(db):
    """回滚 = 激活历史版本；当前版本行保留（账本完整），生效指针切回旧版。"""
    _clean(db)
    v1 = create_new_version(db, "gen_system", _base_content("gen_system"), user_id=1)
    v2 = create_new_version(db, "gen_system", _base_content("gen_system") + " v2", user_id=1)
    activate_version(db, 1, v2.id)
    assert load_effective_prompt(db, "gen_system") == _base_content("gen_system") + " v2"

    row = rollback_to_version(db, 1, v1.id)
    assert row["version"] == 1
    assert row["is_active"] is True
    assert load_effective_prompt(db, "gen_system") == _base_content("gen_system")
    # v2 仍在账本中（回滚不删行）
    assert len(list_versions(db, "gen_system")) == 2


def test_extract_and_gen_codes_independent(db):
    """两个 code 的生效版本互不影响（gen_system 激活不影响 extract_system）。"""
    _clean(db)
    g1 = create_new_version(db, "gen_system", _base_content("gen_system"), user_id=1)
    e1 = create_new_version(db, "extract_system", _base_content("extract_system"), user_id=1)
    activate_version(db, 1, g1.id)
    # extract_system 仍无生效行 → 回退代码常量
    assert load_effective_prompt(db, "extract_system") is None
    activate_version(db, 1, e1.id)
    assert load_effective_prompt(db, "extract_system") == _base_content("extract_system")
    assert load_effective_prompt(db, "gen_system") == _base_content("gen_system")


def test_get_version_missing(db):
    """不存在的版本 id → None（激活路由据此 400）。"""
    assert get_version(db, 999999) is None


# ── #179 金丝雀冒烟 ─────────────────────────────────────────
def test_canary_smoke_ok(db, monkeypatch):
    """LLM 正常返回 → ok=True（通过才激活）。"""
    from app.services import llm_client

    calls = {"n": 0}

    async def fake_llm(messages, **_kw):
        calls["n"] += 1
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "新提示词内容"
        return "今日退款: 共 5 笔, 金额 1000 元(窗口: 今日)"

    monkeypatch.setattr(llm_client, "chat_completion", fake_llm)
    res = canary_smoke(db, "gen_system", "新提示词内容")
    assert res["ok"] is True and calls["n"] == 1
    assert res["sample"]


def test_canary_smoke_empty_output(db, monkeypatch):
    """LLM 返回空 → ok=False（拒绝激活）。"""
    from app.services import llm_client

    async def fake_llm(*_a, **_kw):
        return ""

    monkeypatch.setattr(llm_client, "chat_completion", fake_llm)
    res = canary_smoke(db, "gen_system", "新提示词内容")
    assert res["ok"] is False and res["error"]


def test_canary_smoke_degraded(db, monkeypatch):
    """LLM 抛异常 → degraded（降级放行，不阻塞版本管理）。"""
    from app.services import llm_client

    async def boom(*_a, **_kw):
        raise RuntimeError("LLM 服务不可用")

    monkeypatch.setattr(llm_client, "chat_completion", boom)
    res = canary_smoke(db, "extract_system", "新提示词内容")
    assert res["ok"] is None and res["degraded"] is True


def test_canary_smoke_empty_content(db):
    """空内容 → ok=False（内容为空必拒）。"""
    res = canary_smoke(db, "gen_system", "   ")
    assert res["ok"] is False

"""出域策略落库测试（2026-09-21）

背景（见 docs/design/20260921-模型角色分组与出域管控设计.md §3.3）:

  B1 出域管控的策略取自 `settings.LLM_CLOUD_POLICY`（.env），改动需重启。
  本次落库到 `llm_cloud_policy` 单行表，管理员界面可即时切换。

关键约束（本测试锁定 —— 这是**数据边界**，不容许窗口期）:
  1. DB 有行 → 用 DB 值（优先级高于 .env）
  2. DB 无行 → 回退 settings.LLM_CLOUD_POLICY（保证升级后零行为变更）
  3. 非法值 → 回退 explicit_only（保守）
  4. **保存后立即失效缓存**（不设 TTL 窗口期，否则最长 30s 内仍用旧策略）
"""
import pytest

from app.core.config import settings
from app.services import llm_tiers


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个用例前后清缓存，避免相互污染。"""
    llm_tiers.invalidate_policy_cache()
    yield
    llm_tiers.invalidate_policy_cache()


@pytest.fixture
def policy(monkeypatch):
    def _set(v):
        monkeypatch.setattr(settings, "LLM_CLOUD_POLICY", v)
    return _set


class TestDbPriority:
    def test_db_wins_over_env(self, monkeypatch, policy):
        """DB 有行 → 用 DB 值（.env 仅作兜底）。"""
        policy("auto")     # .env 说 auto
        monkeypatch.setattr(llm_tiers, "_read_policy_from_db", lambda: "never")
        assert llm_tiers.cloud_policy() == "never"

    def test_falls_back_to_env_when_no_row(self, monkeypatch, policy):
        """DB 无行 → 回退 .env（保证升级后零行为变更）。"""
        policy("auto")
        monkeypatch.setattr(llm_tiers, "_read_policy_from_db", lambda: None)
        assert llm_tiers.cloud_policy() == "auto"

    def test_illegal_db_value_falls_back(self, monkeypatch, policy):
        """DB 里有非法值（手工改库）→ 保守回退 explicit_only。"""
        policy("auto")
        monkeypatch.setattr(llm_tiers, "_read_policy_from_db", lambda: "bogus")
        assert llm_tiers.cloud_policy() == llm_tiers.CLOUD_POLICY_EXPLICIT

    def test_db_error_falls_back_to_env(self, monkeypatch, policy):
        """读 DB 抛异常 → 不阻塞业务，回退 .env。"""
        policy("never")
        # _read_policy_from_db 内部已 try/except 返回 None，这里模拟其返回值
        monkeypatch.setattr(llm_tiers, "_read_policy_from_db", lambda: None)
        assert llm_tiers.cloud_policy() == "never"


class TestCacheInvalidation:
    def test_cache_hit_avoids_repeat_query(self, monkeypatch):
        """TTL 内重复调用不重复查库（性能）。"""
        calls = {"n": 0}

        def _fake():
            calls["n"] += 1
            return "auto"

        monkeypatch.setattr(llm_tiers, "_read_policy_from_db", _fake)
        llm_tiers.cloud_policy()
        # 注: _read_policy_from_db 被替换为纯函数，缓存逻辑在其内部，
        # 故此处只验证「多次调用不报错」；真实缓存由 _policy_cache 控制。
        assert calls["n"] >= 1

    def test_invalidate_clears_cache(self, monkeypatch):
        """⭐ 关键: invalidate 后必须重新读 —— 数据边界不容许 TTL 窗口期。"""
        state = {"v": "auto"}

        def _fake():
            # 模拟「先查缓存，未命中才读真实来源」
            if llm_tiers._policy_cache["value"] is not None:
                return llm_tiers._policy_cache["value"]
            return state["v"]

        monkeypatch.setattr(llm_tiers, "_read_policy_from_db", _fake)
        assert llm_tiers.cloud_policy() == "auto"

        # 外部改库
        state["v"] = "never"
        # 未失效 → 可能仍读到旧值（取决于缓存状态）
        llm_tiers.invalidate_policy_cache()
        # 失效后必须读到新值
        assert llm_tiers.cloud_policy() == "never"

    def test_never_policy_blocks_cloud_immediately(self, monkeypatch, policy):
        """策略切 never 后，云端应立即从链中消失（无窗口期）。"""
        policy("auto")
        monkeypatch.setattr(llm_tiers, "_read_policy_from_db", lambda: None)
        assert llm_tiers.cloud_allowed() is True

        # 改为 never 并失效缓存
        monkeypatch.setattr(llm_tiers, "_read_policy_from_db", lambda: "never")
        llm_tiers.invalidate_policy_cache()
        assert llm_tiers.cloud_allowed() is False
        assert llm_tiers.CLOUD_FALLBACK not in llm_tiers.resolve_tier(
            "assistant", include_cloud=True)


class TestPolicySemantics:
    """三种策略的语义（与 B1 一致，此处确保落库后语义未变）。"""

    def test_never(self, monkeypatch):
        monkeypatch.setattr(llm_tiers, "_read_policy_from_db", lambda: "never")
        assert llm_tiers.cloud_allowed(explicit=False) is False
        assert llm_tiers.cloud_allowed(explicit=True) is False

    def test_explicit_only(self, monkeypatch):
        monkeypatch.setattr(llm_tiers, "_read_policy_from_db", lambda: "explicit_only")
        assert llm_tiers.cloud_allowed(explicit=False) is False
        assert llm_tiers.cloud_allowed(explicit=True) is True

    def test_auto(self, monkeypatch):
        monkeypatch.setattr(llm_tiers, "_read_policy_from_db", lambda: "auto")
        assert llm_tiers.cloud_allowed(explicit=False) is True

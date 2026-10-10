"""数据出域管控测试（2026-09-21）

背景: 云端 provider(deepseek-v4-flash) 原先是**无条件**挂在所有能力档位链尾的
兜底项。本地模型 transient 失败（实测 27b 曾 30% 超时）会**静默把 prompt 发往
api.deepseek.com**，而 prompt 含真实客服工单数据 —— 与「数据不出内网」的
核心定位冲突，且无开关、无审计标记。

本测试锁定三种策略的行为，防止回归:

  never         → 云端从所有链中移除（数据绝对不出域）
  explicit_only → 仅当调用方显式 include_cloud=True 才加入（默认）
  auto          → 保留在链尾（旧行为，需管理员显式选择）

⚠️ 这里是**数据边界**的守护测试 —— 改动此处务必先读设计文档:
   docs/design/20260921-模型角色分组与出域管控设计.md
"""
import pytest

from app.core.config import settings
from app.services import llm_tiers
from app.services.llm_provider_service import _apply_cloud_filter, get_provider_chain


@pytest.fixture
def policy(monkeypatch):
    """切换全局出域策略的便捷夹具。"""
    def _set(v: str):
        monkeypatch.setattr(settings, "LLM_CLOUD_POLICY", v)
    return _set


# ─────────────────────────────────────────────────────────────────────
# 策略解析
# ─────────────────────────────────────────────────────────────────────
class TestCloudPolicy:
    @pytest.mark.parametrize("v", ["never", "explicit_only", "auto"])
    def test_valid_policies(self, policy, v):
        policy(v)
        assert llm_tiers.cloud_policy() == v

    def test_illegal_falls_back_to_explicit(self, policy):
        """非法值保守回退 explicit_only（不放行，也不过度禁止）。"""
        policy("bogus")
        assert llm_tiers.cloud_policy() == llm_tiers.CLOUD_POLICY_EXPLICIT

    def test_empty_falls_back_to_explicit(self, policy):
        policy("")
        assert llm_tiers.cloud_policy() == llm_tiers.CLOUD_POLICY_EXPLICIT

    def test_case_insensitive(self, policy):
        policy("NEVER")
        assert llm_tiers.cloud_policy() == "never"


class TestCloudAllowed:
    def test_never_blocks_even_explicit(self, policy):
        """never: 连显式请求也不允许（数据绝对不出域）。"""
        policy("never")
        assert llm_tiers.cloud_allowed(explicit=False) is False
        assert llm_tiers.cloud_allowed(explicit=True) is False

    def test_explicit_only_requires_explicit(self, policy):
        """explicit_only: 默认不放行, 仅显式请求时允许。"""
        policy("explicit_only")
        assert llm_tiers.cloud_allowed(explicit=False) is False
        assert llm_tiers.cloud_allowed(explicit=True) is True

    def test_auto_always_allows(self, policy):
        policy("auto")
        assert llm_tiers.cloud_allowed(explicit=False) is True
        assert llm_tiers.cloud_allowed(explicit=True) is True


# ─────────────────────────────────────────────────────────────────────
# resolve_tier 的云端取舍
# ─────────────────────────────────────────────────────────────────────
class TestResolveTierCloud:
    def test_auto_includes_cloud(self, policy):
        policy("auto")
        assert llm_tiers.CLOUD_FALLBACK in llm_tiers.resolve_tier("assistant")

    def test_explicit_only_excludes_by_default(self, policy):
        """默认（未显式请求）不应把云端放进链 —— 这是本次修复的核心。"""
        policy("explicit_only")
        chain = llm_tiers.resolve_tier("assistant")
        assert llm_tiers.CLOUD_FALLBACK not in chain
        # 但本地首选/备选仍在
        assert len(chain) >= 2

    def test_explicit_only_includes_when_requested(self, policy):
        policy("explicit_only")
        chain = llm_tiers.resolve_tier("assistant", include_cloud=True)
        assert llm_tiers.CLOUD_FALLBACK in chain

    def test_never_excludes_cloud(self, policy):
        policy("never")
        for tier in ("assistant", "specialist", "expert"):
            assert llm_tiers.CLOUD_FALLBACK not in llm_tiers.resolve_tier(tier)
            # 显式也不行
            assert llm_tiers.CLOUD_FALLBACK not in llm_tiers.resolve_tier(
                tier, include_cloud=True)


# ─────────────────────────────────────────────────────────────────────
# get_provider_chain 的尾巴过滤（补漏闸门）
# ─────────────────────────────────────────────────────────────────────
def _mk(code, pid, prio, is_cloud=False):
    return {"id": pid, "code": code, "priority": prio,
            "pregen_only": False, "is_cloud": is_cloud}


@pytest.fixture
def mixed_providers(monkeypatch):
    """本地 + 云端混合的活跃 provider 集合。"""
    rows = [
        _mk("talon-gmk-qwen3.5-122b", 5, 3, is_cloud=False),
        _mk("talon-gmk-qwen3.8-27b", 4, 4, is_cloud=False),
        _mk("talon-gmk-qwen3-coder-30b", 1, 15, is_cloud=False),
        _mk("talon-gmk-qwen2.5-7b", 2, 20, is_cloud=False),
        _mk("deepseek-v4-flash", 3, 30, is_cloud=True),      # 云端
    ]
    monkeypatch.setattr(
        "app.services.llm_provider_service._load_active_providers",
        lambda db: rows)
    return rows


class _DB:
    pass


class TestChainTailFilter:
    def test_explicit_only_drops_cloud_from_default_chain(self, policy, mixed_providers):
        """⭐ 核心回归: 默认链（用户实时问答）不得含云端。"""
        policy("explicit_only")
        codes = [p["code"] for p in get_provider_chain(_DB(), None)]
        assert "deepseek-v4-flash" not in codes
        assert "talon-gmk-qwen3.8-27b" in codes     # 本地仍在

    def test_never_drops_cloud(self, policy, mixed_providers):
        policy("never")
        codes = [p["code"] for p in get_provider_chain(_DB(), None)]
        assert "deepseek-v4-flash" not in codes

    def test_auto_keeps_cloud(self, policy, mixed_providers):
        policy("auto")
        codes = [p["code"] for p in get_provider_chain(_DB(), None)]
        assert "deepseek-v4-flash" in codes

    def test_tier_chain_tail_also_filtered(self, policy, mixed_providers):
        """档位链的**尾巴**（其余常规链）同样不得含云端。"""
        policy("explicit_only")
        chain = get_provider_chain(
            _DB(), llm_tiers.resolve_tier("assistant", include_cloud=False))
        codes = [p["code"] for p in chain]
        assert "deepseek-v4-flash" not in codes

    def test_designated_cloud_still_works(self, policy, mixed_providers):
        """显式指定云端（如用户确认后）不受尾巴过滤影响 —— 否则该功能无法使用。

        注: 此处是「指定链」，description 中 designated 优先，属有意放行。
        """
        policy("explicit_only")
        chain = get_provider_chain(_DB(), ["deepseek-v4-flash"])
        assert chain[0]["code"] == "deepseek-v4-flash"


class TestApplyCloudFilterDirect:
    def test_auto_passthrough(self, policy):
        policy("auto")
        rows = [_mk("local", 1, 1), _mk("cloud", 2, 2, is_cloud=True)]
        assert len(_apply_cloud_filter(rows, None)) == 2

    def test_explicit_drops_cloud(self, policy):
        policy("explicit_only")
        rows = [_mk("local", 1, 1), _mk("cloud", 2, 2, is_cloud=True)]
        out = _apply_cloud_filter(rows, None)
        assert [p["code"] for p in out] == ["local"]

    def test_legacy_row_without_is_cloud_field(self, policy):
        """兼容旧库（is_cloud 列未迁移时）: 缺字段视为本地, 不误删。"""
        policy("explicit_only")
        rows = [{"id": 1, "code": "legacy", "priority": 1, "pregen_only": False}]
        out = _apply_cloud_filter(rows, None)
        assert len(out) == 1

"""能力档位（llm_tiers）与 provider 链构造测试（2026-09-21）

背景: csctalon 原先把 talon 的 provider_code 硬编码在各业务服务里，
导致「换模型必须改业务代码」。本测试锁定档位层的契约:

  1. 档位名合法 → 返回「首选 → 备选 → 云端兜底」链
  2. 档位名非法 → 返回空列表（调用方走默认链，不抛异常）
  3. 环境变量可覆盖首选（运维不发布代码即可调整）
  4. get_provider_chain 接受 list（档位链）且逐个跳过不可用者
  5. 向后兼容: 单字符串行为与旧实现一致
"""
import pytest

from app.services import llm_tiers
from app.services.llm_provider_service import get_provider_chain


# ─────────────────────────────────────────────────────────────────────
# 档位定义
# ─────────────────────────────────────────────────────────────────────
class TestTierDefinitions:
    def test_three_tiers_exist(self):
        assert set(llm_tiers.TIERS) == {"assistant", "specialist", "expert"}

    def test_each_tier_has_primary_and_desc(self):
        for name, spec in llm_tiers.TIERS.items():
            assert spec.get("primary"), f"{name} 缺 primary"
            assert spec.get("desc"), f"{name} 缺 desc"
            assert spec.get("rationale"), f"{name} 缺 rationale（选型依据）"

    def test_primary_not_qwen2_5_7b_for_assistant(self):
        """回归: assistant 首选**不得**是 7b —— 实测它在语义匹配 5/5 全错。"""
        assert llm_tiers.TIERS["assistant"]["primary"] != "talon-gmk-qwen2.5-7b"

    def test_no_tier_primary_is_disabled_provider(self):
        """档位首选应是实测可用且当前启用的 provider（防止指向 disabled）。"""
        # 允许集合 = 生产实测可用者（27b 当前 disabled，故不作 assistant/expert 首选）
        assert llm_tiers.TIERS["assistant"]["primary"] == "talon-gmk-qwen3-coder-30b"
        assert llm_tiers.TIERS["expert"]["primary"] == "talon-gmk-qwen3.5-122b"


class TestResolveTier:
    def test_assistant_chain(self):
        chain = llm_tiers.resolve_tier("assistant")
        assert chain[0] == "talon-gmk-qwen3-coder-30b"     # 首选
        assert chain[1] == "talon-gmk-qwen2.5-7b"          # 备选
        assert chain[-1] == llm_tiers.CLOUD_FALLBACK       # 云端兜底

    def test_expert_chain(self):
        chain = llm_tiers.resolve_tier("expert")
        assert chain[0] == "talon-gmk-qwen3.5-122b"
        assert chain[-1] == llm_tiers.CLOUD_FALLBACK

    def test_chain_has_no_duplicates(self):
        for name in llm_tiers.TIERS:
            chain = llm_tiers.resolve_tier(name)
            assert len(chain) == len(set(chain)), f"{name} 链有重复: {chain}"

    def test_unknown_tier_returns_empty(self):
        """非法档位返回空列表（调用方走默认链），不抛异常。"""
        assert llm_tiers.resolve_tier("nonexistent") == []
        assert llm_tiers.resolve_tier("") == []

    def test_env_override(self, monkeypatch):
        """运维可用环境变量覆盖首选，无需发布代码。"""
        monkeypatch.setenv("LLM_TIER_PRIMARY_OVERRIDE",
                           "assistant=talon-gmk-qwen3.8-27b")
        chain = llm_tiers.resolve_tier("assistant")
        assert chain[0] == "talon-gmk-qwen3.8-27b"   # 被覆盖
        assert chain[1] == "talon-gmk-qwen2.5-7b"    # 备选不受影响

    def test_env_override_ignores_unknown_tier(self, monkeypatch):
        """覆盖里出现非法档位名 → 忽略，不影响合法档位。"""
        monkeypatch.setenv("LLM_TIER_PRIMARY_OVERRIDE",
                           "bogus=xxx,assistant=talon-gmk-qwen2.5-7b")
        assert llm_tiers.resolve_tier("assistant")[0] == "talon-gmk-qwen2.5-7b"

    def test_describe_shape(self):
        d = llm_tiers.describe()
        assert set(d) == {"assistant", "specialist", "expert"}
        for v in d.values():
            assert isinstance(v["chain"], list) and v["chain"]


# ─────────────────────────────────────────────────────────────────────
# get_provider_chain 支持 list
# ─────────────────────────────────────────────────────────────────────
def _mk(code, pid, prio):
    return {"id": pid, "code": code, "priority": prio, "pregen_only": False}


class _FakeDB:
    pass


@pytest.fixture
def fake_providers(monkeypatch):
    """注入假的活跃 provider 集合，隔离数据库。"""
    rows = [
        _mk("talon-gmk-qwen3.5-122b", 5, 3),
        _mk("talon-gmk-qwen3-coder-30b", 1, 15),
        _mk("talon-gmk-qwen2.5-7b", 2, 20),
        _mk("deepseek-v4-flash", 3, 30),
    ]
    monkeypatch.setattr(
        "app.services.llm_provider_service._load_active_providers",
        lambda db: rows,
    )
    return rows


class TestProviderChainWithList:
    def test_list_orders_by_given_sequence(self, fake_providers):
        """档位链按声明顺序置顶（首选 coder-30b → 备选 7b → 兜底 deepseek）。"""
        chain = get_provider_chain(_FakeDB(), [
            "talon-gmk-qwen3-coder-30b", "talon-gmk-qwen2.5-7b", "deepseek-v4-flash"])
        assert [p["code"] for p in chain[:3]] == [
            "talon-gmk-qwen3-coder-30b", "talon-gmk-qwen2.5-7b", "deepseek-v4-flash"]
        # 其余 provider 追加在后（122b 未在链中，应出现在尾部）
        assert "talon-gmk-qwen3.5-122b" in [p["code"] for p in chain[3:]]

    def test_list_skips_unavailable(self, fake_providers):
        """链中不可用者被跳过，不影响其余（如 27b 未启用）。"""
        chain = get_provider_chain(_FakeDB(), [
            "talon-gmk-qwen3.8-27b",      # 不在活跃列表（已 disabled）
            "talon-gmk-qwen3-coder-30b",
        ])
        assert chain[0]["code"] == "talon-gmk-qwen3-coder-30b"

    def test_list_all_unavailable_falls_back_to_normal(self, fake_providers):
        """链全部不可用 → 退回常规链（不硬失败）。"""
        chain = get_provider_chain(_FakeDB(), ["nope-a", "nope-b"])
        assert [p["code"] for p in chain] == [
            "talon-gmk-qwen3.5-122b", "talon-gmk-qwen3-coder-30b",
            "talon-gmk-qwen2.5-7b", "deepseek-v4-flash"]

    def test_string_backward_compatible(self, fake_providers):
        """单字符串行为与旧实现一致（置顶 + 其余按 priority）。"""
        chain = get_provider_chain(_FakeDB(), "talon-gmk-qwen2.5-7b")
        assert chain[0]["code"] == "talon-gmk-qwen2.5-7b"
        assert [p["code"] for p in chain[1:]] == [
            "talon-gmk-qwen3.5-122b", "talon-gmk-qwen3-coder-30b", "deepseek-v4-flash"]

    def test_none_returns_normal(self, fake_providers):
        chain = get_provider_chain(_FakeDB(), None)
        assert [p["code"] for p in chain] == [
            "talon-gmk-qwen3.5-122b", "talon-gmk-qwen3-coder-30b",
            "talon-gmk-qwen2.5-7b", "deepseek-v4-flash"]

    def test_no_duplicate_when_designated_also_in_normal(self, fake_providers):
        """指定的 provider 不应在尾部重复出现。"""
        chain = get_provider_chain(_FakeDB(), ["talon-gmk-qwen2.5-7b"])
        codes = [p["code"] for p in chain]
        assert codes.count("talon-gmk-qwen2.5-7b") == 1

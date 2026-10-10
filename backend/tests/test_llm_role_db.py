"""角色落库（B2）测试 —— resolve_tier 读 DB 的行为

背景（2026-09-21，见 docs/design/20260921-模型角色分组与出域管控设计.md）:

  B2 把能力档位从「Python 常量」升级为「DB 优先，常量兜底」，
  使管理员可在界面调整角色成员与顺序，无需改代码。

关键约束（本测试锁定）:
  1. 传 db 且 DB 有配置 → 用 DB（按 role_order 排序）
  2. 传 db 但 DB 无配置 → 回退源码常量（保证首次部署即生效）
  3. db=None → 行为与 B1 完全一致（向后兼容，旧调用点不受影响）
  4. 角色级 allow_cloud=false 时，即使全局策略为 auto 也不出域（双重管控）
  5. 角色 enabled=false → 视为未配置，回退常量
"""
import pytest

from app.core.config import settings
from app.services import llm_tiers


# ─────────────────────────────────────────────────────────────────────
# 轻量 fake（避免依赖真实 DB；只测 resolve_tier 的分支逻辑）
# ─────────────────────────────────────────────────────────────────────
class _Role:
    def __init__(self, enabled=True, allow_cloud=False):
        self.enabled = enabled
        self.allow_cloud = allow_cloud


class _Prov:
    def __init__(self, code, role_order=100, priority=100, enabled=True, healthy=True):
        self.code = code
        self.role_order = role_order
        self.priority = priority
        self.enabled = enabled
        self.healthy = healthy


class _Member:
    """关联表成员行（B2.1 引入）。"""

    def __init__(self, provider_code, sort_order=100, enabled=True):
        self.provider_code = provider_code
        self.sort_order = sort_order
        self.enabled = enabled


class _QueryMock:
    """模拟 SQLAlchemy 链式查询。

    按 model 类名分派返回值，够用于 resolve_tier 的三条查询:
      LlmRoleConfig → role（first）
      LlmRoleMember → members（all，按 sort_order）
      LlmProvider   → provs（all，已按 enabled+healthy 过滤）
    """

    def __init__(self, role, members, provs):
        self._role, self._members, self._provs = role, list(members), list(provs)
        self._model_name = None

    def query(self, model):
        self._model_name = getattr(model, "__name__", str(model))
        return self

    def filter(self, *a, **kw):
        # 模拟 filter(enabled.is_(True), healthy.is_(True)) 的可用性过滤
        if self._model_name == "LlmProvider":
            self._provs = [p for p in self._provs if p.enabled and p.healthy]
        if self._model_name == "LlmRoleMember":
            self._members = [m for m in self._members if m.enabled]
        return self

    def order_by(self, *a):
        if self._model_name == "LlmRoleMember":
            self._members = sorted(self._members, key=lambda m: m.sort_order)
        elif self._model_name == "LlmProvider":
            self._provs = sorted(self._provs, key=lambda p: (p.role_order, p.priority))
        return self

    def first(self):
        return self._role if self._model_name == "LlmRoleConfig" else None

    def all(self):
        if self._model_name == "LlmRoleMember":
            return self._members
        if self._model_name == "LlmProvider":
            return self._provs
        return []


def _db(role, members=(), provs=()):
    return _QueryMock(role, members, provs)


@pytest.fixture
def policy(monkeypatch):
    def _set(v):
        monkeypatch.setattr(settings, "LLM_CLOUD_POLICY", v)
    return _set


# ─────────────────────────────────────────────────────────────────────
class TestResolveFromDb:
    def test_members_table_order_wins(self, policy):
        """关联表 sort_order 决定顺序（界面上下箭头即改此值）。"""
        policy("explicit_only")
        db = _db(_Role(), members=[
            _Member("talon-gmk-qwen2.5-7b", sort_order=2),
            _Member("talon-gmk-qwen3-coder-30b", sort_order=1),
        ], provs=[_Prov("talon-gmk-qwen2.5-7b"), _Prov("talon-gmk-qwen3-coder-30b")])
        chain = llm_tiers.resolve_tier("assistant", db=db)
        assert chain == ["talon-gmk-qwen3-coder-30b", "talon-gmk-qwen2.5-7b"]

    def test_members_override_source_constant(self, policy):
        """关联表配置应覆盖源码常量（管理员可改，无需发版）。"""
        policy("explicit_only")
        db = _db(_Role(), members=[_Member("custom-model-a", 1)],
                 provs=[_Prov("custom-model-a")])
        chain = llm_tiers.resolve_tier("assistant", db=db)
        assert chain == ["custom-model-a"]
        assert llm_tiers.TIERS["assistant"]["primary"] not in chain

    def test_disabled_provider_excluded(self, policy):
        """关联表成员但 provider 被禁用 → 不在链中。"""
        policy("explicit_only")
        db = _db(_Role(), members=[_Member("bad", 1), _Member("good", 2)],
                 provs=[_Prov("bad", enabled=False), _Prov("good")])
        chain = llm_tiers.resolve_tier("assistant", db=db)
        assert chain == ["good"]

    def test_empty_members_falls_back_to_legacy_column(self, policy):
        """关联表无记录 → 回退旧单值列（兼容迁移前配置）。"""
        policy("explicit_only")
        db = _db(_Role(), members=[], provs=[_Prov("legacy-col-model")])
        chain = llm_tiers.resolve_tier("assistant", db=db)
        assert chain == ["legacy-col-model"]

    def test_disabled_role_falls_back(self, policy):
        """角色 enabled=false → 视为未配置，回退常量。"""
        policy("explicit_only")
        db = _db(_Role(enabled=False), members=[_Member("should-not-use")])
        chain = llm_tiers.resolve_tier("assistant", db=db)
        assert "should-not-use" not in chain
        assert chain[0] == llm_tiers.TIERS["assistant"]["primary"]

    def test_no_db_uses_constant(self, policy):
        """db=None → 与 B1 行为完全一致（向后兼容）。"""
        policy("explicit_only")
        assert llm_tiers.resolve_tier("assistant") == \
            llm_tiers.resolve_tier("assistant", db=None)

    def test_db_query_failure_falls_back(self, policy, monkeypatch):
        """查库异常不应阻塞业务 → 回退常量。"""
        policy("explicit_only")

        class _Boom:
            def query(self, *a, **kw):
                raise RuntimeError("db down")

        chain = llm_tiers.resolve_tier("assistant", db=_Boom())
        assert chain[0] == llm_tiers.TIERS["assistant"]["primary"]


class TestRoleLevelCloudGate:
    """角色级 allow_cloud 与全局策略形成双重管控（取交集，保守优先）。"""

    def test_role_disallows_cloud_even_when_global_auto(self, policy):
        """⭐ 关键: 全局 auto 但角色 false → 仍不出域。"""
        policy("auto")
        db = _db(_Role(allow_cloud=False), members=[_Member("local-model", 1)],
                 provs=[_Prov("local-model")])
        chain = llm_tiers.resolve_tier("assistant", db=db)
        assert llm_tiers.CLOUD_FALLBACK not in chain

    def test_role_allows_cloud_and_global_allows(self, policy):
        """两者都允许 → 云端加入链尾。"""
        policy("auto")
        db = _db(_Role(allow_cloud=True), members=[_Member("local-model", 1)],
                 provs=[_Prov("local-model")])
        chain = llm_tiers.resolve_tier("assistant", db=db)
        assert chain == ["local-model", llm_tiers.CLOUD_FALLBACK]

    def test_global_never_beats_role_allow(self, policy):
        """全局 never 优先级最高 —— 角色允许也没用。"""
        policy("never")
        db = _db(_Role(allow_cloud=True), members=[_Member("local-model", 1)],
                 provs=[_Prov("local-model")])
        chain = llm_tiers.resolve_tier("assistant", db=db, include_cloud=True)
        assert llm_tiers.CLOUD_FALLBACK not in chain

    def test_constant_path_unaffected_by_role_gate(self, policy):
        """回退常量路径无角色配置（role_allow_cloud=None）→ 不阻拦。"""
        policy("auto")
        chain = llm_tiers.resolve_tier("assistant")   # 无 db
        assert llm_tiers.CLOUD_FALLBACK in chain


class TestMultiRoleSupport:
    """⭐ B2.1 核心价值：同一 provider 可属多个角色，顺序互相独立。

    这正是 B2 单值列方案被实测证伪的场景 ——
    coder-30b 需同时是 assistant 首选 与 specialist 备选。
    """

    def test_same_provider_in_two_roles_independent_order(self, policy):
        policy("explicit_only")
        # assistant: coder-30b(1) → 7b(2)
        assert llm_tiers.resolve_tier("assistant", db=_db(
            _Role(), members=[_Member("talon-gmk-qwen3-coder-30b", 1),
                              _Member("talon-gmk-qwen2.5-7b", 2)],
            provs=[_Prov("talon-gmk-qwen3-coder-30b"), _Prov("talon-gmk-qwen2.5-7b")],
        )) == ["talon-gmk-qwen3-coder-30b", "talon-gmk-qwen2.5-7b"]

        # specialist: 27b(1) → coder-30b(2)   ← 同一 provider，顺序不同
        assert llm_tiers.resolve_tier("specialist", db=_db(
            _Role(), members=[_Member("talon-gmk-qwen3.8-27b", 1),
                              _Member("talon-gmk-qwen3-coder-30b", 2)],
            provs=[_Prov("talon-gmk-qwen3.8-27b"), _Prov("talon-gmk-qwen3-coder-30b")],
        )) == ["talon-gmk-qwen3.8-27b", "talon-gmk-qwen3-coder-30b"]

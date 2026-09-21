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
    def __init__(self, code, role_order=100, priority=100):
        self.code = code
        self.role_order = role_order
        self.priority = priority


class _QueryMock:
    """模拟 SQLAlchemy 的链式查询（够用于本测试的两条查询）。"""

    def __init__(self, role, provs):
        self._role, self._provs = role, provs
        self._model = None

    def query(self, model):
        self._model = model
        return self

    def filter(self, *a, **kw):
        return self

    def order_by(self, *a):
        # 按 (role_order, priority) 排序，模拟 DB 的 ORDER BY
        self._provs = sorted(self._provs, key=lambda p: (p.role_order, p.priority))
        return self

    def first(self):
        return self._role

    def all(self):
        return self._provs


def _db(role, provs):
    return _QueryMock(role, provs)


@pytest.fixture
def policy(monkeypatch):
    def _set(v):
        monkeypatch.setattr(settings, "LLM_CLOUD_POLICY", v)
    return _set


# ─────────────────────────────────────────────────────────────────────
class TestResolveFromDb:
    def test_db_order_wins(self, policy):
        """DB 中 role_order 决定顺序（界面上下箭头即改此值）。"""
        policy("explicit_only")
        db = _db(_Role(), [
            _Prov("talon-gmk-qwen2.5-7b", role_order=20),
            _Prov("talon-gmk-qwen3-coder-30b", role_order=10),
        ])
        chain = llm_tiers.resolve_tier("assistant", db=db)
        assert chain == ["talon-gmk-qwen3-coder-30b", "talon-gmk-qwen2.5-7b"]

    def test_db_overrides_source_constant(self, policy):
        """DB 配置应覆盖源码常量（管理员可改，无需发版）。"""
        policy("explicit_only")
        db = _db(_Role(), [_Prov("custom-model-a", role_order=1)])
        chain = llm_tiers.resolve_tier("assistant", db=db)
        assert chain == ["custom-model-a"]
        # 源码常量里的首选不应出现
        assert llm_tiers.TIERS["assistant"]["primary"] not in chain

    def test_empty_role_falls_back_to_constant(self, policy):
        """角色存在但无启用成员 → 回退常量（避免"配空了就没模型可用"）。"""
        policy("explicit_only")
        db = _db(_Role(), [])
        chain = llm_tiers.resolve_tier("assistant", db=db)
        assert chain[0] == llm_tiers.TIERS["assistant"]["primary"]

    def test_disabled_role_falls_back(self, policy):
        """角色 enabled=false → 视为未配置，回退常量。"""
        policy("explicit_only")
        db = _db(_Role(enabled=False), [_Prov("should-not-use")])
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
        db = _db(_Role(allow_cloud=False), [_Prov("local-model")])
        chain = llm_tiers.resolve_tier("assistant", db=db)
        assert llm_tiers.CLOUD_FALLBACK not in chain

    def test_role_allows_cloud_and_global_allows(self, policy):
        """两者都允许 → 云端加入链尾。"""
        policy("auto")
        db = _db(_Role(allow_cloud=True), [_Prov("local-model")])
        chain = llm_tiers.resolve_tier("assistant", db=db)
        assert chain == ["local-model", llm_tiers.CLOUD_FALLBACK]

    def test_global_never_beats_role_allow(self, policy):
        """全局 never 优先级最高 —— 角色允许也没用。"""
        policy("never")
        db = _db(_Role(allow_cloud=True), [_Prov("local-model")])
        chain = llm_tiers.resolve_tier("assistant", db=db, include_cloud=True)
        assert llm_tiers.CLOUD_FALLBACK not in chain

    def test_constant_path_unaffected_by_role_gate(self, policy):
        """回退常量路径无角色配置（role_allow_cloud=None）→ 不阻拦。"""
        policy("auto")
        chain = llm_tiers.resolve_tier("assistant")   # 无 db
        assert llm_tiers.CLOUD_FALLBACK in chain

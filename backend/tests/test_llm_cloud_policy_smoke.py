"""出域管控 —— import 路径与调用链冒烟测试（2026-09-21）

⚠️ 为什么需要这个文件（教训）:

  `test_llm_cloud_policy_db.py` 把方法 mock 掉了，结果漏掉一个**生产才暴露的崩溃**:

      _list_cloud_providers() 里写的是 `from app.database import SessionLocal`
      正确路径应为 `from app.core.database import SessionLocal`
      → ModuleNotFoundError（部署后才被发现）

  **mock 掉的东西测不到它内部的 import 错误。**
  但直接用真实 DB 又依赖测试库 schema（且曾因此险酿事故，见 repo memory）。

  本文件取折中：**只 mock 掉「数据层」，保留真实 import 与函数体结构** ——
  能抓住 import 路径错误、函数名拼写、参数签名不匹配，
  但不触碰任何真实数据库。

设计原则（与 test_llm_cloud_policy_db.py 的分工）:
  - 本文件: 真实调用 + mock 数据层 → 抓 import/签名/结构错误
  - 那个文件: 全 mock → 抓策略逻辑分支
"""
import inspect
import re

from app.services import llm_provider_service


class TestImportPaths:
    """⭐ 核心: 抓 import 路径错误 —— 这正是生产崩溃的根因。"""

    def test_module_importable(self):
        assert llm_provider_service is not None

    def test_session_local_import_path_correct(self):
        """⭐ 回归: 源码里不得出现错误的 `from app.database import ...`。

        该路径不存在（正确为 `app.core.database`）—— 2026-09-21 生产崩溃根因。
        """
        src = inspect.getsource(llm_provider_service)
        bad = [
            ln.strip() for ln in src.splitlines()
            if "from app.database import" in ln or "import app.database" in ln
        ]
        assert not bad, f"发现错误的 import 路径（应为 app.core.database）: {bad}"

    def test_no_other_wrong_module_paths(self):
        """全模块扫描可疑的 app.xxx 顶层 import。"""
        src = inspect.getsource(llm_provider_service)
        known = {"core", "models", "services", "api", "schemas", "utils", "middleware"}
        bad = []
        for m in re.finditer(r"from (app\.\w+) import", src):
            top = m.group(1).split(".")[1]
            if top not in known:
                bad.append(m.group(0))
        assert not bad, f"可疑的 import 路径: {bad}"


class TestFunctionSignatures:
    """函数签名稳定性（前端/调用方依赖）。"""

    def test_get_cloud_policy_accepts_optional_db(self):
        sig = inspect.signature(llm_provider_service.get_cloud_policy)
        assert "db" in sig.parameters
        assert sig.parameters["db"].default is None, "db 应有默认值 None"

    def test_update_cloud_policy_params(self):
        sig = inspect.signature(llm_provider_service.update_cloud_policy)
        for name in ("db", "policy", "username", "require_confirm"):
            assert name in sig.parameters, f"缺少参数 {name}"

    def test_policy_helpers_exist(self):
        assert callable(llm_provider_service._policy_row_exists)
        assert callable(llm_provider_service._policy_updated_by)
        assert callable(llm_provider_service._list_cloud_providers)

    def test_role_member_functions_exist(self):
        for fn in ("add_role_member", "remove_role_member", "set_role_member_order",
                   "list_roles", "update_role"):
            assert callable(getattr(llm_provider_service, fn)), f"缺少 {fn}"


class TestCloudPolicyShapeWithMockedData:
    """mock 数据层，验证 get_cloud_policy 的返回结构（不碰真实 DB）。"""

    def test_shape(self, monkeypatch):
        monkeypatch.setattr(llm_provider_service, "_policy_row_exists", lambda db: False)
        monkeypatch.setattr(llm_provider_service, "_policy_updated_by", lambda db: None)
        monkeypatch.setattr(llm_provider_service, "_list_cloud_providers", lambda: [
            {"code": "deepseek-v4-flash", "model": "deepseek-v4-flash", "enabled": True},
        ])
        out = llm_provider_service.get_cloud_policy(None)
        assert out["policy"] in ("never", "explicit_only", "auto")
        assert out["source"] == "env"
        assert isinstance(out["cloud_providers"], list)
        assert len(out["cloud_providers"]) == 1
        assert len(out["options"]) == 3
        for o in out["options"]:
            assert {"value", "label", "desc"} <= set(o)

    def test_source_db_when_row_exists(self, monkeypatch):
        monkeypatch.setattr(llm_provider_service, "_policy_row_exists", lambda db: True)
        monkeypatch.setattr(llm_provider_service, "_policy_updated_by", lambda db: "tanzk")
        monkeypatch.setattr(llm_provider_service, "_list_cloud_providers", lambda: [])
        out = llm_provider_service.get_cloud_policy(None)
        assert out["source"] == "db"
        assert out["updated_by"] == "tanzk"


class TestListCloudProvidersBody:
    """`_list_cloud_providers` 的函数体路径（mock SessionLocal）。"""

    def test_body_executes_with_mocked_session(self, monkeypatch):
        """⭐ 该函数体曾在生产崩溃（错误 import）—— 此测试执行其完整函数体。

        只要函数体能跑到底（不抛 ModuleNotFoundError/AttributeError），
        就说明 import 路径与查询链结构正确。
        """
        class _FakeQuery:
            def filter(self, *a, **kw):
                return self

            def all(self):
                return []

        class _FakeSession:
            def query(self, *a, **kw):
                return _FakeQuery()

            def close(self):
                pass

        import app.core.database as core_db
        monkeypatch.setattr(core_db, "SessionLocal", lambda: _FakeSession())

        rows = llm_provider_service._list_cloud_providers()
        assert rows == []

    def test_body_maps_rows_to_dicts(self, monkeypatch):
        """函数体能把 ORM 行映射成 {code, model, enabled} —— 界面依赖该结构。"""
        class _Row:
            code = "deepseek-v4-flash"
            model = "deepseek-v4-flash"
            enabled = True
            is_cloud = True

        class _FakeQuery:
            def filter(self, *a, **kw):
                return self

            def all(self):
                return [_Row()]

        class _FakeSession:
            def query(self, *a, **kw):
                return _FakeQuery()

            def close(self):
                pass

        import app.core.database as core_db
        monkeypatch.setattr(core_db, "SessionLocal", lambda: _FakeSession())

        rows = llm_provider_service._list_cloud_providers()
        assert rows == [{"code": "deepseek-v4-flash",
                         "model": "deepseek-v4-flash",
                         "enabled": True}]

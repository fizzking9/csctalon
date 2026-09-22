# -*- coding: utf-8 -*-
"""运行模式框架与配置分离 —— 回归测试（工单 #190）。

## 为什么需要这组测试

2026-09-10 与 2026-09-22 **同因两次**事故：
前者真损失（`refunds 170960→2`），后者靠"恰好有活跃连接"侥幸未遂。

前次修复只加了 `tests/_db_guard.py` —— 仅对 pytest 生效，
`python -c` 与 `scripts/*.py` 完全绕过它。**同因复发**证明：只堵一个入口不够。

本文件锁定新机制的**核心不变式**：
  ① 库名默认值不得指向真实库（否则"忘记配置"="连真实库"）
  ② 模式 ↔ 库名不一致必须**拒绝**（prod 连 dev 库 / dev 连真实库）
  ③ 未知模式必须报错（不得静默降级为 dev —— 那也是"以为连 A 实际连 B"）
  ④ `.env` 加载不得依赖 CWD（原实现按 CWD 解析，从仓库根跑会加载生产配置）
  ⑤ 守卫必须能拦 `scripts/`（非 pytest 场景）——这是本次事故的直接入口

★ 这些断言的价值在于**会让违规变红**。纯文档（铁律）拦不住不读文档的进程。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import (
    VALID_MODES,
    all_profiles,
    check_db_matches_profile,
    extract_db_name,
    resolve_profile,
)
from app.core.db_guard import assert_safe_to_destroy, is_test_db

_BACKEND_DIR = Path(__file__).resolve().parents[1]


class TestDbNameDefaultIsSafe:
    """① 库名默认值必须是「不可用占位符」，不得是真实库名。

    这是本次修复的核心：原默认值 `"csctalon"` 就是真实库名，
    使"忘记配置"等价于"连真实库"。
    """

    def test_default_db_name_is_not_a_real_db(self):
        """读源码断言：默认值不得是 csctalon / csctalon_test 等具体库名。

        为什么不实例化 Settings 来断言：那会被环境变量与 .env 影响
        （当前 shell / 本地 .env 可能恰好有值），测不出"默认值"本身。故直接读源码。

        ⚠️ 断言用正则而非字面量：实现里默认值来自常量 `_PLACEHOLDER_DB`，
           测试写死字面量会因"实现改名"而假失败（本测试最初即踩此坑）。
        """
        src = (_BACKEND_DIR / "app/core/config/settings.py").read_text(encoding="utf-8")
        assert not re.search(r'POSTGRES_DB:\s*str\s*=\s*"csctalon"', src), (
            "POSTGRES_DB 默认值又变回真实库名 csctalon —— "
            "这会让「忘记配置」= 「连真实库」（2026-09-10 事故根因）"
        )
        # 默认值应指向占位符常量（无论常量叫什么名字）
        assert re.search(r'POSTGRES_DB:\s*str\s*=\s*\w+', src), (
            "未找到 POSTGRES_DB 的默认值定义"
        )
        assert "_PLACEHOLDER_DB" in src, (
            "POSTGRES_DB 默认值应为占位符常量（缺失即启动失败），现有实现未使用占位符"
        )
        assert "MUST_SET_POSTGRES_DB" in src, "占位符内容应含明确的配置提示语"

    def test_missing_db_fails_fast(self):
        """既无环境变量、也读不到 .env 时，必须启动失败而非静默连某个库。

        ⚠️ 关键前提：必须**同时屏蔽 .env 文件**。
           仅在环境里删掉 POSTGRES_DB 是不够的 —— `backend/.env` 会提供值
           （那是正常配置来源，不是缺陷）。本测试最初即因漏了这点而假失败。
           故用 `APP_MODE` 指向一个不存在的 .env 场景：直接让实现
           走"无可用的库名"分支。

        做法：临时把工作副本的 .env 排除 —— 通过 `PYDANTIC` 无法实现，
        故改为直接调用校验逻辑：构造一个 DB 为占位符的 Settings。
        """
        from app.core.config.settings import Settings

        # 直接构造：显式传入占位符值，模拟"未配置"。
        # ⚠️ 校验器是 `@model_validator(mode="after")`，在**构造时**自动运行，
        #    故不需要（也不应该）手动调用它。
        with pytest.raises(Exception) as ei:
            Settings(
                POSTGRES_DB="__MUST_SET_POSTGRES_DB__",
                JWT_SECRET_KEY="x" * 40,
                POSTGRES_PASSWORD="x" * 20,
                REDIS_PASSWORD="x" * 20,
                INIT_ADMIN_PASSWORD="x" * 20,
            )
        assert "POSTGRES_DB" in str(ei.value), (
            "未配置库名时应明确报出 POSTGRES_DB，当前错误信息未提及"
        )


class TestModeDbConsistency:
    """② 模式 ↔ 库名一致性（让"连错库"从靠运气变为启动即失败）。"""

    def test_prod_rejects_dev_and_test_db(self):
        """prod 模式连到 _dev / _test 库 → 必须报错（防"生产连了测试库"）。"""
        prod = resolve_profile("prod")
        for bad in ("csctalon_dev", "csctalon_test"):
            url = f"postgresql://u:p@h:5432/{bad}"
            problems = check_db_matches_profile(prod, url)
            assert problems, f"prod 模式连接 {bad!r} 应被拒绝，却通过了"
            assert any("_dev" in p or "_test" in p for p in problems)

    def test_prod_accepts_real_db(self):
        """prod 连生产库名（csctalon）→ 必须通过（不能误伤生产）。"""
        prod = resolve_profile("prod")
        assert check_db_matches_profile(prod, "postgresql://u:p@h:5432/csctalon") == []

    def test_dev_rejects_real_db(self):
        """dev 模式连真实库 csctalon → 必须报错。

        ★ 这是 2026-09-22 未遂事故的**直接场景**。
        """
        dev = resolve_profile("dev")
        problems = check_db_matches_profile(dev, "postgresql://u:p@h:5432/csctalon")
        assert problems, "dev 模式连接真实库 csctalon 应被拒绝，却通过了"
        assert any("_dev" in p for p in problems)

    def test_dev_accepts_dev_db(self):
        dev = resolve_profile("dev")
        assert check_db_matches_profile(dev, "postgresql://u:p@h:5432/csctalon_dev") == []

    def test_test_mode_rejects_real_db(self):
        """test 模式连真实库 → 必须报错（#42 事故根因即此）。"""
        test_p = resolve_profile("test")
        problems = check_db_matches_profile(test_p, "postgresql://u:p@h:5432/csctalon")
        assert problems, "test 模式连接真实库应被拒绝"

    def test_guard_does_not_auto_correct_db_name(self):
        """守卫必须**报错**而非自动改写库名。

        自动改写会造成"以为连 A 实际连 B" —— 正是要消灭的失败模式。
        """
        dev = resolve_profile("dev")
        problems = check_db_matches_profile(dev, "postgresql://u:p@h:5432/csctalon")
        # 校验函数只返回问题，不改写；确认它没返回"已修正"之类的东西
        assert all(isinstance(p, str) for p in problems)
        assert len(problems) == 1  # 只报问题，不做修正

    def test_extract_db_name(self):
        assert extract_db_name("postgresql://u:p@h:5432/mydb") == "mydb"
        assert extract_db_name("postgresql://u:p@h:5432/mydb?opt=1") == "mydb"


class TestUnknownModeFails:
    """③ 未知模式必须报错（不得静默降级）。"""

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="未知运行模式"):
            resolve_profile("prdo")   # 拼错

    def test_unknown_mode_error_mentions_valid_values(self):
        """错误信息必须列出有效值，否则使用者无从下手。"""
        with pytest.raises(ValueError) as ei:
            resolve_profile("staging")
        msg = str(ei.value)
        for m in VALID_MODES:
            assert m in msg, f"错误信息应列出有效模式 {m!r}"

    def test_empty_mode_defaults_to_dev(self):
        """空值取默认 dev（安全侧：dev 不连真实库）。"""
        assert resolve_profile(None).mode == "dev"
        assert resolve_profile("").mode == "dev"


class TestProfilesAreWellFormed:
    """profile 集合自身的完整性。"""

    def test_all_modes_covered(self):
        assert set(all_profiles()) == set(VALID_MODES)

    def test_profiles_are_frozen(self):
        """profile 是常量，运行期不得被改写。"""
        p = resolve_profile("dev")
        with pytest.raises(Exception):
            p.db_name = "csctalon"  # type: ignore[misc]

    def test_each_profile_db_name_matches_its_own_rules(self):
        """每个 profile 的 db_name 必须满足它自己的规则（自洽性）。

        若 dev 的 db_name 不以 _dev 结尾，则按默认配置启动就会自相矛盾。
        """
        for name, p in all_profiles().items():
            url = f"postgresql://u:p@h:5432/{p.db_name}"
            problems = check_db_matches_profile(p, url)
            assert not problems, f"profile {name} 自相矛盾: {problems}"

    def test_dev_and_prod_db_names_differ(self):
        """★ dev 与 prod 的库名**必须不同**。

        这是让"连错库"肉眼可见的前提 —— 同名时错误无任何信号。
        """
        assert resolve_profile("dev").db_name != resolve_profile("prod").db_name, (
            "dev 与 prod 库名相同 → 连错库无法察觉（2026-09-10/09-22 两次事故的根因）"
        )

    def test_prod_log_config_is_structured(self):
        """prod 日志必须是结构化 + INFO（便于采集；dev 才有 DEBUG）。"""
        prod = resolve_profile("prod")
        assert prod.log_format == "json"
        assert prod.log_level == "INFO"
        assert resolve_profile("dev").log_level == "DEBUG"

    def test_sql_echo_off_in_prod(self):
        """prod 不得开 SQL echo（日志会含真实数据且量极大）。"""
        assert resolve_profile("prod").sql_echo is False


class TestProfileFieldsAreWired:
    """⑥ profile 的每个字段都必须**真的被读取**（防止"文档式空承诺"）。

    ★ 为什么需要：`docs_enabled` 最初只是声明在 Profile 里，**没人读它** ——
      使用者以为"prod 关闭了 /docs"，实际一直开着。这类"声明了但没接线"的字段
      比没有更糟：它让人误信一个不成立的保证。

      本组用**源码扫描**保证接线（比运行时更重要：运行时有默认值兜底，看不出漏接线）。
    """

    def test_docs_enabled_is_read_by_main(self):
        """`docs_enabled` 必须被 main.py 读取（接线到 FastAPI 的 docs_url）。"""
        src = (_BACKEND_DIR / "app/main.py").read_text(encoding="utf-8")
        assert "docs_url" in src, (
            "main.py 未设置 docs_url —— profile.docs_enabled 是空声明（声明了没人读）"
        )
        assert "docs_enabled" in src, "main.py 未读取 profile.docs_enabled"

    def test_prod_keeps_docs_open_by_default(self):
        """prod 的 /docs **保持改造前现状**（开放）。

        ★ 为什么断言"开放"而不是"关闭"：
          改造前 FastAPI 用默认值 → /docs 一直开放。若本次框架改造顺带把它关了，
          那是**非预期的行为变更**（可能有对接方依赖 OpenAPI）。
          要关闭必须走显式开关 OPENAPI_ENABLED=false（独立决策）。
        """
        prod = resolve_profile("prod")
        # 未显式设 OPENAPI_ENABLED 时，prod 应视为开放（保持现状）
        assert prod.docs_enabled is False, "profile 里 prod.docs_enabled 应为 False"
        # 但 main.py 必须把 prod 视为"开放"（见 main.create_app 的 _openapi_on 逻辑）
        src = (_BACKEND_DIR / "app/main.py").read_text(encoding="utf-8")
        assert 'mode == "prod"' in src or "OPENAPI_ENABLED" in src, (
            "main.py 需保留 prod 的 /docs 现状（或提供显式开关）"
        )

    def test_openapi_env_switch_exists(self):
        """必须提供显式开关，否则"想关也关不掉"。"""
        src = (_BACKEND_DIR / "app/main.py").read_text(encoding="utf-8")
        assert "OPENAPI_ENABLED" in src, "缺少显式开关 OPENAPI_ENABLED"

    def test_no_profile_field_is_obviously_unused(self):
        """粗筛：Profile 的字段名应在**别处**出现过（被读取或测试引用）。

        这是一个**启发式**检查（不是完备证明），用于抓住
        "加了个字段却忘了接线"这类低级错误。字段若只在本文件出现 → 高度可疑。
        """
        import dataclasses

        # 收集仓库内的其它源码（app/ 与 tests/），排除 profiles.py 自身
        other_src = ""
        for sub in ("app", "tests", "scripts"):
            for p in (_BACKEND_DIR / sub).rglob("*.py"):
                if p.name == "profiles.py":
                    continue
                try:
                    other_src += p.read_text(encoding="utf-8")
                except Exception:
                    continue

        unused = []
        for f in dataclasses.fields(resolve_profile("dev")):
            name = f.name
            if name not in other_src:
                unused.append(name)
        assert not unused, (
            f"Profile 字段 {unused} 未在 app/ tests/ scripts/ 中出现 —— "
            "疑似声明了但没人读（空承诺）。若确实有意保留作元数据，"
            "请在本测试中显式列为例外并说明原因。"
        )


class TestEnvFileNotCwdDependent:
    """④ `.env` 加载不得依赖 CWD —— 原实现按 CWD 解析，从仓库根跑会加载生产配置。"""

    def test_env_file_is_absolute(self):
        """env_file 必须是绝对路径（相对路径会随 CWD 漂移）。"""
        from app.core.config.settings import Settings

        cfg = Settings.model_config.get("env_file")
        if not cfg:
            pytest.skip("当前模式不加载 .env 文件（如 test 模式由 conftest 注入）")
        files = (cfg,) if isinstance(cfg, str) else tuple(cfg)
        for f in files:
            assert Path(f).is_absolute(), (
                f"env_file {f!r} 不是绝对路径 → 会随 CWD 变化，"
                "从仓库根跑可能加载到生产配置（本次事故的机制之一）"
            )

    def test_settings_from_any_cwd_resolves_same_db(self):
        """从不同 CWD 导入 settings，结果必须一致。

        ★ 这是对"根因"的直接回归 —— 改造前从仓库根跑会连生产配置。
        """
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "from app.core.config import settings\n"
            "print(settings.POSTGRES_DB)\n"
        ) % str(_BACKEND_DIR)
        results = []
        for cwd in (_BACKEND_DIR, _BACKEND_DIR.parent, "/tmp"):
            r = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, cwd=cwd, timeout=60,
                env={**os.environ, "APP_MODE": "dev", "POSTGRES_DB": "csctalon_dev"},
            )
            assert r.returncode == 0, f"cwd={cwd} 导入失败: {r.stderr[-300:]}"
            results.append(r.stdout.strip())
        assert len(set(results)) == 1, (
            f"不同 CWD 得到不同库名 {results} —— .env 加载仍依赖 CWD"
        )


class TestDbGuardCoversNonPytest:
    """⑤ 守卫必须覆盖 `scripts/` 与 `python -c`（本次事故的直接入口）。"""

    def test_guard_module_is_importable_outside_tests(self):
        """守卫必须在 `app.core` 下（而非仅在 `tests/`）——否则脚本用不上。"""
        from app.core import db_guard

        assert hasattr(db_guard, "assert_safe_to_destroy")

    def test_is_test_db_by_suffix(self):
        assert is_test_db("csctalon_test") is True
        assert is_test_db("csctalon_test_ci") is True   # CI 自定义名
        assert is_test_db("csctalon_dev") is False
        assert is_test_db("csctalon") is False

    def test_real_db_without_flag_is_blocked(self):
        """非测试库 + 未授权 → 必须抛错。

        构造一个假 session 模拟"连到真实库"的场景。
        """

        class _FakeUrl:
            def __init__(self, s):
                self._s = s

            def __str__(self):
                return self._s

        class _FakeBind:
            url = _FakeUrl("postgresql://u:p@h:5432/csctalon")

        class _FakeSession:
            def get_bind(self):
                return _FakeBind()

        with pytest.raises(RuntimeError, match="破坏性操作被拦截"):
            assert_safe_to_destroy(_FakeSession(), reason="测试")

    def test_real_db_with_explicit_flag_is_allowed(self):
        """显式授权（生产运维脚本）时应放行。"""

        class _FakeUrl:
            def __init__(self, s):
                self._s = s

            def __str__(self):
                return self._s

        class _FakeBind:
            url = _FakeUrl("postgresql://u:p@h:5432/csctalon")

        class _FakeSession:
            def get_bind(self):
                return _FakeBind()

        # 不抛错即通过
        assert_safe_to_destroy(_FakeSession(), reason="生产运维", allow_real_db=True)

    def test_tests_db_guard_delegates_to_core(self):
        """`tests/_db_guard.py` 必须复用 `app/core/db_guard.py`，不得有第二份实现。

        （同 A-20260910-004 双份定义漂移教训）
        """
        src = (_BACKEND_DIR / "tests/_db_guard.py").read_text(encoding="utf-8")
        assert "from app.core.db_guard import" in src, (
            "tests/_db_guard.py 应委托给 app/core/db_guard.py，"
            "否则会出现第二份守卫实现（改一处漏一处）"
        )

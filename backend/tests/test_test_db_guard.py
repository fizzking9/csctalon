# -*- coding: utf-8 -*-
"""测试库守卫回归锁 (2026-09-10 数据事故修复, 工单 #42)

## 事故回顾
conftest 原先用 `os.environ.setdefault("POSTGRES_DB", ...)`。setdefault 在变量
**已存在时不覆盖** —— 调用方 shell 里若残留 `POSTGRES_DB=csctalon`(常见于执行过
`set -a && . ./.env` 加载配置跑分析脚本), 测试就会连到**开发库**。
而多个用例的清理逻辑含"无条件整表 DELETE"(如
`session.execute(Model.__table__.delete())`), 在开发库上执行 = 清空整张业务表。
实测后果: refunds 170960→2、refund_wide→64313、manual_refunds→0。
(已按备份逐表恢复完毕)

## 本测试锁定的修复
A. conftest 必须**强制覆盖** POSTGRES_DB(不用 setdefault);
B. conftest 必须提供 `_assert_test_database` 库名守卫, 且 engine fixture 真正调用它。
"""
from __future__ import annotations

import os


def test_conftest_forces_db_name_not_setdefault():
    """★A: 库名必须被强制覆盖写, 不得依赖 setdefault(否则上游残留值会遮蔽)

    用 AST 判定**真实代码**(注释/文档字符串里提到旧写法属正常, 不算违规)。
    """
    import ast
    import pathlib

    src = pathlib.Path(__file__).with_name("conftest.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    forced_db = False
    setdefault_db = False
    for node in ast.walk(tree):
        # os.environ["POSTGRES_DB"] = ...
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (isinstance(tgt, ast.Subscript)
                        and getattr(tgt.value, "attr", None) == "environ"
                        and getattr(tgt.slice, "value", None) == "POSTGRES_DB"):
                    forced_db = True
        # os.environ.setdefault("POSTGRES_DB", ...)
        if isinstance(node, ast.Call):
            fn = node.func
            if (getattr(fn, "attr", None) == "setdefault"
                    and getattr(fn.value, "attr", None) == "environ"
                    and node.args
                    and getattr(node.args[0], "value", None) == "POSTGRES_DB"):
                setdefault_db = True

    assert forced_db, "库名必须强制赋值 os.environ['POSTGRES_DB'] = ..."
    assert not setdefault_db, \
        "回归: 又用回 os.environ.setdefault('POSTGRES_DB', ...), 污染环境下会连到开发库"


def test_db_name_actually_forced_at_import():
    """★A 端到端: 导入 conftest 后, POSTGRES_DB 必为测试库(即使上游设了别的值)"""
    import tests.conftest  # noqa: F401  (已在本进程导入过, 断言最终值即可)

    assert os.environ["POSTGRES_DB"] in ("csctalon_test",) or \
        os.environ["POSTGRES_DB"].endswith("_test"), \
        f"POSTGRES_DB 未被强制为测试库: {os.environ['POSTGRES_DB']}"


def test_guard_rejects_dev_database():
    """★B: 守卫必须拒绝非测试库(否则含整表 DELETE 的用例会清空真实业务表)"""
    import tests.conftest as C

    for bad in (
        "postgresql://u:p@127.0.0.1:5432/csctalon",
        "postgresql://u:p@db:5432/csctalon",
        "postgresql://u:p@127.0.0.1:5432/csctalon?sslmode=disable",
    ):
        try:
            C._assert_test_database(bad)
        except RuntimeError:
            continue
        raise AssertionError(f"守卫未拦截非测试库: {bad}")


def test_guard_allows_test_database():
    """★B: 守卫必须放行测试库(否则 CI 全红)"""
    import tests.conftest as C

    C._assert_test_database("postgresql://u:p@127.0.0.1:5432/csctalon_test")
    C._assert_test_database("postgresql://u:p@ci-pg:5432/csctalon_test")


def test_engine_fixture_invokes_guard():
    """★B 接线锁: 守卫必须真的挂在 engine fixture 上, 否则形同虚设

    2026-10-07: 改用 AST 静态检查。pytest 9 + Python 3.14 下 fixture 包装对象
    已不可 inspect.getsource(OSError: could not get source code), 静态检查
    同样锁定"engine fixture 必须调用库名守卫"且跨版本稳定。
    """
    import ast
    import pathlib

    src = pathlib.Path(__file__).with_name("conftest.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_engine":
            assert "_assert_test_database" in ast.unparse(node), \
                "engine fixture 未调用库名守卫"
            break
    else:
        raise AssertionError("conftest 中找不到 _engine fixture")


# ---- 第二轮防御: 禁止裸整表删除 (事故善后加固) ----

def test_no_bare_table_delete_in_tests():
    """★回归锁: 测试代码不得再出现裸 `X.__table__.delete()`(必须经 safe_wipe)。

    背景: 裸整表删除在测试库无害, 但一旦连到真实库就是"清空整张业务表"。
    2026-09-10 事故即由此放大(refunds 170960→2 等)。
    统一走 tests._db_guard.safe_wipe, 它在执行前**再校验一次**连接的是测试库。
    """
    import ast
    import pathlib

    tests_dir = pathlib.Path(__file__).parent
    offenders: list[str] = []
    for f in sorted(tests_dir.glob("test_*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        lines = f.read_text(encoding="utf-8").splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            # X.__table__.delete()
            if not (isinstance(fn, ast.Attribute) and fn.attr == "delete"):
                continue
            inner = fn.value
            if not (isinstance(inner, ast.Attribute) and inner.attr == "__table__"):
                continue
            src_line = lines[node.lineno - 1].strip()
            if src_line.startswith("#"):
                continue  # 注释
            # safe_wipe 内部实现不在 tests/test_*.py, 不会命中
            offenders.append(f"{f.name}:{node.lineno}: {src_line}")

    assert not offenders, (
        "发现裸整表删除(请改用 tests._db_guard.safe_wipe):\n  " + "\n  ".join(offenders)
    )


def test_db_guard_rejects_non_test_db():
    """safe_wipe 的库名校验必须真的拒绝非测试库"""
    from tests._db_guard import assert_test_database

    for bad in ("postgresql://u:p@127.0.0.1:5432/csctalon",
                "postgresql://u:p@db:5432/csctalon"):
        try:
            assert_test_database(bad)
        except RuntimeError:
            continue
        raise AssertionError(f"未拦截非测试库: {bad}")
    assert_test_database("postgresql://u:p@127.0.0.1:5432/csctalon_test")

# -*- coding: utf-8 -*-
"""数据库操作守卫 —— 破坏性操作前的最后一道校验（脚本与测试共用）。

## 与 `app/core/config/guard.py` 的分工

| 模块 | 时机 | 断言内容 |
|---|---|---|
| `config/guard.py` | **启动期**（进程级，一次） | "我连的库符合我的运行模式" |
| `db_guard.py`（本模块） | **操作期**（每次破坏性调用） | "这个操作允许在此库执行" |

两者互补：启动期拦住"整个进程连错库"，操作期拦住"在正确进程里执行了越界操作"。

## 为什么需要抽到 `app/core/`（原只在 `tests/` 下）

原实现 `tests/_db_guard.py` **只对 pytest 生效** —— `scripts/*.py` 与
`python -c` 完全绕过它。而 2026-09-22 未遂事故正是发生在 `python -c` 场景。

> 2026-09-10 事故：多个用例的"无条件整表 DELETE"连到开发库执行 →
> `refunds 170960→2`、`refund_wide→64313`、`manual_refunds→0`。
> 该事故的**同因复发**（2026-09-22）证明：只堵一个入口不够。

本模块抽出通用实现，`tests/_db_guard.py` 与 `scripts/*` 共同复用 ——
避免出现"第二份守卫"（同 A-20260910-004 双份定义漂移教训）。

## 用法

    from app.core.db_guard import assert_safe_to_destroy
    assert_safe_to_destroy(db, reason="清理测试残留")
    db.execute(...)   # 破坏性操作
"""
from __future__ import annotations

import os

# 测试库的期望名（可由 TEST_POSTGRES_DB 覆盖，与 conftest.py 同口径）
_EXPECTED_TEST_DB = os.environ.get("TEST_POSTGRES_DB", "csctalon_test")

# 判定"这是测试库"的后缀特征。用后缀而非全等，是为了兼容 CI 自定义库名
# （如 csctalon_test_ci）与本地 migtest 等衍生库。
_TEST_DB_SUFFIX = "_test"


def current_db_name(db) -> str:
    """取 session 实际连接的库名。

    ★ 用 `get_bind()` 取**真实**连接而非 `settings.POSTGRES_DB`：
      配置可能被环境变量/默认值影响，只有实际绑定才是真相。
    """
    engine = db.get_bind()
    return str(engine.url).rsplit("/", 1)[-1].split("?", 1)[0]


def is_test_db(db_name: str) -> bool:
    """库名是否属于测试库。

    判据：名称中含 `_test`（下划线 + test，即"test"作为独立段出现）。
    覆盖常见变体：`csctalon_test`、`csctalon_test_ci`（CI 自定义）、`csctalon_test2`。

    ★ 取值取向：**偏严格**。
      误判为测试库（放过）→ 可能清空真实数据，代价不可逆；
      误判为真实库（拦下）→ 使用者显式传 `--allow-real-db` 即可，代价可见且可控。
      故不用 `"test" in db_name` 这种宽判据（那会把 `csctalon_latest` 之类的名字也算进来）。
    """
    if db_name == _EXPECTED_TEST_DB:
        return True
    # 要求下划线紧邻 test —— "csctalon_migtest" 不含 "_test"，故不匹配（偏严格是有意的）
    return "_test" in db_name


def assert_safe_to_destroy(db, *, reason: str = "", allow_real_db: bool = False) -> None:
    """破坏性操作前的守卫。

    参数:
      db:            SQLAlchemy Session（取其真实连接判断）
      reason:        操作说明，失败时输出以便定位
      allow_real_db: 是否允许对**非测试库**执行。默认 False。
                     ⚠️ 该参数由调用方显式传入（如脚本的 `--allow-real-db` 开关），
                        **不得**由环境变量隐式决定 —— 否则又变成"靠环境碰运气"。

    抛错条件：目标是真实库（非测试库）且未显式授权。
    """
    db_name = current_db_name(db)
    if is_test_db(db_name) or allow_real_db:
        return
    raise RuntimeError(
        f"⛔ 破坏性操作被拦截：目标库 {db_name!r} 不是测试库，且未显式授权。\n"
        f"   操作说明: {reason or '(未提供)'}\n"
        f"   完整连接: {db.get_bind().url}\n\n"
        "  ★ 为什么拦截：\n"
        "    2026-09-10 曾有测试的『无条件整表 DELETE』连到开发库执行，\n"
        "    导致 refunds 170960→2、refund_wide→64313、manual_refunds→0。\n\n"
        "  若确实要对真实库执行（生产运维脚本）：\n"
        "    请显式传 allow_real_db=True（脚本层对应 --allow-real-db 开关），\n"
        "    而**不要**通过环境变量绕过 —— 那正是本机制要消灭的『靠环境碰运气』。\n\n"
        "  排查: 终端是否残留 POSTGRES_DB / APP_MODE / ENV 环境变量?\n"
        "        (常见于执行过 `set -a && . ./.env`)"
    )

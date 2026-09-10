# -*- coding: utf-8 -*-
"""测试数据库守卫 —— 防止测试清理逻辑误伤真实库 (2026-09-10 数据事故善后)。

## 背景(事故)
多个用例的清理逻辑含"无条件整表 DELETE", 例如:

    for cls in (RefundRecordReport, Refund):
        session.execute(cls.__table__.delete())      # ← 全表删!

这在**测试库**上没问题(测试库本就是一次性数据), 但一旦因环境变量污染连到
**开发库/生产库**, 就是"清空整张业务表"。
2026-09-10 实测后果: refunds 170960→2、refund_wide→64313、manual_refunds→0。

## 双层防御
- 第一层(conftest): 导入期强制覆盖 POSTGRES_DB + engine fixture 断言库名。
- 第二层(本模块): **每次破坏性清理前**再校验一次当前 session 实际连的库。
  即使有人在测试里自建 engine / 绕过 conftest, 也无法在非测试库上执行全表删除。

## 用法
把 `session.execute(Model.__table__.delete())` 换成:

    from tests._db_guard import safe_wipe
    safe_wipe(session, Model)                      # 全表清空(仅测试库允许)
    safe_wipe(session, Device, where=Device.sn.like("SN-D-%"))   # 带条件(推荐)

## 为什么保留"全表清空"能力
部分用例需要表处于"空"状态才能断言(如"导入前该表无数据")。硬性禁止全表删除会
让这些用例无法编写。故策略是"**限定库** + **强制显式**", 而非"禁止"。
"""
from __future__ import annotations

import os

_EXPECTED_TEST_DB = os.environ.get("TEST_POSTGRES_DB", "csctalon_test")


def assert_test_database(url: str) -> None:
    """断言 URL 指向测试库, 否则抛错(与 conftest 同口径)。"""
    db_name = str(url).rsplit("/", 1)[-1].split("?", 1)[0]
    if db_name != _EXPECTED_TEST_DB:
        raise RuntimeError(
            f"⛔ 危险操作被拦截: 期望连接 {_EXPECTED_TEST_DB!r}, 实际 {db_name!r}。\n"
            f"   完整连接: {url}\n"
            f"   本操作会清空真实业务表, 已中止。\n"
            f"   排查: 终端是否残留 POSTGRES_DB / TEST_POSTGRES_DB 环境变量?\n"
            f"        (常见于执行过 `set -a && . ./.env`) —— 请 `unset POSTGRES_DB` 后重跑。"
        )


def _current_db_url(session) -> str:
    """取 session 实际连接的库 URL。"""
    engine = session.get_bind()
    return str(engine.url)


def safe_wipe(session, model, *, where=None, reason: str = "") -> int:
    """在**确认是测试库**的前提下执行删除(替代裸 `Model.__table__.delete()`)。

    参数:
      session: SQLAlchemy Session
      model:   目标模型类
      where:   可选 SQLAlchemy 条件。**强烈建议给出**, 把影响面限定到本用例造的数据;
               不给出时是全表清空(仅测试库允许)。
      reason:  可选说明, 便于失败时定位是哪个用例触发的

    返回: 影响行数(execute 的 rowcount; 视驱动可能为 None → 返回 -1)
    """
    assert_test_database(_current_db_url(session))
    stmt = model.__table__.delete()
    if where is not None:
        stmt = stmt.where(where)
    res = session.execute(stmt)
    return int(res.rowcount) if res.rowcount is not None else -1


def safe_wipe_many(session, models, *, reason: str = "") -> None:
    """批量全表清空多个模型(对应原 `for cls in (...): ...delete()` 写法)。"""
    assert_test_database(_current_db_url(session))
    for m in models:
        session.execute(m.__table__.delete())

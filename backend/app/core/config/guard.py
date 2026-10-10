# -*- coding: utf-8 -*-
"""模式一致性守卫 —— 让「模式 ↔ 实际连接」不一致时**必然失败**。

## 为什么需要（本次改造的核心）

改造前的问题不是"没有配置"，而是**没有任何一层会察觉模式与行为不一致**：

    本地 backend/.env : POSTGRES_DB=csctalon
    生产 /opt/.env    : POSTGRES_DB=csctalon     ← 同名！
    config.py         : POSTGRES_DB = "csctalon" ← 默认值也是生产库名

连错库时，配置、日志、界面**没有任何异常信号** → 只能靠"运气"。
（2026-09-10 已造成 `refunds 170960→2`；2026-09-22 同因复发。）

本模块把「模式」变成**可验证的断言**：

    prod 模式 + 库名含 _dev/_test  → 拒绝启动
    dev  模式 + 库名不符合 _dev    → 拒绝启动（除非显式豁免）
    test 模式 + 库名不含 _test     → 拒绝启动

这样两类致命错配（prod 连 dev 库、dev 连真实库）**都在启动期炸掉**，
而不是静默运行到某个破坏性操作才暴露。

## 与 `app/core/db_guard.py` 的分工

- 本模块 = **启动期**守卫（进程级、一次性）：断言"我连的库符合我的模式"
- `db_guard.py` = **操作期**守卫（每破坏性调用一次）：断言"这个操作允许在此库执行"

两者互补：启动期拦住"整个进程连错库"，操作期拦住"在正确进程里执行了越界操作"。
"""
from __future__ import annotations

from .profiles import Profile


def extract_db_name(database_url: str) -> str:
    """从 SQLAlchemy URL 取库名。

    形如 `postgresql+psycopg2://user:pw@host:5432/dbname?opts` → `dbname`
    """
    # 去掉协议头，取最后一个 / 之后、? 之前的部分
    tail = str(database_url).rsplit("/", 1)[-1]
    return tail.split("?", 1)[0]


def check_db_matches_profile(profile: Profile, database_url: str) -> list[str]:
    """校验「模式 ↔ 库名」一致性，返回问题列表（空 = 通过）。

    为什么要返回列表而非直接抛错：便于测试逐条断言，也让调用方
    可以决定是"拒绝启动"还是"仅告警"（当前实现一律拒绝启动）。
    """
    db = extract_db_name(database_url)
    problems: list[str] = []

    # 规则 1：不得命中该模式禁止的后缀（如 prod 不得连 *_dev / *_test）
    for suffix in profile.forbidden_db_suffixes:
        if db.endswith(suffix):
            problems.append(
                f"模式 {profile.mode} 禁止连接以 {suffix!r} 结尾的库，实际库名 {db!r}"
            )

    # 规则 2：必须命中该模式的期望后缀（如 dev 必须以 _dev 结尾）
    if profile.required_db_suffix and not db.endswith(profile.required_db_suffix):
        problems.append(
            f"模式 {profile.mode} 要求库名以 {profile.required_db_suffix!r} 结尾，"
            f"实际库名 {db!r}"
        )

    return problems

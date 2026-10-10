# -*- coding: utf-8 -*-
"""配置包 —— 运行模式驱动的配置体系（2026-09-22 起）。

## 为什么从单文件变成包

原 `app/core/config.py` 的问题不是"代码太长"，而是**没有一个地方能回答
「当前模式下应该连哪个库、写哪个目录、用哪个日志级别」**——
这些取值散落各处，靠"此刻环境里恰好有什么变量"决定。

现在把它们收敛为 `profiles.py` 的**一处定义**，`settings.py` 据此加载。

## 模块职责

| 模块 | 职责 |
|---|---|
| `profiles.py` | 模式 → 全部环境差异的**单一真值来源**（库名/日志/目录/开关） |
| `guard.py`    | 启动期校验「模式 ↔ 实际连接的库」一致，不一致**拒绝启动** |
| `settings.py` | `Settings` 类本体（由 profile 驱动加载 .env） |
| `__init__.py` | 向后兼容导出 `settings` |

## 向后兼容

全仓 175 个文件、62 处导入**全部**是 `from app.core.config import settings`，
本文件保持该入口不变 → **零改动**。

`Settings` 的字段名与语义也全部保留（仅 `POSTGRES_DB` 默认值从真实库名改为占位符，
这是本次安全修复的核心，见 `settings.py` 注释）。
"""
from __future__ import annotations

from .guard import (
    check_db_matches_profile,
    extract_db_name,
)
from .logging_setup import setup_logging
from .profiles import VALID_MODES, Profile, all_profiles, resolve_profile
from .settings import Settings, get_settings, settings

__all__ = [
    # 向后兼容主入口
    "settings",
    "Settings",
    "get_settings",
    # 模式体系
    "Profile",
    "resolve_profile",
    "all_profiles",
    "VALID_MODES",
    # 守卫（供 scripts/ 与启动期调用）
    "check_db_matches_profile",
    "extract_db_name",
    # 日志（按 profile 初始化）
    "setup_logging",
]

# -*- coding: utf-8 -*-
"""脚本引导 —— 让 `scripts/` 下的脚本自动获得**运行模式**与**破坏性操作守卫**。

## 为什么需要（2026-09-22 未遂事故的直接教训）

原防御链只覆盖 pytest：
    pytest 内     → ✅ conftest.py + tests/_db_guard.py
    scripts/*.py  → ❌ 无守卫
    python -c     → ❌ 无守卫

而 2026-09-22 的未遂事故**正是发生在 `python -c`**，2026-09-10 的真实损失
（`refunds 170960→2`）则涉及会话残留环境变量。**只堵一个入口不够** ——
这是同因复发的根本原因。

## 用法（脚本开头）

    from scripts._bootstrap import bootstrap          # 或按下方 sys.path 写法
    db = bootstrap(mode="test")                       # 或 "dev"
    # db 已通过"模式↔库名"校验；破坏性操作前再调 assert_safe_to_destroy(db)

    # 需要操作真实库的运维脚本（如修生产数据）：
    db = bootstrap(mode="prod", allow_real_db=sys.argv[1:2] == ["--allow-real-db"])

## 设计要点

1. **必须在 import app 之前设环境变量** —— `Settings` 在导入期即读取模式并校验，
   晚了就来不及（那就等于没有守卫）。
2. **`allow_real_db` 必须由调用方显式传入**（如 `--allow-real-db` 开关），
   **不得**从环境变量隐式推导 —— 否则又回到"靠环境碰运气"的老路。
3. 本模块**不导入 app**（只用标准库），这样才能安全地在设环境变量之前被导入。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# backend/ 目录（本文件位于 backend/scripts/_bootstrap.py）
_BACKEND_DIR = Path(__file__).resolve().parents[1]

# 各模式的库名（与 app/core/config/profiles.py 保持一致）。
# ⚠️ 此处**复制**了库名而非从 profiles 导入 —— 因为本模块要在"设置环境变量"
#    阶段使用，而此时 `app` 尚未导入（导入 app 会立即触发模式校验，
#    那时环境变量还没设好，必然失败）。这是**有意的**启动顺序约束。
#    两处一致性由 `tests/test_runtime_mode_framework.py` 锁定。
_DEFAULT_DB_FOR_MODE = {
    "dev": "csctalon_dev",
    "test": "csctalon_test",
    "prod": "csctalon",
}


def bootstrap(
    mode: str | None = None,
    *,
    db_name: str | None = None,
    allow_real_db: bool = False,
    require_db: bool = True,
):
    """为脚本准备环境并返回一个数据库会话。

    参数:
      mode:          dev / test / prod。None 时读环境变量 APP_MODE/ENV，默认 dev。
      db_name:       显式指定库名；None 时按 mode 取默认值。
      allow_real_db: 是否允许连**真实库**。默认 False。
                     ⚠️ 必须由调用方显式传（通常来自 `--allow-real-db` 参数），
                        **不得**由环境变量隐式决定。
      require_db:    是否需要数据库。False 时仅设环境变量、不建会话
                     （供纯文件操作类脚本使用）。

    返回: SQLAlchemy Session（require_db=False 时返回 None）
    """
    resolved_mode = mode or os.environ.get("APP_MODE") or os.environ.get("ENV") or "dev"
    resolved_mode = resolved_mode.strip().lower()

    if resolved_mode not in _DEFAULT_DB_FOR_MODE:
        raise SystemExit(
            f"⛔ 未知运行模式 {resolved_mode!r}"
            f"（有效值: {', '.join(_DEFAULT_DB_FOR_MODE)}）"
        )

    # ── 决定库名 ──
    target_db = db_name or os.environ.get("POSTGRES_DB") or _DEFAULT_DB_FOR_MODE[resolved_mode]

    # ★ 真实库护栏：非 prod 模式**不得**连真实库（除非显式授权）。
    is_real_db = not target_db.endswith(("_dev", "_test")) and "_test" not in target_db
    if is_real_db and resolved_mode != "prod" and not allow_real_db:
        raise SystemExit(
            f"⛔ 模式 {resolved_mode!r} 不允许连接真实库 {target_db!r}。\n"
            f"   若确需（生产运维脚本），请显式传 allow_real_db=True"
            f"（脚本参数通常为 --allow-real-db）。\n"
            f"   ★ 为什么拦：2026-09-10 曾有脚本/测试误连开发库执行整表 DELETE，\n"
            f"     导致 refunds 170960→2；2026-09-22 同因复发（未遂）。"
        )

    # ── 设环境变量（必须在 import app 之前）──
    os.environ["APP_MODE"] = resolved_mode
    os.environ["POSTGRES_DB"] = target_db
    # 让 `import app.*` 能在脚本直跑时找到包
    if str(_BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(_BACKEND_DIR))

    if not require_db:
        return None

    from app.core.database import SessionLocal

    return SessionLocal()


def real_db_requested(argv: list[str] | None = None) -> bool:
    """从命令行参数判断是否显式请求操作真实库。

    用法: `bootstrap(mode="prod", allow_real_db=real_db_requested())`

    ★ 支持两种写法（`--allow-real-db` 与 `--allow-real-db=true`），
      并**要求精确匹配** —— 不用 `startswith` 以免 `--allow-real-db-fake` 之类误判。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    return any(a == "--allow-real-db" or a == "--allow-real-db=true" for a in args)

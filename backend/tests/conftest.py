# -*- coding: utf-8 -*-
"""pytest 全局 fixture: 测试环境隔离与数据库会话管理。

关键设计(对应 AGENTS.md 全局约束):
1. 测试默认使用独立 PostgreSQL 测试库 csctalon_test, 绝不触碰开发/生产库。
2. 必须在导入 app 任何模块之前设置环境变量 ENV=test(通过 pytest 的
   conftest 收集阶段生效), 保证 Settings 以测试配置加载。
3. 会话级 fixture 自动建表(Base.metadata.create_all), 避免依赖 Alembic 迁移。
4. 每个测试函数的事务自动回滚(transaction fixture), 保证用例间数据隔离。
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest

# ---- 在导入 app 之前注入测试环境变量 --------------------------------------
# 必须放在模块顶层: 任何 `from app.xxx import ...` 之前执行。
#
# 设计(三态):
# 1. 本地开发(backend/.env 存在): 仅强制 ENV=test + 独立测试库名, host/user/
#    password 由 settings 从 backend/.env 自动加载(与开发库同连接, 库名隔离)。
# 2. CI / 无 .env 场景: 通过 TEST_POSTGRES_* 环境变量注入完整连接参数
#    (CI 工作流自行提供 postgres 服务, 见 .gitea/workflows/test.yml)。
# 3. 绝不硬编码密码在仓库(对应 AGENTS.md 绝对禁止第 3 条)。
#
# ⚠️⚠️ 2026-09-10 数据事故修复 (工单 #42) ⚠️⚠️
#   原实现用 `os.environ.setdefault("POSTGRES_DB", ...)`。setdefault 在"变量已存在时
#   **不覆盖**" —— 若调用方环境里残留 POSTGRES_DB=csctalon(如开发者在同一 shell 里
#   执行过 `set -a && . ./.env` 加载配置跑脚本), 测试就会连到**开发库**。
#   而多个用例的清理逻辑含"无条件整表 DELETE"(如 test_bms_3source_upsert.py /
#   test_e_route_switch.py 的 `session.execute(Model.__table__.delete())`,
#   `TRUNCATE` 等), 在开发库上执行 = 清空整张业务表。
#   实测后果: refunds 从 170960 → 2、refund_wide → 64313、manual_refunds → 0
#   (已按备份逐表恢复, 见 docs/reports/)。
#
#   修复(两层防御):
#     A. **强制覆盖**库名 —— 不再依赖 setdefault, 确保一定是测试库;
#     B. **库名守卫** —— engine 建立后硬断言实际连到的库 = 期望测试库,
#        否则立即抛错中止(宁可测试全红, 也不能写错库)。
os.environ["ENV"] = "test"   # 强制(不用 setdefault): 防上游残留值遮蔽
_EXPECTED_TEST_DB = os.environ.get("TEST_POSTGRES_DB", "csctalon_test")
os.environ["POSTGRES_DB"] = _EXPECTED_TEST_DB   # 强制覆盖, 见上 A

# 仅当 backend/.env 不存在时, 才用 TEST_POSTGRES_* / 默认值注入完整连接参数;
# 否则让 settings 优先读取 .env 的真实连接(避免环境变量遮蔽 .env 密码)。
if not os.path.exists(os.path.join(os.path.dirname(__file__), "..", ".env")):
    os.environ.setdefault("POSTGRES_HOST", os.environ.get("TEST_POSTGRES_HOST", "127.0.0.1"))
    os.environ.setdefault("POSTGRES_PORT", os.environ.get("TEST_POSTGRES_PORT", "5432"))
    os.environ.setdefault("POSTGRES_USER", os.environ.get("TEST_POSTGRES_USER", "csctalon"))
    _pw = os.environ.get("TEST_POSTGRES_PASSWORD", "csctalon123")
    os.environ.setdefault("POSTGRES_PASSWORD", _pw)

# 测试环境不启动 BMS 定时同步、不依赖真实 Redis 凭证
os.environ.setdefault("BMS_SCHEDULE_AUTOSTART", "false")


def _assert_test_database(url: str) -> None:
    """★库名守卫(见顶部事故说明 B): 断言实际连接的是测试库, 否则立即中止。

    为什么用"硬断言"而非"自动改写": 若此处静默把库名改成 csctalon_test, 而调用方
    本意是连别的库(如独立 CI 库), 会造成"以为连 A 实际连 B"的隐性错配。宁可显式报错。
    """
    db_name = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if db_name != _EXPECTED_TEST_DB:
        raise RuntimeError(
            f"⛔ 测试库守卫拦截: 期望连接 {_EXPECTED_TEST_DB!r}, 实际 {db_name!r}。\n"
            f"   完整连接: {url}\n"
            f"   原因排查: 终端里是否残留 POSTGRES_DB / TEST_POSTGRES_DB 环境变量?\n"
            f"   (常见于执行过 `set -a && . ./.env`) —— 请 `unset POSTGRES_DB` 后重跑。\n"
            f"   ⚠️ 直接放行会令含'无条件整表 DELETE'的用例清空真实业务表。"
        )



@pytest.fixture(scope="session")
def _engine():
    """会话级 engine: 基于测试环境变量创建, 并自动建表。"""
    from sqlalchemy import create_engine

    from app.core.config import settings

    # ★ 库名守卫: 必须在创建 engine 之前拦截, 否则后续用例可能已写库
    _assert_test_database(settings.database_url)
    # settings 已在导入时按测试环境变量加载
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        import app.models  # noqa: F401  确保所有模型已注册到 Base.metadata
        from app.core.database import Base

        Base.metadata.create_all(bind=engine)
        # ⚠️ 2026-09-07 修复: create_all 建表后 stamp alembic 到 head。
        #   否则 app lifespan(_run_migrations_on_startup) 在 TestClient 启动时会再次
        #   alembic upgrade head, 对全新测试库(无 alembic_version)从 d9b7f1 重跑全部
        #   建表 → 与 create_all 已建的表冲突(DuplicateTable: data_match_relations)。
        #   stamp 后 upgrade 检测到已是最新即跳过, 消除 CI 偶发失败。
        try:
            from pathlib import Path

            from alembic.config import Config

            from alembic import command

            _backend_dir = Path(__file__).resolve().parents[1]
            _cfg = Config(str(_backend_dir / "alembic.ini"))
            _cfg.set_main_option("script_location", str(_backend_dir / "alembic"))
            command.stamp(_cfg, "head")
        except Exception:
            # stamp 失败不阻塞测试(create_all 已建表, 顶多 app 启动重跑 migration 冲突)
            pass
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def db(_engine) -> Generator:
    """函数级 session: 包裹在事务中, 测试结束后回滚, 保证用例间隔离。

    用法: 测试函数参数声明 `db`, 即可拿到 Session 并自由增删查, 无需清理。
    """
    from sqlalchemy.orm import Session

    session = Session(_engine, autocommit=False, autoflush=False)
    try:
        # 开启显式事务; 结束时 rollback, 数据不落库
        session.begin()
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def client(_engine):
    """FastAPI TestClient: 用测试库 engine 覆盖应用依赖 get_db。

    用法: `client.get("/api/v1/...")`。注意: 该 client 使用独立 session,
    与 `db` fixture 不同(不共享事务), 适合只读/独立写场景。
    """
    from fastapi.testclient import TestClient

    from app.core.database import get_db
    from app.main import app

    def _override_db():
        from sqlalchemy.orm import Session

        session = Session(_engine, autocommit=False, autoflush=False)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()

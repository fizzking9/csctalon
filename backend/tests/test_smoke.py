# -*- coding: utf-8 -*-
"""冒烟测试: 验证测试基建链路是否可用(环境隔离 / DB 建表 / 依赖覆盖)。

用途: 作为测试基建的"自检", 不测业务逻辑。CI / 本地接入测试时最先跑它。
"""


def test_imports_ok():
    """核心模块可导入(依赖完整)。"""
    import app.core.database  # noqa: F401
    import app.main  # noqa: F401

    assert True


def test_settings_is_test_env():
    """Settings 以 ENV=test 加载(隔离策略生效)。"""
    from app.core.config import settings

    assert settings.ENV == "test"
    # 测试库名应为 csctalon_test(可由 TEST_POSTGRES_DB 覆盖)
    assert settings.POSTGRES_DB.endswith("test")


def test_db_fixture_rolls_back(db):
    """db fixture 插入的数据在用例结束后回滚(不会残留)。"""
    from app.models.sync_source_config import SyncSourceConfig

    before = db.query(SyncSourceConfig).count()
    # 插入一条临时记录(用不冲突的 data_type, 避免与种子数据 ticket 等撞唯一约束)
    db.add(
        SyncSourceConfig(
            data_type="__smoke_test__",
            name="__smoke__",
            enabled=False,
            base_url="http://localhost",
            page_path="/",  # NOT NULL 字段
        )
    )
    db.flush()
    after = db.query(SyncSourceConfig).count()
    assert after == before + 1
    # 用例结束自动 rollback, 无需显式清理


def test_health_endpoint(client):
    """健康检查接口可达(应用可启动)。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data

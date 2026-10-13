"""Alembic 环境配置"""

import logging
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.config import settings
from app.core.database import Base
from app.models import *  # noqa: F401,F403  # 注册所有模型

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    # 仅在独立 CLI 运行(如命令行 alembic upgrade head)时才让 alembic 接管日志;
    # 由 app.main lifespan 触发时应用已配置好日志(root logger 已有 handler),
    # 此时再 fileConfig 会用 alembic.ini(root=WARN+console)替换 root handlers,
    # 导致应用后续 INFO 日志(含"应用启动完成"/预热/文件落盘)全部消失。
    if not logging.getLogger().handlers:
        fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    # #319: 模型已分布到 public/ods 两 schema, 必须 include_schemas=True,
    # 否则 autogen 只反射 public, 会把 ods 表误判为"待新建"而生成重复建表。
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True,
        compare_type=True, include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata,
            compare_type=True, include_schemas=True,  # #319: 同上
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

"""数据库连接与会话管理"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

# PostgreSQL 长连接 TCP keepalive 参数:防止 idle 连接被服务端/负载均衡器/运营商网络
# 中间设备静默断开(表现为 sqlalchemy.exc.OperationalError: server closed the connection
# unexpectedly)。常见 PG 默认 tcp_keepalives_idle=2h,对我们的长事务/长任务来说太长。
_PG_CONNECT_ARGS = {
    "keepalives": 1,
    "keepalives_idle": 120,   # 120s 无数据后开始发送 keepalive probe
    "keepalives_interval": 15,  # 每 15s 重试一次 probe
    "keepalives_count": 6,      # 最多 6 次失败后判定对端断开(120+6×15=210s 内断链)
}

engine = create_engine(
    settings.database_url,
    connect_args=_PG_CONNECT_ARGS,
    pool_pre_ping=True,
    pool_recycle=1800,  # 30分钟回收连接,避免 PostgreSQL 服务端 idle 连接被踢断
    pool_use_lifo=True,
    pool_size=10,
    max_overflow=20,
    echo=settings.SQL_ECHO,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类"""

    pass


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖:获取数据库会话,请求结束自动关闭"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

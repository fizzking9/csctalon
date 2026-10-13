"""数据库连接与会话管理"""

from collections.abc import Generator

from sqlalchemy import create_engine, event
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


# #319 数据分层: 21 张信源表已从 public 移入 ods schema。
# 仓库内存在 300+ 处历史裸 SQL(text() 直写表名, 如 refund.py 单文件 60+ 处),
# 逐一改写风险高且噪音大;改为连接级 search_path=public,ods:
#   - public 在前 → 系统业务表(86-21 张)解析行为完全不变;
#   - ods 兜底 → 裸 SQL 命中 ods.<t> 等价于原 public.<t>, 42P01 风险从根上消除。
# ORM/迁移仍走显式 schema 限定,不依赖 search_path。
@event.listens_for(engine, "connect")
def _set_search_path(dbapi_conn, _connection_record):
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute("SET search_path TO public, ods")
        # ⚠️ 必须 commit: psycopg2 下 SET 处于隐式事务中, 而连接池 reset_on_return=rollback
        # 会在连接归还时回滚掉未提交的 SET(表现为"首次取用正常, 复用后 42P01")。
        dbapi_conn.commit()
    finally:
        cursor.close()

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

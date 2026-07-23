"""FastAPI 应用入口"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.models import *  # noqa: F401,F403  # 注册所有模型

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期:启动时初始化数据库与默认管理员"""
    # 开发环境自动建表(生产用 alembic)
    if settings.ENV == "dev":
        Base.metadata.create_all(bind=engine)
        logger.info("开发环境:已自动创建表结构")
    _init_admin()
    logger.info("应用启动完成")
    yield
    logger.info("应用关闭")


def _init_admin() -> None:
    """初始化默认管理员账号"""
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.models.user import RoleEnum, User

    db = SessionLocal()
    try:
        admin = db.scalar(select(User).where(User.username == settings.INIT_ADMIN_USERNAME))
        if not admin:
            admin = User(
                username=settings.INIT_ADMIN_USERNAME,
                display_name="管理员",
                hashed_password=hash_password(settings.INIT_ADMIN_PASSWORD),
                role=RoleEnum.ADMIN.value,
            )
            db.add(admin)
            db.commit()
            logger.info(f"已创建默认管理员: {settings.INIT_ADMIN_USERNAME} / {settings.INIT_ADMIN_PASSWORD}")
    finally:
        db.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="客服中心管理系统 - 内部工作流与数据辅助平台",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix="/api/v1")

    @app.get("/health", tags=["系统"])
    def health():
        return {"status": "ok", "version": settings.APP_VERSION}

    return app


app = create_app()

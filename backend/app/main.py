"""FastAPI 应用入口"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import SessionLocal
from app.middleware.operation_log import OperationLogMiddleware
from app.models import *  # noqa: F401,F403  # 注册所有模型

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# backend/ 目录（alembic.ini 与 alembic/ 所在位置）
_BACKEND_DIR = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期:启动时执行迁移并初始化默认管理员"""
    _run_migrations_on_startup()
    _init_admin()
    _init_data_sources_and_presets()
    _start_bms_scheduler()
    logger.info("应用启动完成")
    yield
    # 关闭时停止调度器
    from app.services.bms_scheduler import bms_scheduler
    bms_scheduler.stop()
    logger.info("应用关闭")


def _run_migrations_on_startup() -> None:
    """启动时执行 alembic upgrade head（统一走迁移，不再用 create_all）"""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")
    logger.info("数据库迁移已执行至 head")


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


def _init_data_sources_and_presets() -> None:
    """启动时注册数据源元数据 + 预设分析视图(幂等)"""
    from app.services.data_source_registry import ensure_data_sources_registered
    from app.services.preset_dashboards_seed import ensure_preset_dashboards_registered
    from app.services.preset_views_seed import ensure_preset_views_registered
    from app.services.sync_source_config_service import ensure_sync_sources_registered

    db = SessionLocal()
    try:
        ds_stats = ensure_data_sources_registered(db)
        logger.info(f"数据源注册完成: sources={ds_stats['sources']}, fields={ds_stats['fields']}")
        pv_stats = ensure_preset_views_registered(db)
        logger.info(
            f"预设视图注册完成: presets={pv_stats['presets']}, "
            f"created={pv_stats['created']}, updated={pv_stats['updated']}"
        )
        pd_stats = ensure_preset_dashboards_registered(db)
        logger.info(
            f"预设看板注册完成: presets={pd_stats['presets']}, "
            f"created={pd_stats['created']}, updated={pd_stats['updated']}, "
            f"skipped_views={pd_stats['skipped_views']}"
        )
        ss_stats = ensure_sync_sources_registered(db)
        logger.info(f"同步源配置注册完成: created={ss_stats['created']}, total={ss_stats['total']}")
    except Exception as e:
        logger.error(f"数据源/预设视图注册失败: {e}", exc_info=True)
    finally:
        db.close()


def _start_bms_scheduler() -> None:
    """启动 BMS 定时同步调度器(如果配置了自动启动)"""
    if not settings.BMS_SCHEDULE_AUTOSTART:
        logger.info("BMS 定时同步未开启自动启动(BMS_SCHEDULE_AUTOSTART=False)")
        return
    from app.services.bms_scheduler import bms_scheduler
    bms_scheduler.start()
    logger.info("BMS 定时同步已自动启动")


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
    app.add_middleware(OperationLogMiddleware)

    # 静态文件: 挂载 uploads 目录,审计贴图等附件可通过 /uploads/... 访问
    from fastapi.staticfiles import StaticFiles

    uploads_path = Path(settings.UPLOAD_DIR)
    uploads_path.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=str(uploads_path)), name="uploads")

    app.include_router(api_router, prefix="/api/v1")

    @app.get("/health", tags=["系统"])
    def health():
        return {"status": "ok", "version": settings.APP_VERSION}

    return app


app = create_app()

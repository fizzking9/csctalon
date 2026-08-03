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
    """应用生命周期:启动时执行迁移并初始化默认管理员

    ⚠️ 多 worker (gunicorn -w N) 场景下, 每个 worker 都会调用 lifespan。
    若不加锁, 多个 worker 同时:
      ① 执行 alembic upgrade head (冲突少但会重复跑)
      ② 对 analysis_views / dashboards 表执行相同行的 UPSERT → Postgres 极易死锁 (PG kill 其中一个事务 → worker crash code=3)
      ③ 启动多个 bms_scheduler → 同一时间重复触发同步任务
    用 Postgres 应用级咨询锁 (Advisory Lock, key=99887766) 让第一个拿到锁的 worker 真正执行迁移/种子/调度器;
    其他 worker 等待数秒 (保证第一个 worker 完成 DDL/种子 UPSERT 提交) 后跳过即可。
    """
    import asyncio as _aio
    from sqlalchemy import text as _tx

    STARTUP_LOCK_KEY = 99887766
    lock_db = SessionLocal()
    got_lock = False
    try:
        # 非阻塞尝试拿锁: 第一个 worker 必拿到; 其他立刻 False
        r = lock_db.execute(_tx("SELECT pg_try_advisory_lock(:k)"), {"k": STARTUP_LOCK_KEY}).scalar()
        got_lock = bool(r)
    except Exception as _e:
        logger.warning(f"拿咨询锁失败(fallback 降级为无锁,可能重复执行): {_e}")
    finally:
        # 咨询锁 session 级持有,这里先不关,等 lifespan 结束统一关 (关了锁就释放了)
        pass

    try:
        if got_lock:
            logger.info(f"[lifespan] worker 持有启动锁(99887766),开始执行迁移/种子/调度器")
            # 单独包一层 try-except: 迁移/种子/调度器 任一失败都不要让 worker 崩掉 (gunicorn exit code 3 → master kill 全集群)
            # 只记录错误日志, 之后仍让 HTTP 服务正常启动。
            try:
                _run_migrations_on_startup()
            except Exception as _e:
                logger.error(f"[lifespan] 启动时迁移失败(非致命,服务继续启动): {_e}", exc_info=True)
            try:
                _init_admin()
            except Exception as _e:
                logger.error(f"[lifespan] 启动时初始化管理员失败(非致命): {_e}", exc_info=True)
            try:
                _init_data_sources_and_presets()
            except Exception as _e:
                logger.error(f"[lifespan] 启动时种子注册失败(非致命): {_e}", exc_info=True)
            try:
                _start_bms_scheduler()
            except Exception as _e:
                logger.error(f"[lifespan] 启动时调度器启动失败(非致命): {_e}", exc_info=True)
            # 主动 commit, 确保 UPSERT 的行对其他后续跳过的 worker 可见
            try:
                lock_db.commit()
            except Exception:
                pass
        else:
            # 没拿到锁 → 等一下, 确保持有锁的 worker 把迁移+种子做完(事务提交), 然后直接跳过
            logger.info("[lifespan] worker 未拿到启动锁,等待 6s 后跳过迁移/种子/调度器...")
            await _aio.sleep(6.0)
        logger.info("应用启动完成")
        yield
    finally:
        # 关闭时停止调度器 (无论哪个 worker, 都尝试 stop, scheduler.stop() 是幂等的)
        try:
            from app.services.bms_scheduler import bms_scheduler
            bms_scheduler.stop()
        except Exception:
            pass
        # 释放咨询锁 (若持有)
        try:
            if got_lock:
                lock_db.execute(_tx("SELECT pg_advisory_unlock(:k)"), {"k": STARTUP_LOCK_KEY})
                lock_db.commit()
        except Exception:
            pass
        try:
            lock_db.close()
        except Exception:
            pass
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
    """启动时注册数据源元数据 + 预设分类规则 + 预设分析视图(幂等)"""
    from app.services.data_source_registry import ensure_data_sources_registered
    from app.services.preset_classification_rules_seed import (
        ensure_preset_classification_rules_registered,
    )
    from app.services.preset_dashboards_seed import ensure_preset_dashboards_registered
    from app.services.preset_templates_seed import ensure_preset_templates_registered
    from app.services.preset_views_seed import ensure_preset_views_registered
    from app.services.sync_source_config_service import ensure_sync_sources_registered

    db = SessionLocal()
    try:
        ds_stats = ensure_data_sources_registered(db)
        logger.info(f"数据源注册完成: sources={ds_stats['sources']}, fields={ds_stats['fields']}")
        # 分类规则先于视图注册(视图通过 classification_rule_names 引用规则)
        cr_stats = ensure_preset_classification_rules_registered(db)
        logger.info(f"预设分类规则注册完成: rules={len(cr_stats)}, names={list(cr_stats.keys())}")
        pv_stats = ensure_preset_views_registered(db)
        logger.info(
            f"预设视图注册完成: presets={pv_stats['presets']}, "
            f"created={pv_stats['created']}, updated={pv_stats['updated']}, "
            f"skipped_rules={pv_stats.get('skipped_rules', 0)}"
        )
        pt_stats = ensure_preset_templates_registered(db)
        logger.info(
            f"预设模板注册完成: presets={pt_stats['presets']}, "
            f"created={pt_stats['created']}, updated={pt_stats['updated']}, skipped={pt_stats['skipped']}"
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

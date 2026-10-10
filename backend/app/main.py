"""FastAPI 应用入口"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ⚠️ 日志时间戳统一为北京时间(容器默认 TZ=UTC, 否则 asctime 落后 8h, 与前端/调度标签混淆)。
#    仅调整日志 Formatter 渲染, 不影响进程/业务用 datetime.now(timezone.utc) 的 UTC 存储语义。
_TZ_CN = timezone(timedelta(hours=8))


def _beijing_localtime(ts: float):
    return datetime.fromtimestamp(ts, _TZ_CN).timetuple()


logging.Formatter.converter = staticmethod(_beijing_localtime)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router

# ★ 2026-09-22: 日志由运行模式 Profile 决定（级别/格式）。
#   改造前此处硬编码 `level=logging.INFO` + 人类可读格式 —— 所有环境同一套：
#     · dev 看不到 DEBUG（排障最需要的信息被过滤）
#     · prod 用人类可读格式（不利于采集/告警系统解析）
#   现: dev=DEBUG+detailed / test=WARNING+plain / prod=INFO+json（见 profiles.py）。
#   ⚠️ setup_logging 内部会清空既有 handler 再重装，故此处覆盖 uvicorn 的默认配置。
from app.core.config import Profile, resolve_profile, settings, setup_logging
from app.core.database import SessionLocal
from app.middleware.cache_control import NoCacheMiddleware
from app.middleware.operation_log import OperationLogMiddleware
from app.models import *  # noqa: F401,F403  # 注册所有模型

_profile: Profile = resolve_profile(settings.ENV)
setup_logging(_profile, log_dir=_profile.log_dir)
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
            logger.info("[lifespan] worker 持有启动锁(99887766),开始执行迁移/种子/调度器")
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
                _cleanup_zombie_running_tasks()
            except Exception as _e:
                logger.warning(f"[lifespan] 清理僵尸任务失败(非致命): {_e}")
            try:
                # #48: 多 worker 部署检测(只 WARNING 不 fail-fast); 多 worker 时
                # 4 个 login 端点会返回 501 + CLI 降级命令(不阻断其他业务)。
                from app.services.shared_doc_login_service import check_single_worker
                check_single_worker()
            except Exception as _e:
                logger.warning(f"[lifespan] 共享文档登录 worker 检测失败(非致命): {_e}")
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
        # 夜批轮询停止(#258, stop 幂等)
        try:
            from app.services.batch_runner import batch_runner
            batch_runner.stop()
        except Exception:
            pass
        # #45: 清理共享文档登录会话(防进程退出残留 chromium 进程占内存)
        try:
            from app.services.shared_doc_login_service import login_manager
            login_manager.close_all()
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
    """启动时执行 alembic upgrade head（统一走迁移，不再用 create_all）

    历史: 2026-08-11 因 alembic 多 head 分支导致 upgrade 卡住而临时禁用;
          后经 merge migration(m0a1b2c3d4e5) 合并双 head, 链已恢复单 head
          (o1b2c3d4e5f6, 37 版本)。现恢复自动迁移, 解决新环境 docker compose up
          拿到空库导致 UndefinedTable/UndefinedColumn 500 的问题。
          仅由持有启动咨询锁(99887766)的 worker 执行, 避免多 worker 重复迁移。
    """
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # 单 head(已验证: o1b2c3d4e5f6); 用 "head" 而非 "heads" 避免多 head 时的歧义
    try:
        command.upgrade(cfg, "head")
        logger.info("数据库迁移已执行至 head")
    except Exception as e:
        logger.error(f"数据库迁移失败(非致命, 服务继续启动): {e}", exc_info=True)
        # 迁移失败不阻塞服务启动(由 lifespan 外层 try-except 兜底)


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
            # 2026-08-20 审计 H-7/OPS-08: 禁止把初始密码明文写日志(持久化到 ./logs/backend 与 docker json-file)
            logger.info("已创建默认管理员: %s (初始密码来自 INIT_ADMIN_PASSWORD 配置, 首次登录后请尽快修改)", settings.INIT_ADMIN_USERNAME)
    finally:
        db.close()


def _init_data_sources_and_presets() -> None:
    """启动时注册数据源元数据 + 预设分类规则 + 预设分析视图(幂等)"""
    from app.services.data_source_registry import ensure_data_sources_registered
    from app.services.doc_sync_service import ensure_doc_sync_configs
    from app.services.ingest_mode_service import ensure_ingest_modes
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
        dc_stats = ensure_doc_sync_configs(db)
        if dc_stats:
            logger.info(f"共享文档同步配置注册完成: created={dc_stats}")
        im_stats = ensure_ingest_modes(db)
        if im_stats:
            logger.info(f"采集方式开关注册完成: created={im_stats}")
    except Exception as e:
        logger.error(f"数据源/预设视图注册失败: {e}", exc_info=True)
    finally:
        db.close()


def _cleanup_zombie_running_tasks() -> None:
    """清理服务重启后残留的僵尸 running 状态同步任务

    同步任务在后台线程/协程中执行,若服务重启时任务正在运行,
    进程被终止后数据库中的 status='running' 记录将永久残留,
    导致后续定时同步误判"已有任务在运行"而跳过。

    解决:服务启动时将所有 running 状态的同步日志标记为 failed。
    """

    from sqlalchemy import text as _tx

    db = SessionLocal()
    try:
        # 清理 BMS 僵尸任务
        bms_count = db.execute(
            _tx(
                "UPDATE bms_sync_logs SET status='failed', "
                "error='服务重启清理:僵尸running任务', finished_at=NOW() "
                "WHERE status='running'"
            )
        ).rowcount
        # 清理 SCRM 僵尸任务
        scrm_count = db.execute(
            _tx(
                "UPDATE scrm_sync_logs SET status='failed', "
                "error='服务重启清理:僵尸running任务', finished_at=NOW() "
                "WHERE status='running'"
            )
        ).rowcount
        db.commit()
        if bms_count or scrm_count:
            logger.info(
                f"[启动清理] 僵尸running任务已清理: BMS={bms_count}, SCRM={scrm_count}"
            )
    except Exception as e:
        logger.warning(f"[启动清理] 清理僵尸任务出错: {e}")
        db.rollback()
    finally:
        db.close()


def _start_bms_scheduler() -> None:
    """启动 BMS 定时同步调度器(如果配置了自动启动) + 独立缓存预热 + LLM 模型预热

    缓存预热(启动预热+空闲期每分钟)与定时同步解耦: 即使 BMS_SCHEDULE_AUTOSTART=False,
    也独立启动预热循环, 避免看板缓存永不预热、过期后冷加载重查询超时。

    工单 #108 (2026-09-15): LLM 模型预热独立挂在这里(不受调度器启停影响),
    让 Talon/Ollama 模型常驻显存, 用户首次问句走热路径。
    """
    # 独立预热循环: 不依赖调度器是否启动(见 cache_warmer.start_warm_loop)
    try:
        from app.services.cache_warmer import start_warm_loop
        start_warm_loop()
    except Exception as _e:  # noqa: BLE001  预热失败不影响服务启动
        logger.warning(f"独立缓存预热启动失败(不影响服务): {_e}")
    # LLM 模型预热(启动后延迟 30s 异步触发, 失败不影响业务)
    try:
        from app.services.llm_warmup import warmup_on_startup
        warmup_on_startup()
    except Exception as _e:  # noqa: BLE001
        logger.warning(f"LLM 模型预热启动失败(不影响服务): {_e}")
    # 夜间批处理轮询(#258 B4-2): 独立于 BMS 调度器开关 —— 夜批任务(宽表重建/
    # 未来夜间预警)有自己的 enabled 开关, 采集自动启动与否不影响夜批
    try:
        from app.services.batch_runner import batch_runner
        batch_runner.start()
    except Exception as _e:  # noqa: BLE001
        logger.warning(f"夜批轮询启动失败(不影响服务): {_e}")
    if not settings.BMS_SCHEDULE_AUTOSTART:
        logger.info("BMS 定时同步未开启自动启动(BMS_SCHEDULE_AUTOSTART=False)")
        return
    from app.services.bms_scheduler import bms_scheduler
    bms_scheduler.start()
    logger.info("BMS 定时同步已自动启动")


def create_app() -> FastAPI:
    # #210: 注册表引用完整性自检（fail-fast，禁止静默失配）。
    #   纯内存校验（不依赖 DB），在 FastAPI 实例化前执行——缺映射/无登记失配
    #   直接抛 RegistryIntegrityError 拒绝启动，避免带病运行后数字悄悄错位。
    from app.services.registry_index import validate_registry_integrity

    validate_registry_integrity()

    # ★ 2026-09-22: 接线 profile 的 docs_enabled / openapi 开关。
    #   为什么必须接线：`Profile.docs_enabled` 若只是"声明了但没人读"，
    #   它就退化成**文档式的空承诺** —— 使用者以为关掉了、实际还开着。
    #   （本机制的第一原则就是"模式必须驱动行为，不能只是标签"。）
    #
    #   ⚠️ prod 保持**现状**（文档仍开放）：改造前 FastAPI 用默认值 → /docs 一直是开的，
    #      贸然关闭会改变既有可用行为（可能有对接方依赖 OpenAPI）。
    #      故：prod 的开关**不**由 profile 单独决定，而由显式环境变量控制，
    #      避免"框架升级顺带关掉了文档"这种非预期行为变更。
    #      要关闭请显式设 `OPENAPI_ENABLED=false`（独立决策，走变更流程）。
    _openapi_env = os.environ.get("OPENAPI_ENABLED", "").strip().lower()
    if _openapi_env in ("true", "false"):
        _openapi_on = _openapi_env == "true"
    else:
        # 未显式设置：dev/test 按 profile；prod 保持现状（开启）
        _openapi_on = _profile.docs_enabled or _profile.mode == "prod"
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="数据分析工具应用 - 数据分析与内部工作流辅助平台",
        lifespan=lifespan,
        # 非开放时置 None（FastAPI 语义：None = 不注册该路由）
        docs_url="/docs" if _openapi_on else None,
        redoc_url="/redoc" if _openapi_on else None,
        openapi_url="/openapi.json" if _openapi_on else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(OperationLogMiddleware)
    # #36: 动态 API 防缓存(网关 proxy_cache 会缓存 GET 导致实时数据陈旧), 见 middleware/cache_control.py
    app.add_middleware(NoCacheMiddleware)

    # P2-4: 全局 500 异常 traceback 记录器 (FastAPI 异常处理器)
    #       生产环境用户侧只能看到「服务器内部错误」，但服务端必须把完整 traceback 打到日志里，
    #       避免出现「前端 500 后端日志里啥都没有」的排查惨案。
    import traceback as _tb

    from fastapi import Request as _Request
    from fastapi.responses import JSONResponse as _JSONResponse

    @app.exception_handler(Exception)
    async def _global_unhandled_exception_handler(request: _Request, exc: Exception):
        # 拿请求摘要 (method + url + query + 客户端IP)
        try:
            method = request.method
            url = request.url.path
            qs = request.url.query
            client = request.client.host if request.client else "-"
            user_agent = request.headers.get("user-agent", "-")[:200]
            req_summary = f"{method} {url}{('?' + qs) if qs else ''} (from {client}) UA={user_agent}"
        except Exception:
            req_summary = "<unable to extract request info>"
        # 打完整 traceback 到 error 日志
        logger.error(
            f"[HTTP 500] 未处理异常: {type(exc).__name__}: {exc!r}\n"
            f"  请求: {req_summary}\n"
            f"  Traceback (most recent call last):\n{_tb.format_exc()}"
        )
        # 返回给用户侧的消息:保持跟默认 HTTPException 一致格式,不暴露内部堆栈
        return _JSONResponse(
            status_code=500,
            content={
                "code": 500,
                "message": (
                    f"服务器内部错误 ({type(exc).__name__})"
                    if settings.ENV != "prod" else
                    "服务器内部错误,请联系管理员查看日志"
                ),
            },
        )

    # 静态文件: 挂载 uploads 目录(审计贴图等图片附件)
    # ⚠️ 用 SafeStaticFiles 加固: 仅放行图片扩展名, 拒绝 dotfiles/路径穿越, 加 nosniff
    #    阻断 .browser_states 登录态、CSV/XLSX 业务文件、.html 存储型 XSS 的静态暴露
    #    (.browser_states 已移至 BROWSER_STATE_DIR, 不在 UPLOAD_DIR 下; 此处为纵深防御)
    from app.core.safe_static import SafeStaticFiles

    uploads_path = Path(settings.UPLOAD_DIR)
    uploads_path.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", SafeStaticFiles(directory=str(uploads_path)), name="uploads")

    app.include_router(api_router, prefix="/api/v1")

    @app.get("/health", tags=["系统"])
    def health():
        return {"status": "ok", "version": settings.APP_VERSION}

    return app


app = create_app()

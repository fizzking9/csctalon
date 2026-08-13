"""BMS 数据同步编排服务

职责:
  1. 计算各数据源的时间范围(基于上次同步记录)
  2. 调用 BmsFetcher 抓取数据
  3. 自动解析 + 导入(parse → upsert)
  4. 记录 BmsSyncLog

时间范围逻辑:
  - 当天无同步记录: 今日 00:00:00 ~ now
  - 当天有同步记录: 上次同步的 end_time ~ now
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, engine as _db_engine
from app.models.bms_sync_log import BmsSyncLog
from app.models.upload import UploadRecord
from app.services.bms_fetcher import BMS_DATA_SOURCES, BmsFetcher, FetchResult
from app.services.data_parser import load_dataframe, parse_file
from app.services.sync_source_config_service import get_sync_source_config
from app.services.upsert import run_upsert

logger = logging.getLogger(__name__)

# 全部可同步的数据类型(状态展示/UI 复选框遍历用)
SYNC_DATA_TYPES = list(BMS_DATA_SOURCES.keys())  # refund/ticket/wallet/device_combo

# 默认同步的数据类型(default_sync=True):定时同步 + 手动同步默认勾选用
DEFAULT_SYNC_TYPES = [dt for dt, cfg in BMS_DATA_SOURCES.items() if cfg.get("default_sync", True)]


def _get_last_sync_time(db: Session, data_type: str) -> datetime | None:
    """查询某数据源最近一次成功同步的 end_time

    按 created_at 倒序取最近一次成功执行的记录,而不是 end_time 最大的。
    否则更早的任务(抓跨日区间、end_time 偏大)会覆盖更晚执行、真实数据更近的记录。
    """
    log = db.scalar(
        select(BmsSyncLog)
        .where(BmsSyncLog.data_type == data_type, BmsSyncLog.status == "success")
        .order_by(desc(BmsSyncLog.created_at))
        .limit(1)
    )
    return log.end_time if log else None


def calculate_time_range(db: Session, data_type: str) -> tuple[datetime, datetime]:
    """计算同步时间范围(始终限定为当天数据,防止同步过多历史数据)

    逻辑:
      - 如果当天没有成功同步记录,从今日 00:00 开始
      - 如果当天有同步记录,从上次同步的 end_time 开始(增量)
      - 兜底: 任何情况下 start 都不会早于今天 00:00

    注意: 定时同步(bms_scheduler)已显式传入「今天 00:00 ~ now」,
          本函数仅作为未指定 time_ranges 时的兜底(理论上当前无调用方)。
    """
    now = datetime.now(timezone.utc) + timedelta(hours=8)  # 转东八区
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    last_sync = _get_last_sync_time(db, data_type)
    if last_sync:
        # 转东八区比较
        last_sync_local = last_sync
        if last_sync_local.tzinfo:
            last_sync_local = last_sync_local.astimezone(timezone(timedelta(hours=8)))
            last_sync_local = last_sync_local.replace(tzinfo=None)

        if last_sync_local.date() == now.date():
            # 当天有同步,从上次同步时间开始
            start = last_sync_local
        else:
            # 不是当天,从今天0点开始
            start = today_start
    else:
        # 从未同步,从今天0点开始
        start = today_start

    # 兜底保护: start 绝不能早于今天 0 点(防止同步过多历史数据)
    if start.date() < now.date():
        start = today_start

    return start, now


def _auto_import(db: Session, data_type: str, file_path: str, filename: str, triggered_by: str) -> dict:
    """自动解析并导入文件(使用建议的列映射)

    Returns:
        dict with total_rows, inserted_rows, updated_rows, failed_rows
    """
    # 2026-08-11 双维度同步: ticket_finish 是 ticket 的按完成时间补充抓取,按 ticket 导入
    if data_type == "ticket_finish":
        logger.info(f"[ticket_finish] 映射为 ticket 进行导入(按完成时间补充抓取)")
        data_type = "ticket"
    base = Path(settings.UPLOAD_DIR)
    full_path = base / file_path

    if not full_path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    # 1. 解析文件获取建议映射
    preview = parse_file(full_path, data_type)
    mapping = preview.get("suggested_mapping", {})
    encoding = preview.get("detected_encoding")
    total_rows = preview.get("total_rows", 0)

    logger.info(f"[{data_type}] 解析成功: {total_rows} 行, 映射: {len(mapping)} 字段")

    # 2. 创建上传记录
    record = UploadRecord(
        data_type=data_type,
        filename=filename,
        stored_path=file_path,
        file_size=full_path.stat().st_size,
        status="parsing",
        uploaded_by=triggered_by,
        source="bms",
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    try:
        # 3. 加载 DataFrame 并执行 upsert
        df = load_dataframe(full_path, encoding=encoding)
        result = run_upsert(df, data_type, mapping, db)

        record.total_rows = result.total
        record.inserted_rows = result.inserted
        record.updated_rows = result.updated
        record.failed_rows = result.failed
        record.status = "imported"
        record.finished_at = datetime.now(timezone.utc)
        if result.errors:
            record.error_detail = {"errors": result.errors[:50]}
        db.commit()

        logger.info(f"[{data_type}] 导入完成: 新增 {result.inserted} / 更新 {result.updated} / 失败 {result.failed}")

        return {
            "total_rows": result.total,
            "inserted_rows": result.inserted,
            "updated_rows": result.updated,
            "failed_rows": result.failed,
        }
    except Exception as e:
        db.rollback()
        record = db.get(UploadRecord, record.id)
        if record:
            record.status = "failed"
            record.error_detail = {"error": str(e)[:2000]}
            record.finished_at = datetime.now(timezone.utc)
            db.commit()
        raise


def _create_sync_log(
    db: Session,
    data_type: str,
    start_time: datetime,
    end_time: datetime,
    trigger_type: str,
    triggered_by: str,
) -> BmsSyncLog:
    """创建同步日志记录(初始状态 running)"""
    log = BmsSyncLog(
        data_type=data_type,
        start_time=start_time,
        end_time=end_time,
        status="running",
        trigger_type=trigger_type,
        triggered_by=triggered_by,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def _update_sync_log(
    db: Session,
    log_id: int,
    status: str,
    file_path: str | None = None,
    filename: str | None = None,
    import_stats: dict | None = None,
    error: str | None = None,
):
    """更新同步日志记录(带事务回滚兜底,避免前序失败导致 session 处于 aborted 状态而无法 commit)"""
    try:
        log = db.get(BmsSyncLog, log_id)
    except Exception:  # noqa: BLE001
        db.rollback()
        log = db.get(BmsSyncLog, log_id)
    if not log:
        return
    log.status = status
    log.file_path = file_path
    log.filename = filename
    log.finished_at = datetime.now(timezone.utc)
    if import_stats:
        log.total_rows = import_stats.get("total_rows")
        log.inserted_rows = import_stats.get("inserted_rows")
        log.updated_rows = import_stats.get("updated_rows")
        log.failed_rows = import_stats.get("failed_rows")
    if error:
        log.error = error[:2000]
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        db.commit()


async def sync_all(
    trigger_type: str = "manual",
    triggered_by: str = "admin",
    on_progress: callable = None,
    data_types: list[str] | None = None,
    time_ranges: dict[str, tuple[datetime, datetime]] | None = None,
) -> list[dict]:
    """同步 BMS 数据源

    Args:
        trigger_type: manual/scheduled
        triggered_by: 触发者用户名
        on_progress: 回调 (data_type, phase, data) -> None
        data_types: 指定要同步的数据源,默认 DEFAULT_SYNC_TYPES(default_sync=True 的源)
        time_ranges: 指定各数据源的时间范围 {data_type: (start, end)},默认自动计算

    Returns:
        list[dict] 每个数据源的同步结果

    凭证从 sync_source_configs 表读取(按源独立),无需调用方传入。
    """
    results = []

    # 确定要同步的数据源(默认仅 default_sync=True 的源)
    sync_types = data_types if data_types else list(DEFAULT_SYNC_TYPES)

    # 1. 预计算时间范围(如果未传入则自动计算)
    if not time_ranges:
        db = SessionLocal()
        time_ranges = {}
        try:
            for data_type in sync_types:
                start, end = calculate_time_range(db, data_type)
                time_ranges[data_type] = (start, end)
                logger.info(f"[{data_type}] 时间范围: {start} ~ {end}")
        finally:
            db.close()
    else:
        # 只同步传入的时间范围中包含的数据源
        sync_types = [dt for dt in sync_types if dt in time_ranges]

    # 2. 加载各源运行配置(含解密凭证)+ 组装抓取任务
    db = SessionLocal()
    tasks: list[dict] = []
    skipped: list[dict] = []  # 未启用/未配置的源,直接生成结果不抓取
    try:
        for data_type in sync_types:
            config = get_sync_source_config(db, data_type)
            start, end = time_ranges[data_type]
            if not config:
                skipped.append({
                    "data_type": data_type, "name": data_type, "success": False,
                    "error": f"数据源未注册: {data_type}",
                    "start_time": start.isoformat(), "end_time": end.isoformat(),
                })
                continue
            if not config.get("enabled", True):
                skipped.append({
                    "data_type": data_type, "name": config.get("name", data_type), "success": False,
                    "error": "数据源已停用(在同步设置中关闭)",
                    "start_time": start.isoformat(), "end_time": end.isoformat(),
                })
                continue
            tasks.append({"data_type": data_type, "start": start, "end": end, "config": config})

            # 2026-08-11 双维度同步: ticket 附加按"完成时间"抓取一次
            # 原因: 工单登记时handler为空,完工时才回填handler。
            #       只按登记时间同步会遗漏老工单完工后的handler更新(34.8%的工单非当天完工)。
            # 方案: 额外按"完成时间"抓取同时间范围的工单, upsert 按 ticket_no 自动去重合并。
            if data_type == "ticket":
                finish_config = dict(config)  # 浅拷贝,避免污染原始config
                finish_config["time_labels"] = ["起始完成时间", "结束完成时间"]
                finish_config["name"] = config.get("name", "服务请求管理") + "(按完成时间)"
                tasks.append({
                    "data_type": "ticket_finish",
                    "start": start,
                    "end": end,
                    "config": finish_config,
                })
                logger.info(f"[ticket] 双维度同步: 已添加按完成时间抓取任务(覆盖老工单完工handler回填)")
    finally:
        db.close()

    # 3. 抓取 + 导入并发:
    #    旧做法: 所有源全部抓取完成后再串行导入 -> 抓B的等待完全没被利用
    #    新做法: 每个源抓完就立刻启动后台导入任务,与「下一个抓取+等待」并发执行
    #    BMS 1 分钟限制仅针对同一个 Web 系统的访问/查询/下载,与本地导入无关
    # 分流:fetch_mode="http" 的源走 ai_acc_fetcher(HTTP API),其余走 BmsFetcher(Playwright)
    http_tasks = [t for t in tasks if t["config"].get("fetch_mode") == "http"]
    pw_tasks = [t for t in tasks if t["config"].get("fetch_mode") != "http"]

    # 每个源完成抓取后,立刻在此处挂一个 asyncio.Task 做后台导入(用 to_thread 避免阻塞 event_loop)
    pending_imports: dict[str, asyncio.Task] = {}
    # 每个源最新的 FetchResult,供回调启动导入时使用
    last_fr_by_type: dict[str, FetchResult] = {}

    # 用闭包把参数带到 _import_one_in_background 里
    def _build_task_config(data_type: str):
        for t in tasks:
            if t["data_type"] == data_type:
                return t
        return None

    async def _import_one_in_background(fr: FetchResult, start: datetime, end: datetime, name: str) -> dict:
        """在后台线程执行导入(因为 pandas/SQLAlchemy 同步 I/O 会阻塞 event_loop),返回单源结果 dict"""
        try:
            def _sync_import_work() -> dict:
                inner_db = SessionLocal()
                try:
                    if fr.success:
                        log = _create_sync_log(inner_db, fr.data_type, start, end, trigger_type, triggered_by)
                        try:
                            import_stats = _auto_import(inner_db, fr.data_type, fr.file_path, fr.filename, triggered_by)
                            _update_sync_log(inner_db, log.id, "success", fr.file_path, fr.filename, import_stats)
                            return {
                                "data_type": fr.data_type,
                                "name": name,
                                "success": True,
                                "file_path": fr.file_path,
                                "filename": fr.filename,
                                **import_stats,
                                "start_time": start.isoformat(),
                                "end_time": end.isoformat(),
                            }
                        except Exception as e:
                            _update_sync_log(inner_db, log.id, "failed", fr.file_path, fr.filename, error=str(e))
                            logger.error(f"[{fr.data_type}] 导入失败: {e}", exc_info=True)
                            return {
                                "data_type": fr.data_type,
                                "name": name,
                                "success": False,
                                "error": f"导入失败: {e}",
                                "file_path": fr.file_path,
                                "start_time": start.isoformat(),
                                "end_time": end.isoformat(),
                            }
                    else:
                        log = _create_sync_log(inner_db, fr.data_type, start, end, trigger_type, triggered_by)
                        _update_sync_log(inner_db, log.id, "failed", error=fr.error)
                        return {
                            "data_type": fr.data_type,
                            "name": name,
                            "success": False,
                            "error": fr.error,
                            "start_time": start.isoformat(),
                            "end_time": end.isoformat(),
                        }
                finally:
                    inner_db.close()

            # 先把 phase 切到 importing(在此处做因为要走 async on_progress 回调)
            if on_progress:
                await on_progress(fr.data_type, "importing", None)
            result_dict = await asyncio.to_thread(_sync_import_work)

            if on_progress:
                await on_progress(fr.data_type, "imported" if result_dict["success"] else "failed", result_dict)
            return result_dict

        except Exception as e:
            # 兜底: 任何导入后台任务本身的异常,都变成一个失败结果,不让 gather 抛异常
            logger.exception(f"[{fr.data_type}] 导入后台任务崩溃: {e}")
            tcfg = _build_task_config(fr.data_type)
            nm = tcfg["config"].get("name", fr.data_type) if tcfg else fr.data_type
            fallback = {
                "data_type": fr.data_type,
                "name": nm,
                "success": False,
                "error": f"导入崩溃: {e}",
                "start_time": time_ranges.get(fr.data_type, (datetime.now(), datetime.now()))[0].isoformat(),
                "end_time": time_ranges.get(fr.data_type, (datetime.now(), datetime.now()))[1].isoformat(),
            }
            if on_progress:
                await on_progress(fr.data_type, "failed", fallback)
            return fallback

    def _launch_import_if_needed(fr: FetchResult):
        """某源抓取完成:立刻起后台导入任务(不阻塞下一次抓取)"""
        if fr.data_type in pending_imports:
            return
        tcfg = _build_task_config(fr.data_type)
        if not tcfg:
            return
        start, end = tcfg["start"], tcfg["end"]
        name = tcfg["config"].get("name", fr.data_type)
        last_fr_by_type[fr.data_type] = fr
        pending_imports[fr.data_type] = asyncio.create_task(
            _import_one_in_background(fr, start, end, name)
        )

    # 包装原始 on_progress: 当 phase=fetched / done (抓取完成) 时,立刻启动导入任务
    original_on_progress = on_progress

    async def _wrapped_on_progress(data_type: str, phase: str, payload):
        # 先写进度(让前端立刻看到)
        if original_on_progress:
            await original_on_progress(data_type, phase, payload)
        # 抓取阶段结束 -> 立刻起导入
        if phase in ("fetched", "done") and isinstance(payload, FetchResult):
            _launch_import_if_needed(payload)
        elif phase == "fetched" and isinstance(payload, dict):
            # HTTP 抓取的 payload 是 dict,从 last_fr_by_type 找回 FetchResult
            fr = last_fr_by_type.get(data_type)
            if fr:
                _launch_import_if_needed(fr)

    # 读写并发优化(抓取→导入→匹配→宽表):标记「写入高峰期」
    # - Redis 缓存 TTL 自动延长(60s → 300s),避免写入高峰时缓存雪崩击穿 DB
    # - 30 分钟兜底 TTL, 即使进程崩了未清除标记, Redis 也会自动解除
    # - 任何异常都会 finally 清除标记
    _writing_marked = False
    try:
        try:
            from app.core.redis_client import mark_sync_writing
            mark_sync_writing(True, ttl=1800)
            _writing_marked = True
        except Exception:
            pass

        # HTTP API 抓取(ai-acc 等无需浏览器的源)
        if http_tasks:
            from app.services.ai_acc_fetcher import fetch_one as http_fetch_one

            for task in http_tasks:
                logger.info(f"[{task['data_type']}] HTTP 抓取(非 Playwright)")
                fr = await http_fetch_one(task)
                last_fr_by_type[fr.data_type] = fr
                if original_on_progress:
                    await original_on_progress(
                        fr.data_type, "fetched", {"success": fr.success, "file": fr.filename}
                    )
                # HTTP 抓取完成: 立刻启动导入(不等待下一个)
                _launch_import_if_needed(fr)

        # Playwright 抓取(BMS 等需浏览器自动化的源)
        # fetcher.fetch_all 内部按域名分组: 组内等待 65 秒(同一 Web 限制),跨组直接跳过
        # 同时每个单源抓取完成时通过 _wrapped_on_progress 立刻启动导入,与后续等待/抓取下一个并发
        if pw_tasks:
            async with BmsFetcher() as fetcher:
                # 即使外部未传 on_progress,也要包一层以便启动导入并发
                pw_progress_cb = _wrapped_on_progress
                # fetch_all 返回 FetchResult 列表(可能有重复 or 全部),但导入任务其实已经在回调里都起了
                await fetcher.fetch_all(tasks=pw_tasks, on_progress=pw_progress_cb)

        # 4. 等所有后台导入任务全部完成 -> 聚合成最终结果
        #    这里不再串行导入了: 大部分导入其实早就在「等待下一次抓取」的间隙里跑完了
        import_results: list[dict] = []
        if pending_imports:
            logger.info(f"已完成抓取,等待 {len(pending_imports)} 个导入后台任务收尾...")
            done_tasks = await asyncio.gather(*pending_imports.values(), return_exceptions=True)
            for result_or_exc in done_tasks:
                if isinstance(result_or_exc, Exception):
                    logger.error(f"导入任务异常(已兜底转失败): {result_or_exc}")
                    continue
                import_results.append(result_or_exc)
        results.extend(import_results)

        # 跳过的源(停用/未注册)也计入结果
        results.extend(skipped)

        # 5. post-sync 钩子:关联匹配 + 退费宽表物化 + 统计刷新 + 缓存失效(失败不阻塞同步主流程)
        #    只在有成功导入时触发,用独立 Session 避免污染主事务
        any_success = any(r.get("success") for r in results)
        if any_success:
            try:
                from app.services.matcher import match_all
                from app.services.wide_table_builder import build_refund_wide

                # ⚠️ 2026-08-12 关键修复:
                # match_all / build_refund_wide / _analyze_key_tables 都是同步阻塞函数,
                # 直接在 async 函数里调用会阻塞 event loop 数分钟(15万+行宽表构建 ~8分钟),
                # 期间 FastAPI 无法响应 /task/status 轮询 → 前端连续超时后停止轮询,
                # 表现为"界面卡死 也不恢复"。且原 asyncio.ensure_future 回调因 event loop
                # 被阻塞也无法执行,进度日志无法到达前端。
                # 修复: 整个 post-sync 钩子放到 worker 线程(asyncio.to_thread),
                # 进度回调用 run_coroutine_threadsafe 跨线程调度到主 event loop。
                #
                # ⚠️ 2026-08-13 宽表增量优化:
                #   此前每次同步都全量 rebuild 15 万+行宽表(~8 分钟),绝大多数行其实
                #   没有任何数据变化。改为增量:从「本次同步导入成功 + 本次新增匹配关系」
                #   汇总 affected_refund_nos,仅 rebuild 受影响的行。当影响面超过 25%
                #   或总量较小(<2000行)时自动降级全量,兼顾正确性和效率。
                loop = asyncio.get_running_loop()

                def _sync_post_sync_hook() -> tuple[dict, dict]:
                    from sqlalchemy.exc import OperationalError as _OpErr
                    import time as _time

                    def _run_hook_once(first_try: bool) -> tuple[dict, dict]:
                        hook_db = SessionLocal()
                        try:
                            from sqlalchemy import func as _func

                            def _report(name: str, phase: str = "importing"):
                                """跨线程向主 event loop 投递进度事件(fire-and-forget)"""
                                if on_progress:
                                    try:
                                        asyncio.run_coroutine_threadsafe(
                                            on_progress("__post_sync__", phase, {"name": name}),
                                            loop,
                                        )
                                    except Exception:
                                        pass

                            _report("关联匹配中..." if first_try else "关联匹配中(重试)...")
                            def _match_progress_cb(step_name: str, current: int, total: int):
                                _report(f"关联匹配 {current+1}/{total}: {step_name}")

                            match_stats = match_all(hook_db, only_unmatched=True, progress_callback=_match_progress_cb)
                            match_affected: set[str] = match_stats.pop("_affected_refund_nos", set()) or set()

                            # ---------- 增量收集 affected_refund_nos ----------
                            def _collect_affected_from_imports(imp_results: list[dict], db: Session) -> set[str]:
                                """从成功导入记录中,收集需要刷新宽表的 refund_no 集合"""
                                from datetime import datetime as _dt
                                from app.models.refund import Refund
                                from app.models.ticket import ServiceTicket
                                from app.models.wallet import WalletWithdrawal
                                from app.models.merchant_refund import MerchantRefund
                                from app.models.external_complaint import ExternalComplaint
                                from app.models.upload import DataMatchRelation

                                affected: set[str] = set()
                                for r in imp_results:
                                    if not r.get("success"):
                                        continue
                                    if (r.get("inserted_rows", 0) or 0) + (r.get("updated_rows", 0) or 0) == 0:
                                        continue
                                    dt = r.get("data_type")
                                    start_s = r.get("start_time")
                                    start_dt = None
                                    if start_s:
                                        try:
                                            start_dt = _dt.fromisoformat(start_s) if isinstance(start_s, str) else start_s
                                        except Exception:
                                            start_dt = None

                                    if dt == "refund":
                                        q = select(Refund.refund_no)
                                        if start_dt:
                                            q = q.where(
                                                (Refund.created_at_raw >= start_dt) | (Refund.created_at >= start_dt)
                                            )
                                        else:
                                            from datetime import timedelta as _td
                                            recent = _dt.now() - _td(hours=2)
                                            q = q.where(Refund.created_at >= recent)
                                        affected.update(db.scalars(q).all())

                                    elif dt == "ticket":
                                        tq = select(ServiceTicket.ticket_no)
                                        if start_dt:
                                            tq = tq.where(
                                                (ServiceTicket.register_time >= start_dt)
                                                | (ServiceTicket.created_at >= start_dt)
                                            )
                                        else:
                                            from datetime import timedelta as _td
                                            recent = _dt.now() - _td(hours=2)
                                            tq = tq.where(ServiceTicket.created_at >= recent)
                                        ticket_nos = set(db.scalars(tq).all())
                                        if ticket_nos:
                                            rq = (
                                                select(DataMatchRelation.src_id)
                                                .where(
                                                    DataMatchRelation.src_table == "refund",
                                                    DataMatchRelation.dst_table == "ticket",
                                                    DataMatchRelation.status == "matched",
                                                    DataMatchRelation.dst_id.in_(list(ticket_nos)),
                                                )
                                            )
                                            affected.update(db.scalars(rq).all())

                                    elif dt == "wallet":
                                        wq = select(WalletWithdrawal.id)
                                        if start_dt:
                                            wq = wq.where(WalletWithdrawal.created_at >= start_dt)
                                        else:
                                            from datetime import timedelta as _td
                                            recent = _dt.now() - _td(hours=2)
                                            wq = wq.where(WalletWithdrawal.created_at >= recent)
                                        wids = [str(i) for i in db.scalars(wq).all()]
                                        if wids:
                                            rq = (
                                                select(DataMatchRelation.src_id)
                                                .where(
                                                    DataMatchRelation.src_table == "refund",
                                                    DataMatchRelation.dst_table == "wallet",
                                                    DataMatchRelation.status == "matched",
                                                    DataMatchRelation.dst_id.in_(wids),
                                                )
                                            )
                                            affected.update(db.scalars(rq).all())

                                    elif dt == "merchant_refund":
                                        mq = select(MerchantRefund.id)
                                        if start_dt:
                                            mq = mq.where(MerchantRefund.created_at >= start_dt)
                                        else:
                                            from datetime import timedelta as _td
                                            recent = _dt.now() - _td(hours=2)
                                            mq = mq.where(MerchantRefund.created_at >= recent)
                                        mids = [str(i) for i in db.scalars(mq).all()]
                                        if mids:
                                            rq = (
                                                select(DataMatchRelation.src_id)
                                                .where(
                                                    DataMatchRelation.src_table == "refund",
                                                    DataMatchRelation.dst_table == "merchant_refund",
                                                    DataMatchRelation.status == "matched",
                                                    DataMatchRelation.dst_id.in_(mids),
                                                )
                                            )
                                            affected.update(db.scalars(rq).all())

                                    elif dt == "external_complaint":
                                        eq = select(ExternalComplaint.device_sn).where(
                                            ExternalComplaint.device_sn.isnot(None)
                                        )
                                        if start_dt:
                                            eq = eq.where(ExternalComplaint.created_at >= start_dt)
                                        else:
                                            from datetime import timedelta as _td
                                            recent = _dt.now() - _td(hours=2)
                                            eq = eq.where(ExternalComplaint.created_at >= recent)
                                        sns = set(s for s in db.scalars(eq).all() if s)
                                        if sns:
                                            rq = select(Refund.refund_no).where(Refund.device_sn.in_(list(sns)))
                                            affected.update(db.scalars(rq).all())

                                return affected

                            imported_affected = _collect_affected_from_imports(results, hook_db)
                            all_affected = imported_affected | match_affected

                            total_refunds = hook_db.scalar(select(_func.count()).select_from(Refund)) or 0
                            # 阈值策略:总量小(<2000)或影响面大(>25%)→全量更高效;否则增量
                            INCREMENTAL_RATIO_THRESHOLD = 0.25
                            INCREMENTAL_MIN_TOTAL = 2000
                            use_incremental = (
                                len(all_affected) > 0
                                and total_refunds >= INCREMENTAL_MIN_TOTAL
                                and len(all_affected) / total_refunds <= INCREMENTAL_RATIO_THRESHOLD
                            )

                            def _wide_progress_cb(built: int, total: int):
                                _report(f"构建退费宽表 {built}/{total}")

                            _report(
                                ("构建退费宽表" if first_try else "构建退费宽表(重试)")
                                + ("(增量)" if use_incremental else "(全量)")
                                + f" affected={len(all_affected)} total={total_refunds}"
                            )

                            if use_incremental:
                                wide_stats = build_refund_wide(
                                    hook_db,
                                    only_refund_nos=list(all_affected),
                                    progress_callback=_wide_progress_cb,
                                )
                                wide_stats["mode"] = "incremental"
                                wide_stats["affected_count"] = len(all_affected)
                                wide_stats["total_refunds"] = total_refunds
                            else:
                                wide_stats = build_refund_wide(hook_db, progress_callback=_wide_progress_cb)
                                wide_stats["mode"] = "full"
                                wide_stats["total_refunds"] = total_refunds

                            _report("刷新数据库统计...")
                            _analyze_key_tables(hook_db)
                            logger.info(
                                f"post-sync 钩子完成: match={match_stats}, wide={wide_stats}"
                            )
                            _report("post-sync 完成", phase="imported")
                            return match_stats, wide_stats
                        finally:
                            try:
                                hook_db.close()
                            except Exception:
                                pass

                    # 首次执行
                    try:
                        return _run_hook_once(first_try=True)
                    except _OpErr as e:
                        # ⚠️ 数据库连接在长耗时期间被静默断开(OperationalError: server closed
                        # the connection unexpectedly)。此时连接池中的连接可能都已坏掉,
                        # dispose 清空池+等待 2s 再用新 Session 重试一次(最多一次)。
                        logger.warning(
                            "post-sync 钩子遇到 DB 连接断开:%s。清空连接池后重试一次...", e
                        )
                        try:
                            _db_engine.dispose()
                        except Exception:
                            pass
                        _time.sleep(2)
                        try:
                            return _run_hook_once(first_try=False)
                        except _OpErr as e2:
                            logger.warning("post-sync 重试仍然 DB 连接断开:%s,降级跳过", e2)
                            raise

                await asyncio.to_thread(_sync_post_sync_hook)

                # 失效 Redis 缓存 (today:* 等), 避免同步后仍读到旧聚合结果
                try:
                    from app.core.redis_client import cache_invalidate_pattern
                    n = cache_invalidate_pattern("today:*")
                    if n:
                        logger.info(f"已失效 {n} 条 today:* 缓存")
                except Exception as ce:
                    logger.debug(f"缓存失效失败(不影响同步): {ce}")
            except Exception as e:
                logger.warning(f"post-sync 钩子(关联匹配/宽表物化)失败,不影响同步结果: {e}")

    finally:
        # 写入高峰期结束:清除 Redis 标记(即使前面抛异常也保证清掉)
        if _writing_marked:
            try:
                from app.core.redis_client import mark_sync_writing
                mark_sync_writing(False)
            except Exception:
                pass

    return results


def _analyze_key_tables(db) -> None:
    """对同步频繁的表执行 ANALYZE, 刷新 PostgreSQL 统计信息

    BMS 同步用 ON CONFLICT DO NOTHING 全量覆盖, 产生大量 dead tuples + 统计滞后,
    导致查询计划器基于过时的 n_live_tup 选择低效计划(如把 144660 行的表当成 646 行).
    autovacuum 默认阈值 20% 才触发, 对日增量小的表可能几天才分析一次, 期间性能持续恶化.
    """
    # ANALYZE 不能在事务里执行, 用 raw connection autocommit
    from sqlalchemy import text as _sa_text
    conn = db.connection()
    try:
        # 退出当前事务上下文(若有), 切到 autocommit
        conn.execute(_sa_text("COMMIT"))
    except Exception:
        pass
    old_autocommit = getattr(conn, "autocommit", False)
    try:
        conn.autocommit = True
        for tbl in ("cs_records", "service_tickets", "refunds",
                    "wallet_withdrawals", "merchant_refunds", "refund_wide"):
            try:
                conn.execute(_sa_text(f"ANALYZE {tbl}"))
            except Exception:
                pass  # 表不存在(如 refund_wide 未建)静默跳过
    finally:
        conn.autocommit = old_autocommit


def get_sync_status(db: Session) -> list[dict]:
    """获取各数据源的同步状态(遍历全部 SYNC_DATA_TYPES)"""
    statuses = []
    for data_type in SYNC_DATA_TYPES:
        config = get_sync_source_config(db, data_type) or {}
        last_log = db.scalar(
            select(BmsSyncLog)
            .where(BmsSyncLog.data_type == data_type, BmsSyncLog.status == "success")
            .order_by(desc(BmsSyncLog.created_at))
            .limit(1)
        )
        statuses.append({
            "data_type": data_type,
            "name": config.get("name", data_type),
            "url": (config.get("base_url", "") + config.get("page_path", "")) or None,
            "has_credentials": bool(config.get("username") and config.get("password")),
            "default_sync": config.get("default_sync", True),
            "enabled": config.get("enabled", True),
            "requires_captcha": config.get("requires_captcha", False),
            "last_sync": last_log.created_at.isoformat() if last_log else None,
            "last_status": last_log.status if last_log else None,
            "last_end_time": last_log.end_time.isoformat() if last_log else None,
            "last_rows": last_log.total_rows if last_log else None,
        })
    return statuses

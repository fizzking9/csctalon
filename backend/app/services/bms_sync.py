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
from app.core.database import SessionLocal
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

    # 5. post-sync 钩子:关联匹配 + 退费宽表物化(失败不阻塞同步主流程)
    #    只在有成功导入时触发,用独立 Session 避免污染主事务
    any_success = any(r.get("success") for r in results)
    if any_success:
        try:
            from app.services.matcher import match_all
            from app.services.wide_table_builder import build_refund_wide

            hook_db = SessionLocal()
            try:
                match_stats = match_all(hook_db, only_unmatched=True)
                wide_stats = build_refund_wide(hook_db)
                logger.info(
                    f"post-sync 钩子完成: match={match_stats}, wide={wide_stats}"
                )
            finally:
                hook_db.close()
        except Exception as e:
            logger.warning(f"post-sync 钩子(关联匹配/宽表物化)失败,不影响同步结果: {e}")

    return results


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

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
from app.services.upsert import run_upsert

logger = logging.getLogger(__name__)

# 支持同步的数据类型
SYNC_DATA_TYPES = ["refund", "ticket", "wallet"]


def _get_last_sync_time(db: Session, data_type: str) -> datetime | None:
    """查询某数据源最近一次成功同步的 end_time"""
    log = db.scalar(
        select(BmsSyncLog)
        .where(BmsSyncLog.data_type == data_type, BmsSyncLog.status == "success")
        .order_by(desc(BmsSyncLog.end_time))
        .limit(1)
    )
    return log.end_time if log else None


def calculate_time_range(db: Session, data_type: str) -> tuple[datetime, datetime]:
    """计算同步时间范围

    逻辑:
      - 如果当天没有成功同步记录,从今日 00:00 开始
      - 如果当天有同步记录,从上次同步的 end_time 开始
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
    """更新同步日志记录"""
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
    db.commit()


async def sync_all(
    username: str,
    password: str,
    trigger_type: str = "manual",
    triggered_by: str = "admin",
    on_progress: callable = None,
    data_types: list[str] | None = None,
    time_ranges: dict[str, tuple[datetime, datetime]] | None = None,
) -> list[dict]:
    """同步 BMS 数据源

    Args:
        username: BMS 账号
        password: BMS 密码
        trigger_type: manual/scheduled
        triggered_by: 触发者用户名
        on_progress: 回调 (data_type, phase, data) -> None
        data_types: 指定要同步的数据源,默认全部 ["refund","ticket","wallet"]
        time_ranges: 指定各数据源的时间范围 {data_type: (start, end)},默认自动计算

    Returns:
        list[dict] 每个数据源的同步结果
    """
    results = []

    # 确定要同步的数据源
    sync_types = data_types if data_types else list(SYNC_DATA_TYPES)

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

    # 2. 抓取所有数据源
    async with BmsFetcher() as fetcher:
        fetch_results: list[FetchResult] = await fetcher.fetch_all(
            username=username,
            password=password,
            time_ranges=time_ranges,
            on_progress=on_progress,
        )

    # 3. 逐个导入
    for fr in fetch_results:
        db = SessionLocal()
        try:
            start, end = time_ranges[fr.data_type]

            if fr.success:
                # 创建同步日志
                log = _create_sync_log(db, fr.data_type, start, end, trigger_type, triggered_by)

                try:
                    # 自动导入
                    import_stats = _auto_import(db, fr.data_type, fr.file_path, fr.filename, triggered_by)
                    _update_sync_log(db, log.id, "success", fr.file_path, fr.filename, import_stats)

                    results.append({
                        "data_type": fr.data_type,
                        "name": BMS_DATA_SOURCES[fr.data_type]["name"],
                        "success": True,
                        "file_path": fr.file_path,
                        "filename": fr.filename,
                        **import_stats,
                        "start_time": start.isoformat(),
                        "end_time": end.isoformat(),
                    })

                    if on_progress:
                        await on_progress(fr.data_type, "imported", results[-1])

                except Exception as e:
                    _update_sync_log(db, log.id, "failed", fr.file_path, fr.filename, error=str(e))
                    results.append({
                        "data_type": fr.data_type,
                        "name": BMS_DATA_SOURCES[fr.data_type]["name"],
                        "success": False,
                        "error": f"导入失败: {e}",
                        "file_path": fr.file_path,
                        "start_time": start.isoformat(),
                        "end_time": end.isoformat(),
                    })
                    logger.error(f"[{fr.data_type}] 导入失败: {e}", exc_info=True)
            else:
                # 抓取失败,记录日志
                log = _create_sync_log(db, fr.data_type, start, end, trigger_type, triggered_by)
                _update_sync_log(db, log.id, "failed", error=fr.error)

                results.append({
                    "data_type": fr.data_type,
                    "name": BMS_DATA_SOURCES[fr.data_type]["name"],
                    "success": False,
                    "error": fr.error,
                    "start_time": start.isoformat(),
                    "end_time": end.isoformat(),
                })
        finally:
            db.close()

    return results


def get_sync_status(db: Session) -> list[dict]:
    """获取各数据源的同步状态"""
    statuses = []
    for data_type in SYNC_DATA_TYPES:
        config = BMS_DATA_SOURCES[data_type]
        last_log = db.scalar(
            select(BmsSyncLog)
            .where(BmsSyncLog.data_type == data_type)
            .order_by(desc(BmsSyncLog.created_at))
            .limit(1)
        )
        statuses.append({
            "data_type": data_type,
            "name": config["name"],
            "last_sync": last_log.created_at.isoformat() if last_log else None,
            "last_status": last_log.status if last_log else None,
            "last_end_time": last_log.end_time.isoformat() if last_log else None,
            "last_rows": last_log.total_rows if last_log else None,
        })
    return statuses

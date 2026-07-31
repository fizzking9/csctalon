"""BMS 数据同步 API"""

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.api.deps import CurrentUser, DBSession
from app.models.bms_sync_log import BmsSyncLog
from app.schemas.common import R
from app.services.bms_fetcher import BmsFetcher
from app.services.bms_scheduler import bms_scheduler
from app.services.bms_sync import get_sync_status, sync_all

router = APIRouter(prefix="/fetch", tags=["数据中心-BMS同步"])

# 东八区
TZ_CN = timezone(timedelta(hours=8))


def _compute_time_range(
    range_type: str,
    custom_start: datetime | None = None,
    custom_end: datetime | None = None,
) -> tuple[datetime, datetime]:
    """根据预设类型计算时间范围(东八区)"""
    now = datetime.now(TZ_CN)
    if range_type == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now
    elif range_type == "yesterday":
        y = now - timedelta(days=1)
        start = y.replace(hour=0, minute=0, second=0, microsecond=0)
        end = y.replace(hour=23, minute=59, second=59, microsecond=0)
        return start, end
    elif range_type == "week":
        start = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now
    elif range_type == "custom":
        if not custom_start or not custom_end:
            raise HTTPException(status_code=400, detail="自定义时间范围需提供 custom_start 和 custom_end")
        return custom_start, custom_end
    else:
        raise HTTPException(status_code=400, detail=f"不支持的时间范围类型: {range_type}")


# ---------- 请求/响应模型 ----------


class SyncAllRequest(BaseModel):
    """一键同步请求"""

    data_types: list[str] | None = Field(None, description="要同步的数据源,默认勾选的源。可选: refund/ticket/wallet/device_combo/cs_record")
    time_range: str = Field("today", description="时间范围预设: today/yesterday/week/custom")
    custom_start: datetime | None = Field(None, description="自定义开始时间(time_range=custom 时生效)")
    custom_end: datetime | None = Field(None, description="自定义结束时间(time_range=custom 时生效)")


class SyncResultItem(BaseModel):
    data_type: str
    name: str
    success: bool
    file_path: str | None = None
    filename: str | None = None
    total_rows: int | None = None
    inserted_rows: int | None = None
    updated_rows: int | None = None
    failed_rows: int | None = None
    error: str | None = None
    start_time: str | None = None
    end_time: str | None = None


class SyncAllResponse(BaseModel):
    results: list[SyncResultItem]
    total_success: int
    total_count: int


class SyncLogOut(BaseModel):
    id: int
    data_type: str
    start_time: str
    end_time: str
    status: str
    trigger_type: str
    triggered_by: str | None = None
    file_path: str | None = None
    filename: str | None = None
    total_rows: int | None = None
    inserted_rows: int | None = None
    updated_rows: int | None = None
    failed_rows: int | None = None
    error: str | None = None
    created_at: str
    finished_at: str | None = None


class DataSourceStatus(BaseModel):
    data_type: str
    name: str
    url: str | None = None
    has_credentials: bool = False
    default_sync: bool = True
    enabled: bool = True
    last_sync: str | None = None
    last_status: str | None = None
    last_query_end: str | None = None  # 上次查询截止时间(数据已同步至此时间点)
    last_rows: int | None = None


class ScheduleStatus(BaseModel):
    enabled: bool
    schedule_hours: list[int]
    next_run: str | None = None
    current_progress: dict | None = None
    has_credentials: bool


# ---------- API 端点 ----------


@router.get("/data-sources", response_model=R[list[DataSourceStatus]], summary="获取可同步的数据源列表")
def list_sync_data_sources(db: DBSession, _: CurrentUser):
    """获取所有 BMS 可同步数据源及其最近同步状态(凭证/开关来自 sync_source_configs)"""
    statuses = get_sync_status(db)
    result = [
        DataSourceStatus(
            data_type=s["data_type"],
            name=s["name"],
            url=s.get("url"),
            has_credentials=s.get("has_credentials", False),
            default_sync=s.get("default_sync", True),
            enabled=s.get("enabled", True),
            last_sync=s.get("last_sync"),
            last_status=s.get("last_status"),
            last_query_end=s.get("last_end_time"),
            last_rows=s.get("last_rows"),
        )
        for s in statuses
    ]
    return R(data=result)


@router.post("/sync-all", response_model=R[SyncAllResponse], summary="一键同步 BMS 数据")
async def sync_all_data(payload: SyncAllRequest, current_user: CurrentUser):
    """同步 BMS 数据

    - 可选择同步哪些数据源(退费单/服务请求/提现操作/设备套餐),默认勾选的源
    - 可选择时间范围: 今天(默认)/昨天/过去一周/自定义
    - 每个数据源间隔 65 秒(BMS 1分钟限制)
    - 凭证从 sync_source_configs 读取(按源独立),无需调用方传入
    """
    # 校验 data_types
    valid_types = {"refund", "ticket", "wallet", "device_combo", "cs_record"}
    data_types = payload.data_types if payload.data_types else None  # None → sync_all 用 DEFAULT_SYNC_TYPES
    if data_types:
        for dt in data_types:
            if dt not in valid_types:
                raise HTTPException(status_code=400, detail=f"不支持的数据类型: {dt}, 可选: {valid_types}")

    # 计算时间范围(所有选中的数据源共用同一时间范围)
    start, end = _compute_time_range(payload.time_range, payload.custom_start, payload.custom_end)
    time_ranges = {dt: (start, end) for dt in (data_types or valid_types)}

    results = await sync_all(
        trigger_type="manual",
        triggered_by=current_user.username,
        data_types=data_types,
        time_ranges=time_ranges,
    )

    items = [SyncResultItem(**r) for r in results]
    success_count = sum(1 for r in results if r["success"])

    return R(
        data=SyncAllResponse(
            results=items,
            total_success=success_count,
            total_count=len(items),
        )
    )


@router.get("/sync-logs", response_model=R[list[SyncLogOut]], summary="获取同步日志")
def list_sync_logs(
    db: DBSession,
    _: CurrentUser,
    data_type: str | None = None,
    limit: int = 50,
):
    """获取 BMS 同步日志列表"""
    stmt = select(BmsSyncLog)
    if data_type:
        stmt = stmt.where(BmsSyncLog.data_type == data_type)
    stmt = stmt.order_by(desc(BmsSyncLog.created_at)).limit(limit)

    logs = list(db.scalars(stmt))
    return R(
        data=[
            SyncLogOut(
                id=log.id,
                data_type=log.data_type,
                start_time=log.start_time.isoformat() if log.start_time else "",
                end_time=log.end_time.isoformat() if log.end_time else "",
                status=log.status,
                trigger_type=log.trigger_type,
                triggered_by=log.triggered_by,
                file_path=log.file_path,
                filename=log.filename,
                total_rows=log.total_rows,
                inserted_rows=log.inserted_rows,
                updated_rows=log.updated_rows,
                failed_rows=log.failed_rows,
                error=log.error,
                created_at=log.created_at.isoformat() if log.created_at else "",
                finished_at=log.finished_at.isoformat() if log.finished_at else None,
            )
            for log in logs
        ]
    )


@router.get("/schedule/status", response_model=R[ScheduleStatus], summary="获取定时同步状态")
def get_schedule_status(_: CurrentUser):
    """获取定时同步调度器状态"""
    status = bms_scheduler.get_status()
    return R(data=ScheduleStatus(**status))


@router.post("/schedule/start", response_model=R[ScheduleStatus], summary="启动定时同步")
async def start_schedule(current_user: CurrentUser):
    """启动 BMS 定时同步(8/12/16/20/24 点各执行一次)

    前提: 默认同步的源(refund/ticket/wallet)需在 系统管理-同步设置 中配置账号密码
    """
    if bms_scheduler.is_running:
        return R(data=ScheduleStatus(**bms_scheduler.get_status()))

    # 检查凭证(从 sync_source_configs 判断默认源是否都配了凭证)
    status = bms_scheduler.get_status()
    if not status["has_credentials"]:
        raise HTTPException(
            status_code=400,
            detail="默认同步的源未全部配置凭证,请在 系统管理-同步设置 中配置账号密码",
        )

    # 必须在事件循环中调用,因为 bms_scheduler.start() 内部使用 asyncio.create_task
    bms_scheduler.start()
    return R(data=ScheduleStatus(**status))


@router.post("/schedule/stop", response_model=R[ScheduleStatus], summary="停止定时同步")
def stop_schedule(_: CurrentUser):
    """停止 BMS 定时同步"""
    bms_scheduler.stop()
    return R(data=ScheduleStatus(**bms_scheduler.get_status()))


@router.get("/test", response_model=R[bool], summary="测试浏览器可用性")
async def test_browser():
    """测试 Playwright 浏览器是否可用"""
    async with BmsFetcher() as fetcher:
        result = await fetcher.test_connection()
    return R(data=result)

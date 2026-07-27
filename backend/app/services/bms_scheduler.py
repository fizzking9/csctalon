"""BMS 定时同步调度器

使用 asyncio 后台循环实现,无需额外依赖。
定时: 8:00, 12:00, 16:00, 20:00, 00:00 每天5次。

启动/停止通过 API 控制,状态保持在内存中。
凭证从 sync_source_configs 表读取(按源独立,定时任务无人值守,必须预设)。
仅同步 default_sync=True 的源(DEFAULT_SYNC_TYPES)。
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.services.bms_sync import DEFAULT_SYNC_TYPES, sync_all

logger = logging.getLogger(__name__)

# 定时同步的小时列表(东八区)
SCHEDULE_HOURS = [0, 8, 12, 16, 20]

# 检查间隔(秒)
CHECK_INTERVAL = 60


class BmsScheduler:
    """BMS 定时同步调度器"""

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_run_hour: dict[str, int] = {}  # date_str -> last_run_hour, 防止同一小时重复执行
        self._current_progress: dict = {}  # 当前进度

    @property
    def is_running(self) -> bool:
        return self._running

    @staticmethod
    def _default_sources_have_credentials() -> bool:
        """检查默认同步的源是否都已配置凭证(从 sync_source_configs 读)"""
        from app.core.database import SessionLocal
        from app.services.sync_source_config_service import get_sync_source_config

        if not DEFAULT_SYNC_TYPES:
            return True
        db = SessionLocal()
        try:
            for dt in DEFAULT_SYNC_TYPES:
                cfg = get_sync_source_config(db, dt)
                if not cfg or not cfg.get("username") or not cfg.get("password"):
                    return False
            return True
        finally:
            db.close()

    def get_status(self) -> dict:
        """获取调度器状态"""
        now = datetime.now(timezone(timedelta(hours=8)))
        # 计算下一次运行时间
        today_remaining = [h for h in SCHEDULE_HOURS if h > now.hour]
        if today_remaining:
            next_hour = today_remaining[0]
            next_date = now.date()
        else:
            # 今天没有了,取明天第一个
            next_hour = SCHEDULE_HOURS[0]
            next_date = now.date() + timedelta(days=1)

        next_run = datetime(next_date.year, next_date.month, next_date.day, next_hour, 0, 0)

        return {
            "enabled": self._running,
            "schedule_hours": SCHEDULE_HOURS,
            "next_run": next_run.isoformat(),
            "current_progress": self._current_progress if self._current_progress else None,
            "has_credentials": self._default_sources_have_credentials(),
        }

    def start(self):
        """启动调度器"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"BMS 定时同步已启动,定时: {SCHEDULE_HOURS}")

    def stop(self):
        """停止调度器"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        logger.info("BMS 定时同步已停止")

    async def _run_loop(self):
        """主循环:每分钟检查是否到了定时"""
        while self._running:
            try:
                now = datetime.now(timezone(timedelta(hours=8)))
                date_str = now.strftime("%Y-%m-%d")
                hour = now.hour

                # 检查是否到了定时小时,且本小时还没执行过
                if (
                    hour in SCHEDULE_HOURS
                    and now.minute < 5  # 只在前5分钟内触发(防止错过)
                    and self._last_run_hour.get(date_str) != hour
                ):
                    self._last_run_hour[date_str] = hour
                    logger.info(f"触发定时同步: {date_str} {hour}:00")
                    await self._do_scheduled_sync()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"调度器异常: {e}", exc_info=True)

            # 等待下一次检查
            await asyncio.sleep(CHECK_INTERVAL)

    async def _do_scheduled_sync(self):
        """执行定时同步(仅 DEFAULT_SYNC_TYPES,仅当天数据,凭证从 DB 读)

        时间范围硬性限定为「今天 00:00 ~ now」(东八区),不允许同步过多历史数据。
        每次定时都覆盖当天 0 点至今,upsert 幂等不会产生重复,反而能补回上次失败漏掉的数据。
        """
        if not self._default_sources_have_credentials():
            logger.warning("定时同步跳过: 默认同步的源未全部配置凭证(请在系统管理-同步设置中配置)")
            return

        # 定时同步强制只同步当天数据(今天 00:00 ~ now),防止同步过多历史数据
        TZ_CN = timezone(timedelta(hours=8))
        now_cn = datetime.now(TZ_CN)
        today_start = now_cn.replace(hour=0, minute=0, second=0, microsecond=0)
        sync_types = list(DEFAULT_SYNC_TYPES)
        time_ranges = {dt: (today_start, now_cn) for dt in sync_types}
        logger.info(
            f"定时同步时间范围(仅当天): "
            f"{today_start.strftime('%Y-%m-%d %H:%M:%S')} ~ {now_cn.strftime('%Y-%m-%d %H:%M:%S')} "
            f"数据源={sync_types}"
        )

        async def on_progress(data_type, phase, data):
            if phase == "fetching":
                self._current_progress = {"data_type": data_type, "phase": "正在抓取"}
            elif phase == "waiting":
                self._current_progress = {"data_type": data_type, "phase": "等待60秒(BMS限制)"}
            elif phase == "imported":
                self._current_progress = {"data_type": data_type, "phase": "已导入", "data": data}
            logger.info(f"同步进度: {data_type} - {phase}")

        try:
            results = await sync_all(
                trigger_type="scheduled",
                triggered_by="scheduler",
                on_progress=on_progress,
                data_types=sync_types,
                time_ranges=time_ranges,
            )
            success_count = sum(1 for r in results if r["success"])
            logger.info(f"定时同步完成: {success_count}/{len(results)} 成功")
        except Exception as e:
            logger.error(f"定时同步失败: {e}", exc_info=True)
        finally:
            self._current_progress = {}


# 全局单例
bms_scheduler = BmsScheduler()

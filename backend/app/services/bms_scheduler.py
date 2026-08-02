"""BMS 定时同步调度器

使用 asyncio 后台循环实现,无需额外依赖。
定时: 8:00, 12:00, 16:00, 20:00, 00:00 每天5次。

2026-08-02 优化:
  - 接入 SyncTaskManager 全局互斥锁,防止手动同步时定时任务并发冲突
  - 定时任务失败也会写入 task 状态,进度可通过 /task/status 统一查询
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.services.bms_sync import DEFAULT_SYNC_TYPES, sync_all
from app.services.sync_task_manager import sync_task_manager

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
        """获取调度器状态(含 SyncTaskManager 的当前任务进度)"""
        now = datetime.now(timezone(timedelta(hours=8)))
        today_remaining = [h for h in SCHEDULE_HOURS if h > now.hour]
        if today_remaining:
            next_hour = today_remaining[0]
            next_date = now.date()
        else:
            next_hour = SCHEDULE_HOURS[0]
            next_date = now.date() + timedelta(days=1)

        next_run = datetime(next_date.year, next_date.month, next_date.day, next_hour, 0, 0)

        # 优先展示 task_manager 维护的当前进度
        ct = sync_task_manager.get_current_task()
        current_progress = None
        if ct and ct.status == "running":
            current_progress = {
                "task_id": ct.task_id,
                "data_type": ct.time_ranges_display,
                "phase": f"{ct.trigger_type}|{ct.current_phase}" if hasattr(ct, 'current_phase') else f"{ct.trigger_type}运行中",
                "percent": (ct.to_public_dict().get("percent")),
                "current_source": (ct.to_public_dict().get("current_source")),
                "current_phase": (ct.to_public_dict().get("current_phase")),
            }

        return {
            "enabled": self._running,
            "schedule_hours": SCHEDULE_HOURS,
            "next_run": next_run.isoformat(),
            "current_progress": current_progress,
            "has_credentials": self._default_sources_have_credentials(),
            "busy": sync_task_manager.is_busy(),
            "cooldown_remaining": sync_task_manager.remaining_cooldown_seconds(),
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

                if (
                    hour in SCHEDULE_HOURS
                    and now.minute < 5
                    and self._last_run_hour.get(date_str) != hour
                ):
                    self._last_run_hour[date_str] = hour
                    logger.info(f"触发定时同步: {date_str} {hour}:00")
                    await self._do_scheduled_sync()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"调度器异常: {e}", exc_info=True)

            await asyncio.sleep(CHECK_INTERVAL)

    async def _do_scheduled_sync(self):
        """执行定时同步 - 通过 SyncTaskManager 提交(全局互斥)

        时间范围: 每天 00:00 ~ now(全量重算当日,upsert 幂等不会重复,
        反而能补回上次失败漏掉的;但 task_manager 的增量时间记录仍会写入,
        手动同步走增量时可以接续)。
        """
        if not self._default_sources_have_credentials():
            logger.warning("定时同步跳过: 默认同步的源未全部配置凭证")
            return

        # 如果有手动任务正在跑,跳过本次定时
        if sync_task_manager.is_busy():
            ct = sync_task_manager.get_current_task()
            who = f"{ct.triggered_by} 的手动任务" if ct else "任务"
            logger.info(f"定时同步跳过: 正在执行 {who},等下一个检查点")
            return

        TZ_CN = timezone(timedelta(hours=8))
        now_cn = datetime.now(TZ_CN)
        today_start = now_cn.replace(hour=0, minute=0, second=0, microsecond=0)
        sync_types = list(DEFAULT_SYNC_TYPES)
        time_ranges_dt = {dt: (today_start, now_cn) for dt in sync_types}
        time_ranges_display = {
            dt: (
                today_start.strftime("%m-%d 00:00(今日定时-全量)"),
                now_cn.strftime("%m-%d %H:%M:%S"),
            )
            for dt in sync_types
        }
        logger.info(
            f"定时同步时间范围: "
            f"{today_start.strftime('%Y-%m-%d %H:%M:%S')} ~ {now_cn.strftime('%Y-%m-%d %H:%M:%S')} "
            f"数据源={sync_types}"
        )

        async def runner(task_id: str, on_progress_cb):
            return await sync_all(
                trigger_type="scheduled",
                triggered_by="scheduler",
                on_progress=on_progress_cb,
                data_types=sync_types,
                time_ranges=time_ranges_dt,
            )

        try:
            task_id, msg = await sync_task_manager.submit(
                trigger_type="scheduled",
                triggered_by="scheduler",
                data_types=sync_types,
                time_ranges=time_ranges_dt,
                time_ranges_display=time_ranges_display,
                runner_coro=runner,
                # 定时: 仍受忙锁保护,但忽略冷却(定时即使刚手动完成也允许跑,因为是 0~now 补全更可信)
                ignore_cooldown=True,
            )
            logger.info(f"定时同步已提交: task_id={task_id}, {msg}")
        except RuntimeError as e:
            # 被锁挡住,不重试(下一个检查点会再试)
            logger.warning(f"定时同步未执行: {e}")


# 全局单例
bms_scheduler = BmsScheduler()

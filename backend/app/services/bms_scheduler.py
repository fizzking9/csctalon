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

from sqlalchemy import desc, distinct, select

from app.core.database import SessionLocal
from app.models.bms_sync_log import BmsSyncLog
from app.services.bms_sync import DEFAULT_SYNC_TYPES, sync_all
from app.services.scrm_sync_service import (
    DATA_TYPE_OPERATOR,
    DATA_TYPE_SESSION_GROUP,
    DATA_TYPE_SESSION_PRIVATE,
    DATA_TYPE_SESSION_VIRTUAL,
    sync_scrm_all,
)
from app.services.sync_task_manager import sync_task_manager

logger = logging.getLogger(__name__)

# 定时同步的小时列表(东八区,按从小到大排)
SCHEDULE_HOURS = sorted([0, 8, 12, 16, 20])

# 检查间隔(秒)
CHECK_INTERVAL = 60

# 整点触发宽容窗口(分钟): 在该窗口内命中 SCHEDULE_HOURS 都可触发(防止 service 刚在 12:04 重启, 12:30 就永远漏了)
TRIGGER_WINDOW_MINUTES = 30


class BmsScheduler:
    """BMS 定时同步调度器

    关键机制:
      - 启动时从 bms_sync_logs 回填「今天哪些 scheduled 小时已经成功执行过」,
        避免服务重启导致重复执行或重复漏掉整点。
      - 启动后立刻执行一次「启动补跑」: 所有 SCHEDULE_HOURS 中 <= 当前小时
        且今天没成功执行过的点,立刻补一次(因为窗口已过、不补就永远漏了)。
      - 触发窗口放宽到 30 分钟,只要当前整点仍在该窗口内就允许执行(重启等极端场景兜底)。
    """

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_run_hour: dict[str, set[int]] = {}  # date_str -> set(hours executed OK today)
        self._catch_up_done = False  # 启动补跑是否已执行(避免反复补跑)

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

    @staticmethod
    def _scrm_sources_have_credentials() -> bool:
        """SCRM 配置是否已填写(任一 scrm 源配置即视为可用)"""
        from app.core.database import SessionLocal
        from app.services.sync_source_config_service import get_sync_source_config
        db = SessionLocal()
        try:
            cfg = get_sync_source_config(db, DATA_TYPE_OPERATOR)
            return bool(cfg and cfg.get("enabled", True) and cfg.get("username") and cfg.get("password"))
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
            "has_credentials": {
                "bms": self._default_sources_have_credentials(),
                "scrm": self._scrm_sources_have_credentials(),
            },
            "busy": sync_task_manager.is_busy(),
            "cooldown_remaining": sync_task_manager.remaining_cooldown_seconds(),
        }

    def _reload_today_completed_hours(self) -> None:
        """从 bms_sync_logs 回填「今天哪些 scheduled 小时已经成功执行过」

        判断标准: 今天北京时间下, trigger_type='scheduled' 且 status='success'
        的记录中, finished_at(北京时间)所处的整点小时数。
        这样即使服务重启,也不会重复执行/也不会漏掉已经过去但没执行的点(补跑会管)。
        """
        TZ_CN = timezone(timedelta(hours=8))
        now_cn = datetime.now(TZ_CN)
        today_cn = now_cn.date()
        db = SessionLocal()
        try:
            rows = db.execute(
                select(distinct(BmsSyncLog.finished_at))
                .where(
                    BmsSyncLog.trigger_type == "scheduled",
                    BmsSyncLog.status == "success",
                    BmsSyncLog.finished_at.isnot(None),
                )
                .order_by(desc(BmsSyncLog.finished_at))
            ).all()
            hours_done: set[int] = set()
            for (fin_at,) in rows:
                if fin_at is None:
                    continue
                # 统一转北京时间 naive,比较 date 是否为今天
                if fin_at.tzinfo is None:
                    # 数据库可能直接存 naive UTC,强行标 UTC 再转
                    fin_cn = fin_at.replace(tzinfo=timezone.utc).astimezone(TZ_CN)
                else:
                    fin_cn = fin_at.astimezone(TZ_CN)
                if fin_cn.date() == today_cn:
                    hours_done.add(fin_cn.hour)
            today_key = now_cn.strftime("%Y-%m-%d")
            # 只覆盖今日,其他日期保留为空
            self._last_run_hour = {today_key: hours_done}
            logger.info(
                f"[调度器] 回填今日已完成的定时同步小时: {sorted(hours_done) if hours_done else '（无）'},"
                f" 下一次计划窗口: {[h for h in SCHEDULE_HOURS if h not in hours_done and h >= now_cn.hour]}"
            )
        except Exception as e:
            logger.warning(f"[调度器] 回填已执行小时失败(忽略,冷启动即可): {e}", exc_info=True)
        finally:
            db.close()

    def start(self):
        """启动调度器

        步骤:
          1. 从 bms_sync_logs 回填今天已经成功完成过的 scheduled 小时
          2. 启动后台循环
          3. 在后台循环的第一次检查里执行「启动补跑」(SCHEDULE_HOURS 中已过但今日未成功的小时立刻补一次)
        """
        if self._running:
            return
        self._running = True
        self._reload_today_completed_hours()
        self._catch_up_done = False
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            f"BMS 定时同步已启动, 定时小时={SCHEDULE_HOURS} (宽容窗口{TRIGGER_WINDOW_MINUTES}分钟)"
        )

    def stop(self):
        """停止调度器"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        logger.info("BMS 定时同步已停止")

    async def _run_loop(self):
        """主循环:每分钟检查是否到了定时;首次检查先做启动补跑"""
        TZ_CN = timezone(timedelta(hours=8))
        first_tick = True
        while self._running:
            try:
                now_cn = datetime.now(TZ_CN)
                date_str = now_cn.strftime("%Y-%m-%d")
                today_done = self._last_run_hour.setdefault(date_str, set())

                # ── 启动补跑(仅执行一次): 所有 <= 当前小时的 SCHEDULE_HOURS,若今天没成功就立刻补 ──
                if first_tick and not self._catch_up_done:
                    self._catch_up_done = True
                    missed = [h for h in SCHEDULE_HOURS if h <= now_cn.hour and h not in today_done]
                    if missed:
                        logger.info(
                            f"[调度器] 启动补跑: 今日遗漏的整点小时={missed}"
                            f", 立刻触发一次全量(00:00~now)补同步"
                        )
                        await self._do_scheduled_sync(why=f"启动补跑错过窗口{missed}")
                        first_tick = False
                        # 补跑完成后, 将 <= now_cn.hour 的所有 SCHEDULE_HOURS 标记为已处理
                        # (只做一次补跑,覆盖前面所有遗漏点,避免短时间连跑)
                        for h in missed:
                            today_done.add(h)
                        await asyncio.sleep(CHECK_INTERVAL)
                        continue
                    first_tick = False

                # ── 常规定时检查: 在 [hour:00, hour:TRIGGER_WINDOW_MINUTES) 窗口内触发 ──
                if (
                    now_cn.hour in SCHEDULE_HOURS
                    and now_cn.minute < TRIGGER_WINDOW_MINUTES
                    and now_cn.hour not in today_done
                ):
                    today_done.add(now_cn.hour)
                    logger.info(f"触发定时同步: {date_str} {now_cn.hour}:00 ({now_cn.minute}分,窗口内)")
                    await self._do_scheduled_sync(why=f"定时触发 {now_cn.hour}:00")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"调度器异常: {e}", exc_info=True)

            await asyncio.sleep(CHECK_INTERVAL)

    async def _do_scheduled_sync(self, why: str | None = None):
        """执行定时同步 - 通过 SyncTaskManager 提交(全局互斥)

        同一时间窗口内同时跑:
          - BMS sync_all (DEFAULT_SYNC_TYPES, 00:00 ~ now 全量幂等)
          - SCRM sync_scrm_all (4 个 scrm 数据源, 按各自上次成功时间增量)

        任一未配置则跳过,不影响另一个。
        """
        reason = f" [{why}]" if why else ""
        has_bms = self._default_sources_have_credentials()
        has_scrm = self._scrm_sources_have_credentials()
        if not has_bms and not has_scrm:
            logger.warning(f"定时同步跳过{reason}: BMS 和 SCRM 均未配置凭证")
            return

        # 如果有手动任务正在跑,跳过本次定时
        if sync_task_manager.is_busy():
            ct = sync_task_manager.get_current_task()
            who = f"{ct.triggered_by} 的手动任务" if ct else "任务"
            logger.info(f"定时同步跳过{reason}: 正在执行 {who},等下一个检查点")
            return

        TZ_CN = timezone(timedelta(hours=8))
        now_cn = datetime.now(TZ_CN)
        today_start = now_cn.replace(hour=0, minute=0, second=0, microsecond=0)

        # 构造 BMS 时间范围 & data_types
        sync_types_bms = list(DEFAULT_SYNC_TYPES) if has_bms else []
        time_ranges_bms_dt = {dt: (today_start, now_cn) for dt in sync_types_bms} if has_bms else {}

        # SCRM 的 4 个 data_type,时间范围在 sync_scrm_all 内部按 last_success_end_time 计算,这里仅用于 task_manager display
        sync_types_scrm = [
            DATA_TYPE_OPERATOR, DATA_TYPE_SESSION_PRIVATE, DATA_TYPE_SESSION_GROUP, DATA_TYPE_SESSION_VIRTUAL,
        ] if has_scrm else []

        all_types = sync_types_bms + sync_types_scrm
        time_ranges_display = {}
        for dt in sync_types_bms:
            time_ranges_display[dt] = (
                today_start.strftime("%m-%d 00:00(今日定时-全量)"),
                now_cn.strftime("%m-%d %H:%M:%S"),
            )
        for dt in sync_types_scrm:
            time_ranges_display[dt] = (
                "按 last_success 增量",
                now_cn.strftime("%m-%d %H:%M:%S"),
            )

        logger.info(
            f"[定时同步{reason}] 数据源={all_types} (BMS={'ON' if has_bms else 'SKIP'}, SCRM={'ON' if has_scrm else 'SKIP'})"
        )

        async def runner(task_id: str, on_progress_cb):
            combined = []
            # ── P2-3: 调度器拆开 (BMS ↔ SCRM 串行但独立分段,日志清晰) ──
            #   历史问题: 原来虽然是顺序执行,但没有分界日志,当一个失败另一个继续时,
            #   日志里看不清是 BMS 还是 SCRM 的问题。这里加显式分段:
            #     1) BMS 完成后 → 打成功/失败汇总 → 立刻进 SCRM,互不阻塞
            #     2) 任一子任务里单个 data_type 失败, 不会污染另一个
            #   注意: 不做真正多进程/多线程拆分 (SyncTaskManager 是单全局互斥锁),
            #        但 "分段 + 清晰日志" 已经能显著降低定位成本。
            # ── 1) BMS 子任务 ──
            if has_bms:
                logger.info(f"[定时同步{reason}] ▶▶▶ BMS 子任务开始: data_types={sync_types_bms}")
                try:
                    bms_res = await sync_all(
                        trigger_type="scheduled",
                        triggered_by="scheduler",
                        on_progress=on_progress_cb,
                        data_types=sync_types_bms,
                        time_ranges=time_ranges_bms_dt,
                    )
                    combined.extend(bms_res if isinstance(bms_res, list) else [])
                    ok_n = sum(1 for r in (bms_res or []) if isinstance(r, dict) and r.get("success"))
                    logger.info(
                        f"[定时同步{reason}] ◀◀◀ BMS 子任务完成: "
                        f"{ok_n}/{len(sync_types_bms)} 成功"
                    )
                except Exception as e:  # noqa: BLE001
                    logger.exception(f"[定时同步{reason}] ◀◀◀ BMS 子任务异常(全标失败): {e}")
                    combined.extend([{"data_type": dt, "success": False, "error": str(e)} for dt in sync_types_bms])
            # ── 2) SCRM 子任务 ──
            if has_scrm:
                logger.info(f"[定时同步{reason}] ▶▶▶ SCRM 子任务开始: data_types={sync_types_scrm}")
                try:
                    scrm_res = await sync_scrm_all(
                        trigger_type="scheduled",
                        triggered_by="scheduler",
                        on_progress=on_progress_cb,
                        sync_types=sync_types_scrm,
                    )
                    combined.extend(scrm_res if isinstance(scrm_res, list) else [])
                    ok_n = sum(1 for r in (scrm_res or []) if isinstance(r, dict) and r.get("success"))
                    logger.info(
                        f"[定时同步{reason}] ◀◀◀ SCRM 子任务完成: "
                        f"{ok_n}/{len(sync_types_scrm)} 成功"
                    )
                except Exception as e:  # noqa: BLE001
                    logger.exception(f"[定时同步{reason}] ◀◀◀ SCRM 子任务异常(全标失败): {e}")
                    combined.extend([{"data_type": dt, "success": False, "error": str(e)} for dt in sync_types_scrm])
            # 最终汇总
            total_ok = sum(1 for r in combined if isinstance(r, dict) and r.get("success"))
            logger.info(
                f"[定时同步{reason}] 全部子任务结束: {total_ok}/{len(combined)} 成功"
                + (f", failures={[(r.get('data_type'), r.get('error')) for r in combined if isinstance(r, dict) and not r.get('success')]}" if total_ok != len(combined) else "")
            )
            return combined

        try:
            task_id, msg = await sync_task_manager.submit(
                trigger_type="scheduled",
                triggered_by="scheduler",
                data_types=all_types,
                time_ranges={**time_ranges_bms_dt},
                time_ranges_display=time_ranges_display,
                runner_coro=runner,
                ignore_cooldown=True,
            )
            logger.info(f"[定时同步{reason}] 已提交: task_id={task_id}, {msg}")
        except RuntimeError as e:
            # 被锁挡住,不重试(下一个检查点会再试)
            logger.warning(f"[定时同步{reason}] 未执行: {e}")


# 全局单例
bms_scheduler = BmsScheduler()

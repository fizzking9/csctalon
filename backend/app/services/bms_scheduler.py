"""BMS 定时同步调度器

使用 asyncio 后台循环实现,无需额外依赖。

2026-08-13 调度策略变更:
  - 白天同步: 8:00, 9:00, 10:00, ..., 23:00, 00:00 (每小时一次,共 17 次)
  - 补跑同步: 3:00 对前一天做整体同步(兜底确保数据完整)
  - 出错熔断: 白天任何一次同步失败(partial/failed), 当天剩余自动同步全部停止,
    需手动(触发手动同步或调用 /schedule/start)恢复

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

# ── 调度时间配置 ──────────────────────────────────────────────────────
# 白天每小时同步: 8,9,...,23,0  (共 17 次, 0 即 24:00 午夜)
DAYTIME_HOURS: frozenset[int] = frozenset([*range(8, 24), 0])
# 凌晨 3 点: 对前一天做整体同步
CATCHUP_HOUR: int = 3
# 合并后全部需要检测的小时(用于 schedule_hours 对外展示)
SCHEDULE_HOURS = sorted([*DAYTIME_HOURS, CATCHUP_HOUR])

# 检查间隔(秒)
CHECK_INTERVAL = 60

# 整点触发宽容窗口(分钟): 在该窗口内命中 SCHEDULE_HOURS 都可触发
TRIGGER_WINDOW_MINUTES = 30


class BmsScheduler:
    """BMS 定时同步调度器

    关键机制:
      - 启动时从 bms_sync_logs 回填「今天哪些 scheduled 小时已经成功执行过」,
        避免服务重启导致重复执行或重复漏掉整点。
      - 启动后立刻执行一次「启动补跑」: 所有 SCHEDULE_HOURS 中 <= 当前小时
        且今天没成功执行过的点,立刻补一次(因为窗口已过、不补就永远漏了)。
      - 触发窗口放宽到 30 分钟,只要当前整点仍在该窗口内就允许执行(重启等极端场景兜底)。
      - 专项级熔断(2026-08-13 调整): 白天同步中, 若某个 data_type(专项)同步失败,
        则**仅该专项**在当天剩余自动同步中暂停执行(跳过); 其他成功的专项照常每小时同步。
        手动触发任何同步 / 调用 /schedule/start 会清除当前业务日期的全部专项熔断标记。
        凌晨 3:00 的「前一天补跑」不受熔断影响(强制跑所有源)。
    """

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_run_hour: dict[str, set[int]] = {}  # date_str -> set(hours executed OK today)
        self._catch_up_done = False  # 启动补跑是否已执行(避免反复补跑)
        # 专项级熔断: date_str -> set(已失败暂停的 data_type 名称)
        self._halted_types: dict[str, set[str]] = {}

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

    def reset_halt(self) -> None:
        """清除当前业务日期的**所有专项熔断标记**(用户手动启动/手动同步时调用)"""
        TZ_CN = timezone(timedelta(hours=8))
        now_cn = datetime.now(TZ_CN)
        # 小时 0~3 时, 熔断仍然属于「昨天」的业务日期
        biz_date = now_cn.date() if now_cn.hour >= 4 else (now_cn - timedelta(days=1)).date()
        biz_key = biz_date.strftime("%Y-%m-%d")
        halted_set = self._halted_types.get(biz_key)
        if halted_set:
            n = len(halted_set)
            self._halted_types[biz_key] = set()
            logger.info(f"[调度器] 已清除 {biz_key} 的 {n} 个专项熔断标记({sorted(halted_set)}),自动同步恢复")

    def get_status(self) -> dict:
        """获取调度器状态(含 SyncTaskManager 的当前任务进度 + 专项级熔断状态)"""
        TZ_CN = timezone(timedelta(hours=8))
        now = datetime.now(TZ_CN)
        # 业务日期: 4 点之前仍算「昨天」(这样 0:00~3:59 看到的 halt 也是昨天的)
        biz_date = now.date() if now.hour >= 4 else (now - timedelta(days=1)).date()
        biz_key = biz_date.strftime("%Y-%m-%d")
        halted_types = sorted(self._halted_types.get(biz_key) or set())
        has_any_halt = bool(halted_types)

        # 下一次运行: 专项级熔断不影响下一个小时的触发(仍会尝试,只是跳过已熔断的专项)
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
            "daytime_hours": sorted(DAYTIME_HOURS),
            "catchup_hour": CATCHUP_HOUR,
            "next_run": next_run.isoformat(),
            "current_progress": current_progress,
            "has_credentials": {
                "bms": self._default_sources_have_credentials(),
                "scrm": self._scrm_sources_have_credentials(),
            },
            "busy": sync_task_manager.is_busy(),
            "cooldown_remaining": sync_task_manager.remaining_cooldown_seconds(),
            # 专项级熔断: 是否有至少一个专项暂停 + 具体暂停了哪些专项
            "is_halted": has_any_halt,
            "halted_biz_date": biz_key if has_any_halt else None,
            "halted_types": halted_types,
            "halted_count": len(halted_types),
        }

    def _reload_today_completed_hours(self) -> None:
        """从 bms_sync_logs 回填「今天哪些 scheduled 小时已经成功执行过」

        判断标准: 今天北京时间下, trigger_type='scheduled' 且 status='success'
        的记录中, finished_at(北京时间)所处的整点小时数。
        这样即使服务重启,也不会重复执行/也不会漏掉已经过去但没执行的点(补跑会管)。

        同时 best-effort 回填**专项级**熔断: 若「业务日期当天」存在
        trigger_type=scheduled 且 status=failed 的日志, 把对应 data_type 加入
        _halted_types[biz_key] 集合(后续可通过手动同步 / schedule start 手动清除)。
        """
        from app.models.scrm_sync_log import ScrmSyncLog

        TZ_CN = timezone(timedelta(hours=8))
        now_cn = datetime.now(TZ_CN)
        biz_date = now_cn.date() if now_cn.hour >= 4 else (now_cn - timedelta(days=1)).date()
        biz_key = biz_date.strftime("%Y-%m-%d")
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
                if fin_at.tzinfo is None:
                    fin_cn = fin_at.replace(tzinfo=timezone.utc).astimezone(TZ_CN)
                else:
                    fin_cn = fin_at.astimezone(TZ_CN)
                if fin_cn.date() == today_cn:
                    hours_done.add(fin_cn.hour)
            today_key = now_cn.strftime("%Y-%m-%d")
            self._last_run_hour = {today_key: hours_done}

            # ── 回填专项熔断(最佳努力): 逐个 data_type 扫描失败记录 ──
            halted_dt: set[str] = set()
            try:
                # (ModelCls, fin_col, data_type_col) 三元组
                for ModelCls, fin_col, dt_col in [
                    (BmsSyncLog, BmsSyncLog.finished_at, BmsSyncLog.data_type),
                    (ScrmSyncLog, ScrmSyncLog.finished_at, ScrmSyncLog.data_type),
                ]:
                    fail_rows = db.execute(
                        select(dt_col, fin_col)
                        .where(
                            ModelCls.trigger_type == "scheduled",
                            ModelCls.status == "failed",
                            fin_col.isnot(None),
                        )
                        .order_by(desc(fin_col))
                        .limit(200)
                    ).all()
                    for dt_name, fin_at in fail_rows:
                        if fin_at is None or dt_name is None:
                            continue
                        if fin_at.tzinfo is None:
                            fin_cn = fin_at.replace(tzinfo=timezone.utc).astimezone(TZ_CN)
                        else:
                            fin_cn = fin_at.astimezone(TZ_CN)
                        fin_biz = fin_cn.date() if fin_cn.hour >= 4 else (fin_cn - timedelta(days=1)).date()
                        if fin_biz == biz_date:
                            halted_dt.add(dt_name)
            except Exception as he:
                logger.warning(f"[调度器] 回填专项熔断失败(忽略): {he}")
            if halted_dt:
                self._halted_types[biz_key] = halted_dt

            logger.info(
                f"[调度器] 回填: 今日已成功小时={sorted(hours_done) if hours_done else '（无）'},"
                f" 已熔断专项={sorted(halted_dt) if halted_dt else '无'} (biz_date={biz_key}),"
                f" 下一次计划窗口: {[h for h in SCHEDULE_HOURS if h not in hours_done and h >= now_cn.hour]}"
            )
        except Exception as e:
            logger.warning(f"[调度器] 回填已执行小时失败(忽略,冷启动即可): {e}", exc_info=True)
        finally:
            db.close()

    def start(self):
        """启动调度器(手动启动=用户确认继续,会清除当前熔断)

        步骤:
          1. 清除当前业务日期的熔断标记(用户手动恢复)
          2. 从 bms_sync_logs 回填今天已经成功完成过的 scheduled 小时
          3. 启动后台循环
          4. 在后台循环的第一次检查里执行「启动补跑」(SCHEDULE_HOURS 中已过但今日未成功的小时立刻补一次)
        """
        if self._running:
            return
        self.reset_halt()
        self._running = True
        self._reload_today_completed_hours()
        self._catch_up_done = False
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            f"BMS 定时同步已启动, 白天小时(8-23+0)={sorted(DAYTIME_HOURS)},"
            f" 补跑={CATCHUP_HOUR}:00, 宽容窗口{TRIGGER_WINDOW_MINUTES}分钟"
        )

    def stop(self):
        """停止调度器"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        logger.info("BMS 定时同步已停止")

    async def _run_loop(self):
        """主循环:每分钟检查是否到了定时;首次检查先做启动补跑

        调度策略(2026-08-13 调整为专项级熔断):
          - 白天: DAYTIME_HOURS (8,9,...,23,0) 每小时同步;
            若某 data_type(专项)失败 → 仅暂停该专项当天后续小时; 其他专项不受影响
          - 凌晨 CATCHUP_HOUR (3:00): 对「前一天」做整体补跑,忽略所有专项熔断
        """
        TZ_CN = timezone(timedelta(hours=8))
        first_tick = True
        while self._running:
            try:
                now_cn = datetime.now(TZ_CN)
                date_str = now_cn.strftime("%Y-%m-%d")
                today_done = self._last_run_hour.setdefault(date_str, set())
                # 业务日期(用于熔断判断): hour 0~3 仍归属前一天
                biz_date = now_cn.date() if now_cn.hour >= 4 else (now_cn - timedelta(days=1)).date()
                biz_key = biz_date.strftime("%Y-%m-%d")
                halted_today = self._halted_types.get(biz_key) or set()

                # ── 启动补跑(仅一次): DAYTIME_HOURS 中 <= 当前小时且未成功的,立即补一次 ──
                if first_tick and not self._catch_up_done:
                    self._catch_up_done = True
                    daytime_sorted = sorted(DAYTIME_HOURS)
                    if now_cn.hour >= 8:
                        candidate_hours = [h for h in daytime_sorted if 8 <= h <= now_cn.hour]
                    elif now_cn.hour == 0:
                        candidate_hours = [0]
                    else:
                        candidate_hours = []
                    missed = [h for h in candidate_hours if h not in today_done]
                    if missed:
                        # 专项级: 只要还有未熔断的专项就值得补跑(已熔断的会在 runner 里跳过)
                        logger.info(
                            f"[调度器] 启动补跑: 今日遗漏白天小时={missed}"
                            f", 已熔断专项={sorted(halted_today) if halted_today else '无'}, "
                            f"立刻触发一次全量(00:00~now)补同步"
                        )
                        await self._do_scheduled_sync(
                            why=f"启动补跑错过窗口{missed}",
                            yesterday_full_sync=False,
                            biz_key=biz_key,
                        )
                        first_tick = False
                        for h in missed:
                            today_done.add(h)
                        await asyncio.sleep(CHECK_INTERVAL)
                        continue
                    first_tick = False

                # ── 1) 3:00 补跑: 对前一天整体同步(强制跑所有源,忽略专项熔断) ──
                if (
                    now_cn.hour == CATCHUP_HOUR
                    and now_cn.minute < TRIGGER_WINDOW_MINUTES
                    and CATCHUP_HOUR not in today_done
                ):
                    today_done.add(CATCHUP_HOUR)
                    logger.info(
                        f"触发凌晨补跑({date_str} 0{CATCHUP_HOUR}:00, {now_cn.minute}分窗口内)"
                        " → 对前一天(整天)做整体同步(忽略熔断)"
                    )
                    await self._do_scheduled_sync(
                        why=f"凌晨补跑 前一天全量 {CATCHUP_HOUR}:00",
                        yesterday_full_sync=True,
                        biz_key=biz_key,
                    )

                # ── 2) 白天每小时同步(8~23, 0) ──
                if now_cn.hour in DAYTIME_HOURS and now_cn.minute < TRIGGER_WINDOW_MINUTES and now_cn.hour not in today_done:
                    today_done.add(now_cn.hour)
                    # 快速预检: 当前有资格同步的数据源 = (BMS 默认 + SCRM 有凭证) ∩ 未熔断
                    # 预检失败 → 整小时跳过(打一行提示,避免空 task)
                    bms_candidate = list(DEFAULT_SYNC_TYPES) if self._default_sources_have_credentials() else []
                    scrm_candidate = [
                        DATA_TYPE_OPERATOR, DATA_TYPE_SESSION_PRIVATE,
                        DATA_TYPE_SESSION_GROUP, DATA_TYPE_SESSION_VIRTUAL,
                    ] if self._scrm_sources_have_credentials() else []
                    effective_types = [
                        dt for dt in (bms_candidate + scrm_candidate) if dt not in halted_today
                    ]
                    if not effective_types:
                        if now_cn.minute < 1:
                            logger.warning(
                                f"[调度器] {biz_key} 本次 {now_cn.hour:02d}:00 所有源均已熔断或无凭证"
                                f" (已暂停={sorted(halted_today)}), 跳过本轮"
                            )
                    else:
                        if halted_today and now_cn.minute < 1:
                            logger.info(
                                f"[调度器] {biz_key} {now_cn.hour:02d}:00 触发白天同步,"
                                f" 已熔断专项(将跳过)={sorted(halted_today)},"
                                f" 本次执行专项={effective_types}"
                            )
                        else:
                            logger.info(
                                f"触发白天定时同步: {date_str} {now_cn.hour:02d}:00"
                                f" ({now_cn.minute}分,窗口内)"
                            )
                        await self._do_scheduled_sync(
                            why=f"定时触发 {now_cn.hour:02d}:00",
                            yesterday_full_sync=False,
                            biz_key=biz_key,
                        )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"调度器异常: {e}", exc_info=True)

            await asyncio.sleep(CHECK_INTERVAL)

    async def _do_scheduled_sync(
        self,
        why: str | None = None,
        yesterday_full_sync: bool = False,
        biz_key: str | None = None,
    ):
        """执行定时同步 - 通过 SyncTaskManager 提交(全局互斥)

        Args:
            why: 人类可读的触发原因(打日志用)
            yesterday_full_sync: True=凌晨 3 点补跑模式,时间范围锁定「前一天整天」,
                                 忽略专项熔断,失败也不写入熔断;
                                 False=白天正常同步,时间「今天 0 点~now」,
                                 执行前先跳过 biz_key 对应日期里已熔断的专项,
                                 完成后逐个失败 data_type 写入熔断集合
            biz_key: 业务日期字符串(YYYY-MM-DD),专项熔断集合归属的日期;
                     None 时按 now 即时推导

        同一时间窗口内串行跑: BMS sync_all → SCRM sync_scrm_all
        """
        reason = f" [{why}]" if why else ""
        TZ_CN = timezone(timedelta(hours=8))
        now_cn = datetime.now(TZ_CN)
        if biz_key is None:
            biz_date = now_cn.date() if now_cn.hour >= 4 else (now_cn - timedelta(days=1)).date()
            biz_key = biz_date.strftime("%Y-%m-%d")
        halted_set = set(self._halted_types.get(biz_key) or set())  # copy,避免中途被改

        has_bms = self._default_sources_have_credentials()
        has_scrm = self._scrm_sources_have_credentials()

        # ── 时间范围: 补跑模式=昨天整天; 白天模式=今天 0 点~now ──
        if yesterday_full_sync:
            yesterday_date = now_cn.date() - timedelta(days=1)
            range_start = datetime(yesterday_date.year, yesterday_date.month, yesterday_date.day,
                                   0, 0, 0, tzinfo=TZ_CN)
            range_end = datetime(now_cn.year, now_cn.month, now_cn.day,
                                 0, 0, 0, tzinfo=TZ_CN)
            start_disp = range_start.strftime("%m-%d 00:00(前一天全量)")
        else:
            range_start = now_cn.replace(hour=0, minute=0, second=0, microsecond=0)
            range_end = now_cn
            start_disp = range_start.strftime("%m-%d 00:00(今日定时-全量)")

        # ── 过滤专项级熔断(仅白天同步生效;3 点补跑强制全部执行) ──
        sync_types_bms_raw = list(DEFAULT_SYNC_TYPES) if has_bms else []
        sync_types_scrm_raw = [
            DATA_TYPE_OPERATOR, DATA_TYPE_SESSION_PRIVATE,
            DATA_TYPE_SESSION_GROUP, DATA_TYPE_SESSION_VIRTUAL,
        ] if has_scrm else []
        if yesterday_full_sync:
            sync_types_bms = sync_types_bms_raw
            sync_types_scrm = sync_types_scrm_raw
        else:
            skipped_bms = [dt for dt in sync_types_bms_raw if dt in halted_set]
            skipped_scrm = [dt for dt in sync_types_scrm_raw if dt in halted_set]
            sync_types_bms = [dt for dt in sync_types_bms_raw if dt not in halted_set]
            sync_types_scrm = [dt for dt in sync_types_scrm_raw if dt not in halted_set]
            if skipped_bms or skipped_scrm:
                logger.info(
                    f"[定时同步{reason}] 专项熔断跳过(白天模式):"
                    f" BMS跳过={skipped_bms}, SCRM跳过={skipped_scrm},"
                    f" 剩余 BMS={sync_types_bms}, SCRM={sync_types_scrm}"
                )

        all_types = sync_types_bms + sync_types_scrm
        if not all_types:
            logger.warning(f"[定时同步{reason}] 过滤后无有效数据源可执行,直接跳过本轮")
            return

        if sync_task_manager.is_busy():
            ct = sync_task_manager.get_current_task()
            who = f"{ct.triggered_by} 的手动任务" if ct else "任务"
            logger.info(f"[定时同步{reason}] 跳过: 正在执行 {who},等下一个检查点")
            return

        time_ranges_bms_dt = {dt: (range_start, range_end) for dt in sync_types_bms}
        time_ranges_display = {}
        for dt in sync_types_bms:
            time_ranges_display[dt] = (start_disp, range_end.strftime("%m-%d %H:%M:%S"))
        for dt in sync_types_scrm:
            time_ranges_display[dt] = (
                "按 last_success 增量",
                range_end.strftime("%m-%d %H:%M:%S"),
            )

        logger.info(
            f"[定时同步{reason}] 数据源={all_types}"
            f" (BMS={'ON' if sync_types_bms else 'SKIP'},"
            f" SCRM={'ON' if sync_types_scrm else 'SKIP'},"
            f" mode={'前一天全量(忽略熔断)' if yesterday_full_sync else '白天增量(专项级过滤)'})"
        )

        async def runner(task_id: str, on_progress_cb):
            combined = []
            # ── 1) BMS 子任务 ──
            if sync_types_bms:
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
            if sync_types_scrm:
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
            total_ok = sum(1 for r in combined if isinstance(r, dict) and r.get("success"))
            logger.info(
                f"[定时同步{reason}] 全部子任务结束: {total_ok}/{len(combined)} 成功"
                + (
                    f", failures={[(r.get('data_type'), r.get('error')) for r in combined if isinstance(r, dict) and not r.get('success')]}"
                    if total_ok != len(combined) else ""
                )
            )
            return combined

        task_id: str | None = None
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
            logger.warning(f"[定时同步{reason}] 未执行: {e}")
            return

        # ── 等待任务完成,并按专项写入熔断 ──
        if task_id is None:
            return
        poll_count = 0
        task_results: list[dict] | None = None
        while poll_count < 60 * 60 * 4:  # 最多 4 小时兜底
            await asyncio.sleep(15)
            poll_count += 1
            t = sync_task_manager.get_task(task_id)
            if t and t.status in ("success", "failed", "partial"):
                task_results = t.results
                break
        if task_results is None:
            # 超时拿不到结果 → 保守把本轮 all_types 全部视为失败(仅白天模式写熔断)
            logger.warning(f"[定时同步{reason}] 等待任务完成超时(task_id={task_id})")
            if not yesterday_full_sync:
                s = self._halted_types.setdefault(biz_key, set())
                for dt in all_types:
                    s.add(dt)
                logger.error(
                    f"[调度器] 超时保守熔断: biz_date={biz_key}, 标记暂停={all_types}"
                )
            return

        failed_types = [
            r.get("data_type") for r in task_results
            if isinstance(r, dict) and not r.get("success") and r.get("data_type")
        ]
        success_count = len(task_results) - len(failed_types)
        logger.info(
            f"[定时同步{reason}] 任务结束: task_id={task_id},"
            f" {success_count}/{len(task_results)} 成功,"
            f" 失败专项={failed_types}"
        )

        # 专项级熔断: 仅白天模式,逐个失败 data_type 写入集合(已有的不重复加)
        if not yesterday_full_sync and failed_types:
            s = self._halted_types.setdefault(biz_key, set())
            newly_halted = [dt for dt in failed_types if dt not in s]
            for dt in failed_types:
                s.add(dt)
            if newly_halted:
                logger.error(
                    f"[调度器] 专项熔断更新: biz_date={biz_key},"
                    f" 本轮新增暂停={newly_halted},"
                    f" 累计已暂停专项={sorted(s)}"
                )


# 全局单例
bms_scheduler = BmsScheduler()

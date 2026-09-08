"""BMS 定时同步调度器

使用 asyncio 后台循环实现,无需额外依赖。

2026-08-13 调度策略变更:
  - 白天同步: 8:00, 9:00, 10:00, ..., 23:00, 00:00 (每小时一次,共 17 次)
  - 补跑同步: 5:00 对前一天做整体同步(兜底确保数据完整)
  - 出错熔断: 白天任何一次同步失败(partial/failed), 当天剩余自动同步全部停止,
    需手动(触发手动同步或调用 /schedule/start)恢复

2026-08-25 调度策略增强(应对 BMS 数据按登记时间分批滞后 1~2 天):
  - 8:00 改为「近 7 天窗口」全量兜底同步(覆盖补全数据回填, 忽略专项熔断)
  - 9:00~23:00, 00:00 保持「当天窗口」增量同步(带专项熔断过滤)
  - 5:00 前一天补跑(2026-08-31 调整: 原 3:00, 因 BMS 平台凌晨维护时段采集易失败,
    挪到 5:00 避开维护窗口, 语义不变仍是「前一天整体兜底」)

2026-08-02 优化:
  - 接入 SyncTaskManager 全局互斥锁,防止手动同步时定时任务并发冲突
  - 定时任务失败也会写入 task 状态,进度可通过 /task/status 统一查询
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, distinct, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.bms_sync_log import BmsSyncLog
from app.services.bms_sync import DEFAULT_SYNC_TYPES, sync_all
from app.services.circuit_breaker import (
    clear_halt_recovery,
    mark_probed,
    record_failure,
    reset_circuit,
    should_probe,
)
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
# 凌晨 5 点: 对前一天做整体同步(2026-08-31 由 3:00 调整, 避开 BMS 凌晨维护时段)
CATCHUP_HOUR: int = 5
# 早上 8:00 全量兜底同步窗口(天): BMS 数据按登记时间分批滞后 1~2 天,
# 8 点用近 N 天窗口主动拉一次, 确保补全数据回填(与 BMS 导出近 7 天滚动对齐)
FULL_WINDOW_DAYS: int = 7
# 低频源每日定时档(时)(A-20260907-001 LOCKED / #31): 每天该整点对「DB default_sync=true
# 且非高频 4 源」的大表/报表源(device_combo/bms_order/退货/设备标签/退款记录报表/闪猫等)
# 跑"前一天整天"。默认 6:00(避开 5:00 缺口清点与 8:00 全量); settings.LOW_FREQ_HOUR<=0 禁用。
LOW_FREQ_HOUR: int = max(0, int(getattr(settings, "LOW_FREQ_HOUR", 6) or 0))
# 合并后全部需要检测的小时(用于 schedule_hours 对外展示)
SCHEDULE_HOURS = sorted([*DAYTIME_HOURS, CATCHUP_HOUR] + ([LOW_FREQ_HOUR] if LOW_FREQ_HOUR > 0 else []))
# 低频档忙碌顺延结束小时: 6:00 启动后若一直忙, 直到该小时前必须尝试启动(超过则当日放弃让位 8:00)
LOW_FREQ_DEADLINE_HOUR: int = 7

# ── 检查间隔(秒) ──
CHECK_INTERVAL = 60

# 整点触发宽容窗口(分钟): 在该窗口内命中 SCHEDULE_HOURS 都可触发
TRIGGER_WINDOW_MINUTES = 30


def should_run_low_freq(
    now_hour: int,
    *,
    low_freq_hour: int = LOW_FREQ_HOUR,
    deadline_hour: int = LOW_FREQ_DEADLINE_HOUR,
    done: bool = False,
    busy: bool = False,
) -> bool:
    """低频档本轮是否应执行(纯函数, 供单测锁定忙顺延语义)。

    #31 / A-20260907-001 LOCKED。修复 Review CRITICAL(2026-09-07):
      旧实现 _in_low_slack 用 `6 < hour < 7` 恒 False + 遇忙先置 done → 静默丢档。
    新语义:
      - 触发窗口 = [low_freq_hour, deadline_hour) 即 6:00~6:59;
      - 窗口内且**空闲**(not busy) → True(执行并记 done);
      - 窗口内 busy → False(不记 done, 下分钟 tick 重试 = 真忙顺延);
      - 达到 deadline_hour(7:00)→ False + 上层记 done 放弃(告警)。
    """
    if done:
        return False
    if low_freq_hour <= 0:
        return False
    if now_hour >= deadline_hour:
        return False  # 已过截止, 上层置 done + 告警放弃
    if now_hour < low_freq_hour:
        return False  # 未到低频小时
    # low_freq_hour <= now_hour < deadline_hour
    return not busy


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
        凌晨 5:00 的「前一天补跑」不受熔断影响(强制跑所有源)。
    """

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_run_hour: dict[str, set[int]] = {}  # date_str -> set(hours executed OK today)
        self._catch_up_done = False  # 启动补跑是否已执行(避免反复补跑)
        # 专项级熔断: date_str -> set(已失败暂停的 data_type 名称)
        self._halted_types: dict[str, set[str]] = {}
        # 预警扫描: date_str -> set(已扫描的小时), 一小时最多一次(scan_alerts 已做日幂等)
        self._alert_scanned_hours: dict[str, set[int]] = {}
        # 低频档当日是否已执行/已尝试(A-20260907-001 / #31): date_str -> bool
        # 与 _last_run_hour 分开: 低频小时(6:00)不在 DAYTIME_HOURS, 用独立日标记防重复
        self._low_freq_done: dict[str, bool] = {}
        # 启动后延迟预热任务句柄(防 GC 中断)
        self._warm_task: asyncio.Task | None = None

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
            # 3.2: 一并清除这些源的滑动窗口失败计数(避免手动恢复后残留计数导致快速再熔断)
            for dt in halted_set:
                reset_circuit(biz_key, dt)
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
            # ── #31: 低频档回填 ──
            # 低频小时(LOW_FREQ_HOUR)不在 DAYTIME_HOURS, _last_run_hour 不直接体现。
            # 若今天低频小时已有 scheduled 成功记录(任一低频源成功即认为本轮已尝试,
            # 因为整档一次 sync_all 提交, 部分源失败也会整档成功结束), 置 _low_freq_done
            # 避免重启后重复跑低频档。
            self._low_freq_done = {}
            if LOW_FREQ_HOUR > 0 and LOW_FREQ_HOUR in hours_done:
                self._low_freq_done[today_key] = True
                logger.info(f"[调度器] 低频档回填: 今日 {LOW_FREQ_HOUR}:00 已执行过, 重启不重复触发")

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
        # 启动后延迟预热今日看板(让首个用户命中热缓存); 与同步互斥由 warmer 内部保证,
        # 若此时启动补跑同步正在进行, warmer 会跳过, 改由同步完成后的预热兜底。
        self._warm_task = asyncio.create_task(self._startup_warm_delayed())
        logger.info(
            f"BMS 定时同步已启动, 白天小时(8-23+0)={sorted(DAYTIME_HOURS)},"
            f" 补跑={CATCHUP_HOUR}:00, 宽容窗口{TRIGGER_WINDOW_MINUTES}分钟"
        )

    async def _startup_warm_delayed(self) -> None:
        """启动后延迟预热今日看板(等迁移/调度器先稳定)"""
        try:
            await asyncio.sleep(8)
            from app.services.cache_warmer import warm_on_startup
            await asyncio.to_thread(warm_on_startup)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001  预热失败不影响服务
            logger.debug(f"启动预热失败(不影响): {e}")

    def stop(self):
        """停止调度器"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        if self._warm_task and not self._warm_task.done():
            self._warm_task.cancel()
        self._warm_task = None
        logger.info("BMS 定时同步已停止")

    async def _run_loop(self):
        """主循环:每分钟检查是否到了定时;首次检查先做启动补跑

        调度策略(2026-08-13 调整为专项级熔断):
          - 白天: DAYTIME_HOURS (8,9,...,23,0) 每小时同步;
            若某 data_type(专项)失败 → 仅暂停该专项当天后续小时; 其他专项不受影响
          - 凌晨 CATCHUP_HOUR (5:00): 对「前一天」做整体补跑,忽略所有专项熔断
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

                # ── 预警扫描(每小时最多一次, scan_alerts 内部做当日幂等) ──
                alert_done = self._alert_scanned_hours.setdefault(date_str, set())
                if now_cn.minute < TRIGGER_WINDOW_MINUTES and now_cn.hour not in alert_done:
                    alert_done.add(now_cn.hour)
                    try:
                        await self._scan_alerts_safe()
                    except Exception as ae:  # noqa: BLE001
                        logger.warning(f"[调度器] 预警扫描异常: {ae}")

                # ── 共享文档(腾讯文档)同步: 按间隔轮询, 到点且空闲时执行 ──
                try:
                    await self._maybe_sync_tencent_doc()
                except Exception as de:  # noqa: BLE001
                    logger.warning(f"[调度器] 共享文档同步检查异常: {de}")

                # ── 共享文档在线采集(doc_sync_sources): 到点源逐个同步(#33) ──
                try:
                    await self._maybe_sync_shared_docs()
                except Exception as sd:  # noqa: BLE001
                    logger.warning(f"[调度器] 共享文档在线采集检查异常: {sd}")

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

                # ── 1) 5:00 补跑: 优先"缺口清点补跑", 无缺口退回前一天全天兜底 ──
                #    (工单#2/A-20260904-002 3.3: 只补真缺口, 不重复无缺口源)
                if (
                    now_cn.hour == CATCHUP_HOUR
                    and now_cn.minute < TRIGGER_WINDOW_MINUTES
                    and CATCHUP_HOUR not in today_done
                ):
                    today_done.add(CATCHUP_HOUR)
                    # 昨天业务日
                    y_date = (now_cn.date() - timedelta(days=1)).strftime("%Y-%m-%d")
                    has_gaps = False
                    try:
                        from app.services.gap_queue_service import (
                            list_gaps,
                            settle_and_backfill_yesterday,
                        )

                        db0 = SessionLocal()
                        try:
                            open_gaps = list_gaps(db0, biz_date=y_date, status=None)
                            has_gaps = bool(open_gaps)
                        finally:
                            db0.close()
                        # 有昨天缺口记录(open/partial) → 走缺口清点补跑
                        if has_gaps:
                            logger.info(
                                f"[调度器] 5:00 补跑: {y_date} 存在缺口记录, 走缺口清点补跑(只补缺口源)"
                            )
                            gap_db = SessionLocal()
                            try:
                                r = await settle_and_backfill_yesterday(gap_db, yesterday=y_date)
                            finally:
                                gap_db.close()
                            logger.info(
                                f"[调度器] 5:00 缺口清点补跑完成: 补成功 "
                                f"{len(r.get('backfilled', []))}, 仍失败 {len(r.get('still_failed', []))}"
                            )
                        else:
                            logger.info(
                                f"[调度器] 5:00 补跑: {y_date} 无缺口记录, 退回归一前一天(整天)整体同步(忽略熔断)"
                            )
                            await self._do_scheduled_sync(
                                why=f"凌晨补跑 前一天全量 {CATCHUP_HOUR}:00",
                                yesterday_full_sync=True,
                                biz_key=biz_key,
                            )
                    except Exception as e:  # noqa: BLE001 缺口补跑异常 → 退回全天兜底
                        logger.warning(f"[调度器] 5:00 缺口清点补跑异常,退回前一天全量兜底: {e}")
                        await self._do_scheduled_sync(
                            why=f"凌晨补跑 前一天全量 {CATCHUP_HOUR}:00",
                            yesterday_full_sync=True,
                            biz_key=biz_key,
                        )

                # ── 1.5) 低频源每日定时档(A-20260907-001 / #31) ──
                # 每天 LOW_FREQ_HOUR(默认6:00) 对"DB default_sync=true − 高频4源"的低频源
                # 跑"前一天整天"。触发/顺延规则(修复 Review CRITICAL, 2026-09-07):
                #   - 触发窗口 = LOW_FREQ_HOUR(6:00) 起至 LOW_FREQ_DEADLINE_HOUR(7:00) 前,
                #     整段(6:00~6:59)内**空闲**即执行并记 done;
                #   - 窗口内 busy(5:00 缺口清点/前一天全量未结束/手动任务占锁)→ **不记 done**,
                #     下一分钟 tick 再试(真正的忙顺延, 不再静默丢档);
                #   - 到达 LOW_FREQ_DEADLINE_HOUR(7:00)→ 当日放弃(让位 8:00 全量),
                #     记 done + 告警, 靠低频源失败进 gap → 次日 5:00 缺口清点兜底。
                if (
                    LOW_FREQ_HOUR > 0
                    and not self._low_freq_done.get(date_str, False)
                ):
                    if should_run_low_freq(
                        now_cn.hour,
                        done=self._low_freq_done.get(date_str, False),
                        busy=sync_task_manager.is_busy(),
                    ):
                        # 空闲 → 置 done(防本日重复)后提交; 提交前已确认空闲,
                        # 不会出现"置 done 但 _do_scheduled_sync 因 busy return 丢档"
                        self._low_freq_done[date_str] = True
                        # 低频集合非空才提交(读库 helper)
                        _lf_db = SessionLocal()
                        try:
                            from app.services.sync_source_config_service import (
                                list_scheduled_bms_sources,
                            )

                            _lf_set = list_scheduled_bms_sources(_lf_db)
                            _low_types = _lf_set.get("low") or []
                        except Exception as _lfe:  # noqa: BLE001
                            logger.warning(f"[调度器] 低频档读库失败, 跳过本轮: {_lfe}")
                            _low_types = []
                        finally:
                            _lf_db.close()
                        if _low_types:
                            logger.info(
                                f"[调度器] 低频档触发 {date_str} {now_cn.hour:02d}:{now_cn.minute:02d},"
                                f" 低频源={_low_types}(前一天整天, 每天1次)"
                            )
                            await self._do_scheduled_sync(
                                why=f"低频源每日档 {now_cn.hour:02d}:00",
                                yesterday_full_sync=True,
                                biz_key=biz_key,
                                source_tier="low",
                            )
                        else:
                            logger.info(
                                f"[调度器] 低频档 {now_cn.hour:02d}:00 无低频源(DB default_sync 未开启), 跳过"
                            )
                    elif now_cn.hour >= LOW_FREQ_DEADLINE_HOUR:
                        # 已过顺延截止, 当日放弃(不再反复尝试); 让位 8:00 全量
                        self._low_freq_done[date_str] = True
                        logger.warning(
                            f"[调度器] 低频档 {LOW_FREQ_HOUR}:00 因持续忙碌错过(>={LOW_FREQ_DEADLINE_HOUR}:00),"
                            f" 当日放弃; 依赖低频源失败进 gap → 次日 5:00 缺口清点兜底"
                        )

                # ── 2) 白天每小时同步(8~23, 0) ──
                if now_cn.hour in DAYTIME_HOURS and now_cn.minute < TRIGGER_WINDOW_MINUTES and now_cn.hour not in today_done:
                    today_done.add(now_cn.hour)
                    # 快速预检: 当前有资格同步的数据源 = (BMS 高频 + SCRM 有凭证) ∩ 未熔断
                    # #31: BMS 高频候选改为读 DB default_sync=true 的 high 档(原代码常量已不适用)
                    _hh_db = SessionLocal()
                    try:
                        from app.services.sync_source_config_service import (
                            list_scheduled_bms_sources,
                        )

                        _bms_high = (list_scheduled_bms_sources(_hh_db).get("high") or [])
                    except Exception:  # noqa: BLE001 读库失败降级代码常量
                        _bms_high = list(DEFAULT_SYNC_TYPES)
                    finally:
                        _hh_db.close()
                    bms_candidate = _bms_high if self._default_sources_have_credentials() else []
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
                            full_window_days=FULL_WINDOW_DAYS if now_cn.hour == 8 else 0,
                            biz_key=biz_key,
                        )

                # ── 半开探测(3.2): 熔断源每 HALF_OPEN_INTERVAL 放一次单片试探,
                #    成功即摘除熔断自动续采; 失败保持熔断等下一探测窗口 ──
                try:
                    await self._maybe_half_open_probe(biz_key, now_cn)
                except Exception as pe:  # noqa: BLE001
                    logger.warning(f"[调度器] 半开探测异常: {pe}")

                # ── 空闲期缓存预热(白天每分钟自检; warmer 内部按 ttl 节流+同步互斥,
                #    仍新鲜/夜间/同步中均快速跳过, 仅缓存将过期时才重算) ──
                try:
                    from app.services.cache_warmer import warm_idle
                    await asyncio.to_thread(warm_idle)
                except Exception as we:  # noqa: BLE001
                    logger.debug(f"空闲预热失败(不影响): {we}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"调度器异常: {e}", exc_info=True)

            await asyncio.sleep(CHECK_INTERVAL)

    async def _maybe_half_open_probe(self, biz_key: str, now_cn) -> None:
        """半开探测(A-20260904-002 / 工单#2 3.2): 熔断源周期性单片试探。

        - 触发条件: 白天(DAYTIME_HOURS)非整点强制窗口、当前无任务忙、
          源已熔断(_halted_types[biz_key])、且距上次探测 >= HALF_OPEN_INTERVAL。
        - 探测方式: 仅对该源做「当前小时窗口」单源同步(近 1h 短窗, 轻量试探)。
        - 成功 → 摘除熔断(_halted_types 移除) + 清零失败计数(reset_circuit),
                该源立即恢复, 后续整点正常调度自动续采。
        - 失败 → 保持熔断 + mark_probed(记探测时间), 等下一探测窗口再试。
        - 半开探测失败**不累加熔断计数**(避免探测本身把已熔断源推向更深故障态)。
        """
        halted_set = set(self._halted_types.get(biz_key) or set())
        if not halted_set:
            return
        # 仅白天增量时段探测(5:00/8:00 兜底本就忽略熔断强制全跑, 无需半开)
        if now_cn.hour not in DAYTIME_HOURS:
            return
        if sync_task_manager.is_busy():
            return  # 有任务在跑, 下一分钟再探
        # 5/8 点整点前 30 分钟窗口内也跳过(避免与兜底/全量撞车)
        if now_cn.minute < TRIGGER_WINDOW_MINUTES and now_cn.hour in (CATCHUP_HOUR, 8):
            return

        # 归一化到北京时间(now_cn 已带 tz)
        for dt in sorted(halted_set):
            if not should_probe(biz_key, dt):
                continue
            mark_probed(biz_key, dt)  # 先记账, 防止异常重入导致高频探测
            logger.info(f"[调度器] 半开探测: {biz_key}/{dt} 熔断中, 放单片试探(近1h窗口)")
            try:
                ok = await self._half_open_probe_once(biz_key, dt, now_cn)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[调度器] 半开探测异常({dt}): {e}")
                ok = False
            if ok:
                # 探测成功 → 摘除熔断, 该源恢复调度
                self._halted_types.setdefault(biz_key, set()).discard(dt)
                clear_halt_recovery(biz_key, dt)
                logger.info(f"[调度器] 半开探测成功: {biz_key}/{dt} 上游已恢复, 摘除熔断, 恢复自动采集")
            else:
                logger.warning(f"[调度器] 半开探测失败: {biz_key}/{dt} 仍不可用, 保持熔断(下一窗口再试)")
            return  # 每分钟最多探测一个源(避免多个源同时探测互相干扰)

    async def _half_open_probe_once(self, biz_key: str, dt: str, now_cn) -> bool:
        """对单个熔断源做近 1h 短窗同步试探, 成功返回 True。

        复用 sync_all / sync_scrm_all 按 data_type 分流, 经 SyncTaskManager
        全局互斥提交(与定时/手动任务并发隔离)。
        """
        probe_start = now_cn - timedelta(hours=1)
        probe_end = now_cn

        # #31: BMS 已知名单改为读 DB default_sync=true 全集合(高频+低频),
        # 使任何纳入定时的源熔断后都能被半开探测恢复(原仅种子4源)。
        _hk_db = SessionLocal()
        try:
            from app.services.sync_source_config_service import list_scheduled_bms_sources

            bms_known = set((list_scheduled_bms_sources(_hk_db).get("all") or []))
        except Exception:  # noqa: BLE001
            bms_known = set(DEFAULT_SYNC_TYPES)
        finally:
            _hk_db.close()
        scrm_known = {
            DATA_TYPE_OPERATOR, DATA_TYPE_SESSION_GROUP,
            DATA_TYPE_SESSION_PRIVATE, DATA_TYPE_SESSION_VIRTUAL,
        }
        if dt in bms_known:
            async def probe_runner(task_id: str, on_progress_cb):
                try:
                    res = await sync_all(
                        trigger_type="half_open",  # 独立类型, 避免污染 scheduled 整点回填
                        triggered_by="scheduler-half-open",
                        on_progress=on_progress_cb,
                        data_types=[dt],
                        time_ranges={dt: (probe_start, probe_end)},
                    )
                    return res if isinstance(res, list) else []
                except Exception as e:  # noqa: BLE001
                    return [{"data_type": dt, "success": False, "error": str(e)}]
        elif dt in scrm_known:
            async def probe_runner(task_id: str, on_progress_cb):
                try:
                    res = await sync_scrm_all(
                        trigger_type="half_open",  # 独立类型, 避免污染 scheduled 整点回填
                        triggered_by="scheduler-half-open",
                        on_progress=on_progress_cb,
                        sync_types=[dt],
                    )
                    return res if isinstance(res, list) else []
                except Exception as e:  # noqa: BLE001
                    return [{"data_type": dt, "success": False, "error": str(e)}]
        else:
            logger.warning(f"[调度器] 半开探测: 未知 data_type={dt}, 无法定向试探")
            return False

        display = {dt: ("半开探测(近1h)", probe_end.strftime("%m-%d %H:%M:%S"))}
        try:
            tid, msg = await sync_task_manager.submit_multi(
                trigger_type="half_open",
                triggered_by="scheduler-half-open",
                data_types=[dt],
                time_ranges={dt: (probe_start, probe_end)},
                time_ranges_display=display,
                runners=[probe_runner],
                ignore_cooldown=True,
            )
        except RuntimeError as e:
            logger.warning(f"[调度器] 半开探测未执行({dt}): {e}")
            return False
        if not tid:
            return False

        # 等待结果(短窗同步, 最多 10 分钟)
        poll = 0
        while poll < 40:  # 40 * 15s = 10min
            await asyncio.sleep(15)
            poll += 1
            t = sync_task_manager.get_task(tid)
            if t and t.status in ("success", "failed", "partial"):
                break
        else:
            logger.warning(f"[调度器] 半开探测等待超时({dt}), 视为失败")
            return False
        results = t.results or []
        ok = any(
            isinstance(r, dict) and r.get("success") and r.get("data_type") == dt
            for r in results
        )
        return ok

    async def _scan_alerts_safe(self):
        """定时触发预警扫描(规划设计 §8.1)。
        复用 alerts.py 的 scan_alerts 逻辑, 在独立 session 中执行,
        失败不影响后续同步任务。当日幂等由 scan_alerts 内部保证。
        """
        from app.api.v1.alerts import scan_alerts

        db = SessionLocal()
        try:
            # scan_alerts 不依赖 ManagerUser(脚本调用时跳过权限校验)
            # 直接调用其函数体(传 None 当 user), 函数本身只读不依赖 current_user
            result = scan_alerts(db=db, _=None)
            if result.data and result.data.get("created"):
                logger.info(
                    f"[调度器] 预警扫描完成: 生成 {result.data['created']} 条预警"
                    f" ({result.data.get('details', [])})"
                )
        finally:
            db.close()

    async def _maybe_sync_tencent_doc(self):
        """共享文档(腾讯文档)定时轮询同步。

        每次主循环检查: 配置启用 + 已授权 + 到轮询间隔 + 无其他同步任务在跑
        → 在线程池执行(同步 DB 操作不阻塞事件循环)。
        """
        from app.services.doc_sync_service import should_sync_tencent_doc, sync_tencent_doc_safe

        if sync_task_manager.is_busy():
            return
        if not should_sync_tencent_doc():
            return
        logger.info("[调度器] 腾讯文档到点, 开始轮询同步投诉数据")
        try:
            await asyncio.to_thread(sync_tencent_doc_safe, "scheduled")
        except Exception as e:  # noqa: BLE001
            logger.error(f"[调度器] 腾讯文档轮询同步异常: {e}", exc_info=True)

    async def _maybe_sync_shared_docs(self):
        """共享文档在线采集(doc_sync_sources)定时轮询同步(#33)。

        每次主循环检查: 空闲 + 有到点源(enabled 且距上次>=interval)
        → 逐个在线程池执行(sync_doc_source_safe 内部独立 session + 状态写回,
        不阻塞事件循环; 单源失败不影响其它源)。
        """
        from app.services.shared_doc_sync_service import due_doc_sources, sync_doc_source_safe

        if sync_task_manager.is_busy():
            return
        db = SessionLocal()
        try:
            due = due_doc_sources(db)
        finally:
            db.close()
        if not due:
            return
        logger.info(f"[调度器] 共享文档在线采集到点 {len(due)} 个源, 开始轮询")
        for row in due:
            try:
                await asyncio.to_thread(sync_doc_source_safe, row.id, "scheduled")
            except Exception as e:  # noqa: BLE001
                logger.error(f"[调度器] 共享文档源 #{row.id} 轮询同步异常: {e}", exc_info=True)

    async def _do_scheduled_sync(
        self,
        why: str | None = None,
        yesterday_full_sync: bool = False,
        full_window_days: int = 0,
        biz_key: str | None = None,
        source_tier: str = "high",
    ):
        """执行定时同步 - 通过 SyncTaskManager 提交(全局互斥)

        Args:
            why: 人类可读的触发原因(打日志用)
            yesterday_full_sync: True=凌晨 5 点补跑模式,时间范围锁定「前一天整天」,
                                 忽略专项熔断,失败也不写入熔断;
                                 False=白天正常同步,时间「今天 0 点~now」,
                                 执行前先跳过 biz_key 对应日期里已熔断的专项,
                                 完成后逐个失败 data_type 写入熔断集合
            full_window_days: >0 时(早上 8 点全量兜底),时间范围 = 近 N 天 0 点~now,
                              忽略专项熔断,失败不写入熔断(与 5 点补跑同性质,
                              用于回填 BMS 分批滞后 1~2 天的补全数据);
                              0 时按 yesterday_full_sync/白天逻辑
            biz_key: 业务日期字符串(YYYY-MM-DD),专项熔断集合归属的日期;
                     None 时按 now 即时推导
            source_tier: 本轮要跑的 BMS 源档位(A-20260907-001 LOCKED / #31):
                     "high"=高频档(DB true ∩ 种子核心源, 白天/5点/8点)
                     "low" =低频档(DB true − 高频, 每天 1 次, 低频小时触发)
                     "all" =DB true 全集合(强制兜底用)
                     源集合一律读 DB sync_source_configs.default_sync(=UI「纳入周期采集」),
                     不再用代码常量 DEFAULT_SYNC_TYPES 决定"哪些源定时"。

        同一时间窗口内并发跑: BMS sync_all 与 SCRM sync_scrm_all 各自 runner,
        经 asyncio.gather 并发执行(冷却期间穿插其他数据源)。
        """
        reason = f" [{why}]" if why else ""
        TZ_CN = timezone(timedelta(hours=8))
        now_cn = datetime.now(TZ_CN)
        if biz_key is None:
            biz_date = now_cn.date() if now_cn.hour >= 4 else (now_cn - timedelta(days=1)).date()
            biz_key = biz_date.strftime("%Y-%m-%d")
        halted_set = set(self._halted_types.get(biz_key) or set())  # copy,避免中途被改
        # 兜底模式(5 点补跑 / 8 点全量): 忽略专项熔断,失败也不写入熔断
        force_all = yesterday_full_sync or full_window_days > 0

        # ── 时间范围: 5点补跑=昨天整天; 8点全量=近N天0点~now; 白天=今天 0 点~now; 低频=昨天整天 ──
        if yesterday_full_sync:
            yesterday_date = now_cn.date() - timedelta(days=1)
            range_start = datetime(yesterday_date.year, yesterday_date.month, yesterday_date.day,
                                   0, 0, 0, tzinfo=TZ_CN)
            range_end = datetime(now_cn.year, now_cn.month, now_cn.day,
                                 0, 0, 0, tzinfo=TZ_CN)
            start_disp = range_start.strftime("%m-%d 00:00(前一天全量)")
        elif full_window_days > 0:
            window_date = now_cn.date() - timedelta(days=full_window_days)
            range_start = datetime(window_date.year, window_date.month, window_date.day,
                                   0, 0, 0, tzinfo=TZ_CN)
            range_end = now_cn
            start_disp = range_start.strftime("%m-%d 00:00") + f"(近{full_window_days}天全量兜底)"
        else:
            range_start = now_cn.replace(hour=0, minute=0, second=0, microsecond=0)
            range_end = now_cn
            start_disp = range_start.strftime("%m-%d 00:00(今日定时-全量)")

        # ── 源集合: 读 DB default_sync=true(UI「纳入周期采集」), 按 source_tier 分档 ──
        # A-20260907-001 LOCKED / #31: 原用代码常量 DEFAULT_SYNC_TYPES(仅种子4源),
        # 不读 DB → UI 开关(改 DB default_sync)不生效。现改为统一读库分档:
        #   high = DB true ∩ 种子核心源(白天每小时/5点/8点)
        #   low  = DB true − 高频(低频档每天1次)
        #   all  = DB true 全集合
        # 注: T_PLUS_ONE_BMS_TYPES(退款记录报表 E 路)特殊逻辑已退役——它 DB true 时
        #     自动归入 low 档(低频每天抓前一天 = 天然 T+1), DB false 时不进任何定时。
        _sched_set: dict[str, list[str]] = {"high": [], "low": [], "all": []}
        _db0 = SessionLocal()
        try:
            from app.services.sync_source_config_service import list_scheduled_bms_sources

            _sched_set = list_scheduled_bms_sources(_db0)
        except Exception as _se:  # noqa: BLE001 读库失败降级为代码常量, 不阻塞调度
            logger.warning(f"[定时同步{reason}] 读库源集合失败, 降级用代码常量: {_se}")
            _sched_set = {
                "high": list(DEFAULT_SYNC_TYPES),
                "low": [],
                "all": list(DEFAULT_SYNC_TYPES),
            }
        finally:
            _db0.close()

        _tier = source_tier if source_tier in _sched_set else "high"
        _tier_types = list(_sched_set[_tier])
        # 凭证预检: high/all 档要求高频核心源凭证齐(沿用 _default_sources_have_credentials);
        # low 档不做全局凭证预检——但注意 sync_all 只 skip enabled=False 的源, 不 skip 无凭证源;
        # 故 DB true 但未配凭证/登录态的低频源(如 requires_captcha 且无 storage_state 的闪猫)
        # 会真实失败并记入 gap。按架构灰度 G3 管控: 上线初期应将这类源临时 default_sync=false,
        # 待验证 storage_state 可用后再纳入(见 A-20260907-001 §九)。
        if _tier == "low":
            has_bms = bool(_tier_types)
        else:
            has_bms = bool(_tier_types) and self._default_sources_have_credentials()
        has_scrm = self._scrm_sources_have_credentials()

        sync_types_bms_raw = _tier_types if has_bms else []
        logger.info(
            f"[定时同步{reason}] BMS 源集合 source_tier={_tier}: {sync_types_bms_raw}"
            f" (来自 DB default_sync=true, {len(sync_types_bms_raw)} 源)"
        )
        sync_types_scrm_raw = [
            DATA_TYPE_OPERATOR, DATA_TYPE_SESSION_PRIVATE,
            DATA_TYPE_SESSION_GROUP, DATA_TYPE_SESSION_VIRTUAL,
        ] if has_scrm else []
        if force_all:
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
            f" mode={'前一天全量(忽略熔断)' if yesterday_full_sync else (f'近{full_window_days}天全量兜底(忽略熔断)' if full_window_days > 0 else '白天增量(专项级过滤)')})"
        )

        # ── 并发子任务: BMS 与 SCRM 各自独立 runner, 经 asyncio.gather 并发执行 ──
        # 目的: BMS 内部同域 1 分钟下载间隔冷却 sleep 期间, SCRM(不同域)可继续抓取,
        #       实现「冷却期间穿插其他数据源」, 缩短整体同步总耗时。
        runners: list = []
        if sync_types_bms:
            async def bms_runner(task_id: str, on_progress_cb):
                logger.info(f"[定时同步{reason}] ▶▶▶ BMS 子任务开始: data_types={sync_types_bms}")
                try:
                    bms_res = await sync_all(
                        trigger_type="scheduled",
                        triggered_by="scheduler",
                        on_progress=on_progress_cb,
                        data_types=sync_types_bms,
                        time_ranges=time_ranges_bms_dt,
                    )
                    res = bms_res if isinstance(bms_res, list) else []
                    ok_n = sum(1 for r in res if isinstance(r, dict) and r.get("success"))
                    logger.info(
                        f"[定时同步{reason}] ◀◀◀ BMS 子任务完成: "
                        f"{ok_n}/{len(sync_types_bms)} 成功"
                    )
                    return res
                except Exception as e:  # noqa: BLE001
                    logger.exception(f"[定时同步{reason}] ◀◀◀ BMS 子任务异常(全标失败): {e}")
                    return [{"data_type": dt, "success": False, "error": str(e)} for dt in sync_types_bms]
            runners.append(bms_runner)

        if sync_types_scrm:
            async def scrm_runner(task_id: str, on_progress_cb):
                logger.info(f"[定时同步{reason}] ▶▶▶ SCRM 子任务开始: data_types={sync_types_scrm}")
                try:
                    scrm_res = await sync_scrm_all(
                        trigger_type="scheduled",
                        triggered_by="scheduler",
                        on_progress=on_progress_cb,
                        sync_types=sync_types_scrm,
                    )
                    res = scrm_res if isinstance(scrm_res, list) else []
                    ok_n = sum(1 for r in res if isinstance(r, dict) and r.get("success"))
                    logger.info(
                        f"[定时同步{reason}] ◀◀◀ SCRM 子任务完成: "
                        f"{ok_n}/{len(sync_types_scrm)} 成功"
                    )
                    return res
                except Exception as e:  # noqa: BLE001
                    logger.exception(f"[定时同步{reason}] ◀◀◀ SCRM 子任务异常(全标失败): {e}")
                    return [{"data_type": dt, "success": False, "error": str(e)} for dt in sync_types_scrm]
            runners.append(scrm_runner)

        task_id: str | None = None
        try:
            task_id, msg = await sync_task_manager.submit_multi(
                trigger_type="scheduled",
                triggered_by="scheduler",
                data_types=all_types,
                time_ranges={**time_ranges_bms_dt},
                time_ranges_display=time_ranges_display,
                runners=runners,
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
            # 超时拿不到结果 → 保守把本轮 all_types 全部视为失败(仅白天增量模式写熔断)
            logger.warning(f"[定时同步{reason}] 等待任务完成超时(task_id={task_id})")
            if not force_all:
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

        # ── 专项级熔断(3.2 滑动窗口计数): 仅白天增量模式 ──
        # 改造(A-20260904-002 / 工单#2): 不再"单轮重试后仍失败即熔断一整天"。
        # 改为: 成功源清零计数(reset_circuit);
        #       失败源 record_failure 在 6h 滑动窗口内累计, 连续失败 >= 阈值(默认3)
        #       才真正写入 _halted_types(熔断)。偶发单次失败不熔断。
        if not force_all:
            # 1) 成功源: 清零连续失败计数(连续失败语义 = 成功即打断累计)
            all_done_types = {
                r.get("data_type") for r in task_results
                if isinstance(r, dict) and r.get("success") and r.get("data_type")
            }
            for dt in all_done_types:
                reset_circuit(biz_key, dt)

            # 2) 失败源: 单次重试(保留既有 P1 行为), 仍失败才计入熔断滑动窗口
            if failed_types:
                retried_failed = await self._retry_failed_types_once(
                    failed_types,
                    range_start=range_start,
                    range_end=range_end,
                    start_disp=start_disp,
                    reason=reason,
                )
                recovered = sorted(set(failed_types) - set(retried_failed))
                if recovered:
                    logger.info(f"[定时同步{reason}] 专项重试恢复: {recovered}")
                    for dt in recovered:
                        reset_circuit(biz_key, dt)
                if retried_failed != failed_types:
                    logger.info(f"[定时同步{reason}] 专项重试后仍失败: {sorted(retried_failed)}")
                failed_types = retried_failed

            # 3) 滑动窗口熔断判定: 连续失败达阈值才熔断(未达阈值只计数, 下次仍尝试)
            s = self._halted_types.setdefault(biz_key, set())
            newly_halted: list[str] = []
            for dt in failed_types:
                if dt in s:
                    continue  # 已熔断
                if record_failure(biz_key, dt):
                    s.add(dt)
                    newly_halted.append(dt)
                    try:
                        self._register_circuit_alert(biz_key, dt)
                    except Exception as ae:  # noqa: BLE001 告警失败不影响熔断
                        logger.warning(f"[调度器] 熔断告警登记失败(忽略): {ae}")
                else:
                    logger.warning(
                        f"[调度器] {biz_key}/{dt} 本轮失败(单次重试后仍失败),"
                        f" 未达熔断阈值, 计数累计待下次判定"
                    )
            if newly_halted:
                logger.error(
                    f"[调度器] 滑动窗口熔断: biz_date={biz_key},"
                    f" 本轮新增熔断={newly_halted}(连续失败达阈值),"
                    f" 累计已熔断专项={sorted(s)}"
                )

        # ── 缺口记账(工单#2/A-20260904-002 3.3) ──
        # 失败/部分成功 → gap_records(open/partial), 供次日 5:00 清点补跑。
        # force_all(5:00 前一天全天 / 8:00 近N天全量兜底)成功 → 打 full_day=True 标 filled;
        # 8:00 近N天窗口额外传 full_range, 把窗口内该源历史 open/partial 一并标 filled
        # (避免 8:00 全量已覆盖后, 缺口队列仍把窗口内旧缺口当"待补"重复补跑)。
        # 独立 session, 记账失败不影响调度主流程。
        if task_results is not None and task_results:
            try:
                from app.services.gap_queue_service import record_from_sync_results

                full_range: tuple[str, str] | None = None
                if force_all:
                    for r in task_results:
                        if isinstance(r, dict) and r.get("success"):
                            r["full_day"] = True
                    if full_window_days > 0:
                        # 近 N 天窗口: [range_start 日期, range_end 日期](UTC → 北京日期)
                        try:
                            if range_start.tzinfo is None:
                                rs_cn = range_start.replace(tzinfo=timezone.utc).astimezone(TZ_CN)
                            else:
                                rs_cn = range_start.astimezone(TZ_CN)
                            if range_end.tzinfo is None:
                                re_cn = range_end.replace(tzinfo=timezone.utc).astimezone(TZ_CN)
                            else:
                                re_cn = range_end.astimezone(TZ_CN)
                            full_range = (rs_cn.strftime("%Y-%m-%d"), re_cn.strftime("%Y-%m-%d"))
                        except Exception:  # noqa: BLE001
                            full_range = None
                gap_db = SessionLocal()
                try:
                    n = record_from_sync_results(gap_db, task_results, trigger_type="scheduled", full_range=full_range)
                    if n:
                        logger.info(f"[调度器] 缺口记账: {n} 条更新(gap_records)")
                finally:
                    gap_db.close()
            except Exception as gap_err:  # noqa: BLE001
                logger.warning(f"[调度器] 缺口记账失败(忽略): {gap_err}")

    def _register_circuit_alert(self, biz_key: str, data_type: str) -> None:
        """熔断触发 → 写一条事务通知(站内信), 事件留痕(3.2)。

        复用 notifier 通知管理员/主管; 不做 Incident 自动登记(熔断本身会
        被次日 5:00 补跑/8:00 兜底/半开探测自动恢复, 属"已知告警"而非事故)。
        若需要正式 Incident 台账, 可后续接 _auto_register_ingest_incident。
        """
        try:
            from app.services.notifier import notify_all_admins_and_supervisors
            db = SessionLocal()
            try:
                notify_all_admins_and_supervisors(
                    db,
                    type="alert",
                    title=f"采集熔断: {data_type} ({biz_key})",
                    content=(
                        f"数据源 {data_type} 在业务日 {biz_key} 连续失败达阈值被熔断暂停。\n"
                        f"处置: 熔断后每 {settings.CIRCUIT_HALF_OPEN_INTERVAL_SECONDS // 3600}h "
                        f"自动半开探测, 上游恢复即自动续采; 也可手动触发同步恢复。"
                    ),
                    ref_type="alert",
                    ref_id=data_type,
                )
            finally:
                db.close()
        except Exception as e:  # noqa: BLE001 通知失败不影响熔断主流程
            logger.warning(f"[调度器] 熔断通知失败(忽略): {e}")


    async def _retry_failed_types_once(
        self,
        failed_types: list[str],
        *,
        range_start,
        range_end,
        start_disp: str,
        reason: str,
    ) -> list[str]:
        """P1(2026-08-30 审计): 白天增量模式下, 对本次失败专项**单次重试**一次,
        仍失败才交由调用方写入熔断。

        复用同一轮的时间窗口, 仅针对失败专项, 并经 SyncTaskManager 全局互斥
        提交(与定时/手动任务并发隔离, 避免重复导入)。适用于偶发超时/限流——
        一次重试即可恢复, 而不必把该专项熔断一整天。

        Returns: 重试后仍失败的 data_type 列表(含未重试的未知类型原样保留)。
        """
        if not failed_types:
            return []

        # #31: BMS 已知名单读 DB default_sync=true 全集合(高频+低频), 与半开探测一致
        _rk_db = SessionLocal()
        try:
            from app.services.sync_source_config_service import list_scheduled_bms_sources

            _bms_known = set((list_scheduled_bms_sources(_rk_db).get("all") or []))
        except Exception:  # noqa: BLE001
            _bms_known = set(DEFAULT_SYNC_TYPES)
        finally:
            _rk_db.close()
        bms_types = [dt for dt in failed_types if dt in _bms_known]
        scrm_known = {
            DATA_TYPE_OPERATOR, DATA_TYPE_SESSION_PRIVATE,
            DATA_TYPE_SESSION_GROUP, DATA_TYPE_SESSION_VIRTUAL,
        }
        scrm_types = [dt for dt in failed_types if dt in scrm_known]
        # 未知类型无法定向重试, 原样保留为失败
        unknown = [dt for dt in failed_types if dt not in _bms_known and dt not in scrm_known]
        retry_types = bms_types + scrm_types
        if not retry_types:
            return failed_types

        if sync_task_manager.is_busy():
            logger.warning(f"[定时同步{reason}] 专项重试跳过(任务忙): {retry_types}")
            return failed_types

        runners: list = []
        time_ranges_bms_retry = {dt: (range_start, range_end) for dt in bms_types}
        if bms_types:
            async def bms_retry_runner(task_id: str, on_progress_cb):
                try:
                    res = await sync_all(
                        trigger_type="scheduled",
                        triggered_by="scheduler-retry",
                        on_progress=on_progress_cb,
                        data_types=bms_types,
                        time_ranges=time_ranges_bms_retry,
                    )
                    return res if isinstance(res, list) else []
                except Exception as e:  # noqa: BLE001
                    return [{"data_type": dt, "success": False, "error": str(e)} for dt in bms_types]
            runners.append(bms_retry_runner)

        if scrm_types:
            async def scrm_retry_runner(task_id: str, on_progress_cb):
                try:
                    res = await sync_scrm_all(
                        trigger_type="scheduled",
                        triggered_by="scheduler-retry",
                        on_progress=on_progress_cb,
                        sync_types=scrm_types,
                    )
                    return res if isinstance(res, list) else []
                except Exception as e:  # noqa: BLE001
                    return [{"data_type": dt, "success": False, "error": str(e)} for dt in scrm_types]
            runners.append(scrm_retry_runner)

        retry_display = {}
        for dt in bms_types:
            retry_display[dt] = (start_disp, range_end.strftime("%m-%d %H:%M:%S"))
        for dt in scrm_types:
            retry_display[dt] = ("专项重试(按last_success)", range_end.strftime("%m-%d %H:%M:%S"))

        try:
            tid, msg = await sync_task_manager.submit_multi(
                trigger_type="scheduled",
                triggered_by="scheduler-retry",
                data_types=retry_types,
                time_ranges=time_ranges_bms_retry,
                time_ranges_display=retry_display,
                runners=runners,
                ignore_cooldown=True,
            )
            logger.info(f"[定时同步{reason}] 已提交专项重试: task_id={tid}, {msg}")
        except RuntimeError as e:
            logger.warning(f"[定时同步{reason}] 专项重试未执行: {e}")
            return failed_types

        if not tid:
            return failed_types

        poll = 0
        results: list[dict] | None = None
        while poll < 60 * 60 * 4:  # 最多 4 小时兜底
            await asyncio.sleep(15)
            poll += 1
            t = sync_task_manager.get_task(tid)
            if t and t.status in ("success", "failed", "partial"):
                results = t.results
                break
        if results is None:
            logger.warning(f"[定时同步{reason}] 专项重试等待超时(task_id={tid}), 仍按失败处理")
            still_failed = retry_types
        else:
            still_failed = [
                r.get("data_type") for r in results
                if isinstance(r, dict) and not r.get("success") and r.get("data_type")
            ]
        return sorted(set(still_failed) | set(unknown))


# 全局单例
bms_scheduler = BmsScheduler()

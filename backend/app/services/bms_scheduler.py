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
from app.models.scheduler_settings import (
    DEFAULT_END_HOUR,
    DEFAULT_INTERVAL_MINUTES,
    DEFAULT_START_HOUR,
)
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
# ⚠️ 高频档已改为「DB 可配的时段 + 间隔」(#51 / A-20260910-006 LOCKED)。
#   运行时以 scheduler_settings 表为准(见 scheduler_settings_service.get_cached_slots);
#   下方 DEFAULT_DAYTIME_HOURS / DEFAULT_INTERVAL_MINUTES 仅作**缺省值来源与回归基线**,
#   等价关系: start=8/end=24/interval=60 → 与旧 {8..23} 完全一致(有单测锁定)。
DEFAULT_HIGH_FREQ_START_HOUR: int = DEFAULT_START_HOUR
DEFAULT_HIGH_FREQ_END_HOUR: int = DEFAULT_END_HOUR
DEFAULT_HIGH_FREQ_INTERVAL_MINUTES: int = DEFAULT_INTERVAL_MINUTES

# 旧常量保留: 供「旧行为等价性」回归测试与兼容读取(不再作为调度唯一依据)
DEFAULT_DAYTIME_HOURS: frozenset[int] = frozenset([*range(8, 24), 0])
# ⚠️ DEPRECATED: 仅兼容既有引用(如 cache_warmer / 测试)。新代码请用 compute_slots()。
DAYTIME_HOURS: frozenset[int] = DEFAULT_DAYTIME_HOURS

# 凌晨 5 点: 对前一天做整体同步(2026-08-31 由 3:00 调整, 避开 BMS 凌晨维护时段)
CATCHUP_HOUR: int = 5
# 早上 8:00 全量兜底同步窗口(天): BMS 数据按登记时间分批滞后 1~2 天,
# 8 点用近 N 天窗口主动拉一次, 确保补全数据回填(与 BMS 导出近 7 天滚动对齐)
FULL_WINDOW_DAYS: int = 7
# 低频源每日定时档(时)(A-20260907-001 LOCKED / #31): 每天该整点对「DB default_sync=true
# 且非高频 4 源」的大表/报表源(device_combo/bms_order/退货/设备标签/退款记录报表/闪猫等)
# 跑"前一天整天"。默认 6:00(避开 5:00 缺口清点与 8:00 全量); settings.LOW_FREQ_HOUR<=0 禁用。
LOW_FREQ_HOUR: int = max(0, int(getattr(settings, "LOW_FREQ_HOUR", 6) or 0))
# 低频档忙碌顺延结束小时: 6:00 启动后若一直忙, 直到该小时前必须尝试启动(超过则当日放弃让位 8:00)
LOW_FREQ_DEADLINE_HOUR: int = 7

# ── 检查间隔(秒) ──
CHECK_INTERVAL = 60

# 槽位触发宽容窗口(分钟): 槽位时刻起该分钟内命中都可触发。
# ⚠️ 实际窗口 = min(TRIGGER_WINDOW_MINUTES, interval_minutes) —— 见 slot_trigger_window()。
#   若窗口 >= interval, 一个槽位的窗口会覆盖下一槽位, 造成抖动/漏槽(架构 §2.3 硬约束)。
TRIGGER_WINDOW_MINUTES = 30


def slot_trigger_window(interval_minutes: int) -> int:
    """槽位触发宽容窗口(分钟) = min(TRIGGER_WINDOW_MINUTES, interval)。

    纯函数, 供单测锁定「窗口不超过间隔」这条硬约束。
    """
    if interval_minutes <= 0:
        return 0
    return min(TRIGGER_WINDOW_MINUTES, interval_minutes)


def slot_of(minute_of_day: int, interval_minutes: int) -> str | None:
    """把一个「当日分钟数」向下取整到所属槽位标识(纯函数)。

    用于重启后从 bms_sync_logs 的 finished_at 回填已完成槽位:
        485 (08:05), interval=30 → "08:00"
        510 (08:30)              → "08:30"
        511 (08:31)              → "08:30"
    interval<=0 → None(不参与槽位体系)。
    """
    if interval_minutes <= 0:
        return None
    snapped = (minute_of_day // interval_minutes) * interval_minutes
    hh, mm = divmod(snapped, 60)
    if hh >= 24:
        return None
    return f"{hh:02d}:{mm:02d}"


def current_slot(now_hour: int, now_minute: int, interval_minutes: int,
                 slots: list[str]) -> str | None:
    """当前时刻命中的槽位标识(纯函数); 未命中任何槽位窗口 → None。

    命中条件(架构 §2.3): 槽位时刻 <= now < 槽位时刻 + min(30, interval)
    即窗口从槽位起算, 向下不越到前一槽、向上不盖住下一槽。
    """
    if not slots or interval_minutes <= 0:
        return None
    win = slot_trigger_window(interval_minutes)
    now_min = now_hour * 60 + now_minute
    # 槽位按固定间隔切分, 可直接算: 距时段起点的偏移落哪个格
    for slot in slots:
        sh, sm = (int(x) for x in slot.split(":"))
        s_min = sh * 60 + sm
        if s_min <= now_min < s_min + win:
            return slot
    return None


def low_freq_done_from_logs(rows, sched_map: dict, *, today_key: str, today_cn,
                            default_hour: int, tz_cn) -> dict[str, bool]:
    """#191 纯函数: 由"低频源自身的 scheduled 成功记录"推导已完成的小时档。

    ⚠️ 为什么不用高频槽位(slots_done)回填: 低频小时可按源配置, 若落在高频时段内
    (如 E 路 15:00 ∈ 8:00~24:00), 15:00 的高频槽位会让回填误判"低频档已跑"→ 当天漏采。
    判据改为: (data_type ∈ 低频源集合) ∧ trigger_type='scheduled' ∧ status='success'
             ∧ finished_at 落在今天 ∧ finished 小时 == 该源配置小时。

    入参 rows: [(data_type, finished_at)]; sched_map: {data_type: {"hour": h, ...}}。
    返回 {"<today_key>:<hour>": True}。
    """
    out: dict[str, bool] = {}
    for dt_name, fin_at in rows:
        if fin_at is None or dt_name is None:
            continue
        fin_cn = fin_at if fin_at.tzinfo else fin_at.replace(tzinfo=timezone.utc)
        fin_cn = fin_cn.astimezone(tz_cn)
        if fin_cn.date() != today_cn:
            continue
        hour = (sched_map.get(dt_name) or {}).get("hour", default_hour)
        if fin_cn.hour == hour:
            out[f"{today_key}:{hour}"] = True
    return out


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


def should_sync_shared_docs(slot: str | None, done: bool = False) -> bool:
    """共享文档在线采集是否应在当前高频槽位触发(#35 统一调度, #51 改槽位语义)。

    语义: 与 BMS 高频**同槽位节奏**(架构 §六.6 风险缓解):
      - `slot` 为当前命中的槽位标识(由 `current_slot(...)` 得出); None = 未命中任何槽位;
      - 同槽位已 done(已采过)→ False(防主循环 60s 多次 tick 重复触发);
      - 与 BMS 共用同一 slot 判定 → 改配置后两者自动同步变化, 不会脱钩。
    """
    if done:
        return False
    return slot is not None


class BmsScheduler:
    """BMS 定时同步调度器

    关键机制:
      - 启动时从 bms_sync_logs 回填「今天哪些 scheduled 小时已经成功执行过」,
        避免服务重启导致重复执行或重复漏掉整点。
      - 启动后立刻执行一次「启动补跑」: 当日已过但未执行的槽位
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
        # #51: 已完成槽位登记 —— date_str -> set("HH:MM"); 原 _last_run_hour(小时集合)已升级
        self._last_run_slot: dict[str, set[str]] = {}
        self._catch_up_done = False  # 启动补跑是否已执行(避免反复补跑)
        # 专项级熔断: date_str -> set(已失败暂停的 data_type 名称)
        self._halted_types: dict[str, set[str]] = {}
        # 预警扫描: date_str -> set(已扫描的槽位标识), 一槽位最多一次(scan_alerts 已做日幂等)
        self._alert_scanned_slots: dict[str, set[str]] = {}
        # 低频档当日是否已执行/已尝试(A-20260907-001 / #31): date_str -> bool
        # 与 _last_run_slot 分开: 低频小时(6:00)不在高频槽位体系, 用独立日标记防重复
        self._low_freq_done: dict[str, bool] = {}
        # #299: device_combo 历史回补当夜是否已启动: date_str -> bool
        # (回补本体是后台 task, 服务内部 04:45 硬停止, 这里只防当夜重复触发)
        self._combo_backfill_done: dict[str, bool] = {}
        self._combo_task: asyncio.Task | None = None
        # 共享文档采集"槽位内已完成"(统一调度 #35/#51): date_str -> set(已完成槽位标识)
        # 语义: 共享文档与 BMS 高频同槽位节奏, 同槽位只采一次(防 60s 主循环重复触发)
        self._shared_doc_done: dict[str, set[str]] = {}
        # 启动后延迟预热任务句柄(防 GC 中断)
        self._warm_task: asyncio.Task | None = None
        # 日志保留期清理当日已执行标记(YYYY-MM-DD 北京日; 2026-09-29)
        self._retention_done_key: str | None = None

    # ── #51: 配置读取与变更回调 ──
    def _load_schedule(self):
        """读高频节拍配置(带 60s 缓存)。返回 (slots, start, end, interval)。

        读库失败 → 回退代码默认值(8/24/60 等价旧行为), 保证调度不因配置读失败而停摆。
        """
        db = SessionLocal()
        try:
            from app.services.scheduler_settings_service import get_cached_slots

            return get_cached_slots(db)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[调度器] 读高频节拍配置失败, 回退默认(8~24 每小时): {e}")
            fallback = [
                f"{h:02d}:00" for h in range(DEFAULT_HIGH_FREQ_START_HOUR, DEFAULT_HIGH_FREQ_END_HOUR)
            ]
            return (
                fallback,
                DEFAULT_HIGH_FREQ_START_HOUR,
                DEFAULT_HIGH_FREQ_END_HOUR,
                DEFAULT_HIGH_FREQ_INTERVAL_MINUTES,
            )
        finally:
            db.close()

    def on_settings_changed(self) -> None:
        """配置变更回调(#51): 清空当日槽位登记, 按新配置重新回填。

        为什么必须清空(架构 §六.4 风险): 例如 10:17 把 interval 从 60 改成 30,
        旧登记里已有 "10:00"; 若不清空, 新槽位 "10:30" 之外的边界判断会混乱
        (旧按小时登记没有 "10:30" 概念)。清空后按新 interval 重新回填当日已成功槽位。
        代价: 改配置后可能触发一次补跑 —— 管理员主动操作, 可接受。
        """
        self._last_run_slot = {}
        self._shared_doc_done = {}
        self._alert_scanned_slots = {}
        # 立即按新配置回填(不置 _catch_up_done=False, 避免重复触发启动补跑逻辑)
        try:
            self._reload_today_completed_hours()
            today_key = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
            logger.info(
                "[调度器] 节拍配置已变更, 当日槽位登记已按新配置重算: %s",
                sorted(self._last_run_slot.get(today_key, set())),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[调度器] 变更后重算槽位失败(下次 tick 自愈): {e}")

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
                # #203: 手动恢复 → 平息熔断登记的 Incident(同源同业务日, 幂等)
                self._close_circuit_incident(biz_key, dt, note="手动恢复熔断自动平息")
            self._halted_types[biz_key] = set()
            logger.info(f"[调度器] 已清除 {biz_key} 的 {n} 个专项熔断标记({sorted(halted_set)}),自动同步恢复")

    def _on_combo_backfill_done(self, task: asyncio.Task) -> None:
        """#299 回补后台 task 收尾: 必须取回异常/结果, 避免 "Task exception never retrieved"。"""
        try:
            summary = task.result()
            logger.info(f"[调度器] device_combo 历史回补当夜结束: {summary}")
        except asyncio.CancelledError:
            logger.info("[调度器] device_combo 历史回补 task 被取消")
            return
        except Exception as e:  # noqa: BLE001 后台任务异常不炸调度器, 台账保证次夜续跑
            logger.error(
                f"[调度器] device_combo 历史回补异常退出(未完成片留 combo_backfill_slices, 次夜续跑): {e}",
                exc_info=True,
            )
            return
        # #320: 回补完成后刷新 dim_device_combo 干净维度(SCD2 重建),
        # 保证退款/投诉两侧套餐维度读到最新套餐时点数据。
        try:
            from app.core.database import SessionLocal
            from app.services.dim_device_combo_service import rebuild_dim_device_combo
            _dim_db = SessionLocal()
            try:
                res = rebuild_dim_device_combo(_dim_db)
                logger.info(f"[调度器] dim_device_combo 随回补刷新完成: {res}")
            finally:
                _dim_db.close()
        except Exception as e:  # noqa: BLE001 维度刷新失败不影响主流程, 下次回补再试
            logger.error(f"[调度器] dim_device_combo 刷新失败(下次回补重试): {e}", exc_info=True)

    def get_status(self) -> dict:
        """获取调度器状态(含 SyncTaskManager 的当前任务进度 + 专项级熔断状态)"""
        TZ_CN = timezone(timedelta(hours=8))
        now = datetime.now(TZ_CN)
        # 业务日期: 4 点之前仍算「昨天」(这样 0:00~3:59 看到的 halt 也是昨天的)
        biz_date = now.date() if now.hour >= 4 else (now - timedelta(days=1)).date()
        biz_key = biz_date.strftime("%Y-%m-%d")
        halted_types = sorted(self._halted_types.get(biz_key) or set())
        has_any_halt = bool(halted_types)

        # 下一次运行: 专项级熔断不影响下一个槽位的触发(仍会尝试,只是跳过已熔断的专项)
        slots, start_h, end_h, interval_m = self._load_schedule()
        today_remaining = [s for s in slots if s > now.strftime("%H:%M")]
        if today_remaining:
            next_slot = today_remaining[0]
            next_date = now.date()
        elif slots:
            next_slot = slots[0]
            next_date = now.date() + timedelta(days=1)
        else:
            next_slot = None
            next_date = now.date()
        if next_slot:
            nh, nm = (int(x) for x in next_slot.split(":"))
            next_run = datetime(next_date.year, next_date.month, next_date.day, nh, nm, 0)
        else:
            next_run = None

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
            # #51: 槽位体系(新). schedule_hours 保留为兼容字段 —— 旧前端按 "h:00" 渲染,
            # 若直接删除会导致前端与后端必须同时发布(架构 §3.2 兼容性说明)。
            "slots": slots,
            "slot_count": len(slots),
            "start_hour": start_h,
            "end_hour": end_h,
            "interval_minutes": interval_m,
            "schedule_hours": sorted({int(s.split(":")[0]) for s in slots}),
            "daytime_hours": sorted({int(s.split(":")[0]) for s in slots}),
            "catchup_hour": CATCHUP_HOUR,
            # #97: 低频档整点暴露给前端, 供「手动同步」分组标题动态展示
            #      (A-20260907-001 LOCKED §四 预留的展示增强)。0 = 低频档已禁用。
            "low_freq_hour": LOW_FREQ_HOUR,
            "next_run": next_run.isoformat() if next_run else None,
            "next_slot": next_slot,
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
        """从 bms_sync_logs 回填「今天哪些 scheduled **槽位**已经成功执行过」。

        #51 语义升级: 原按「整点小时」登记, 现按「槽位」(interval 粒度)登记。
        判断标准: 今天北京时间下, trigger_type='scheduled' 且 status='success'
        的记录中, finished_at(北京时间)**向下取整到 interval 边界**所属的槽位。

        ⚠️ 边界(架构 §六.3 风险): 08:29 完成 → 归 "08:00" 槽(而不是 "08:30"),
           因为 08:30 槽尚未开始执行。用 `slot_of()` 纯函数保证口径与调度判定一致。

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
        # #51: 回填需按当前配置的 interval 切分
        _slots, _sh, _eh, _im = self._load_schedule()
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
            slots_done: set[str] = set()
            for (fin_at,) in rows:
                if fin_at is None:
                    continue
                if fin_at.tzinfo is None:
                    fin_cn = fin_at.replace(tzinfo=timezone.utc).astimezone(TZ_CN)
                else:
                    fin_cn = fin_at.astimezone(TZ_CN)
                if fin_cn.date() == today_cn:
                    slot = slot_of(fin_cn.hour * 60 + fin_cn.minute, _im)
                    if slot:
                        slots_done.add(slot)
            today_key = now_cn.strftime("%Y-%m-%d")
            self._last_run_slot = {today_key: slots_done}
            # ── #31: 低频档回填 ──
            # 低频小时(LOW_FREQ_HOUR)不在高频槽位体系, _last_run_slot 不直接体现。
            # 若今天低频小时已有 scheduled 成功记录(任一低频源成功即认为本轮已尝试,
            # 因为整档一次 sync_all 提交, 部分源失败也会整档成功结束), 置 _low_freq_done
            # 避免重启后重复跑低频档。
            self._low_freq_done = {}
            # #299: 历史回补日标记同日切清空(回补 service 无状态重启, 缺口台账在 DB)
            self._combo_backfill_done = {}
            if LOW_FREQ_HOUR > 0:
                # #191: 回填改看"低频源自身的 scheduled 成功记录"(见 low_freq_done_from_logs),
                #   不再用高频槽位推断 —— 低频小时若落在高频时段内(E 路 15:00)会误判已跑导致漏采。
                _bf_db = SessionLocal()
                try:
                    from app.services.sync_source_config_service import (
                        list_low_freq_schedules,
                        list_scheduled_bms_sources,
                    )

                    _bf_low = list_scheduled_bms_sources(_bf_db).get("low") or []
                    _bf_sched = list_low_freq_schedules(_bf_db)
                    _bf_rows = []
                    if _bf_low:
                        _bf_rows = _bf_db.execute(
                            select(BmsSyncLog.data_type, BmsSyncLog.finished_at)
                            .where(
                                BmsSyncLog.trigger_type == "scheduled",
                                BmsSyncLog.status == "success",
                                BmsSyncLog.data_type.in_(_bf_low),
                                BmsSyncLog.finished_at.isnot(None),
                            )
                            .order_by(desc(BmsSyncLog.finished_at))
                            .limit(200)
                        ).all()
                    self._low_freq_done = low_freq_done_from_logs(
                        _bf_rows, _bf_sched, today_key=today_key, today_cn=today_cn,
                        default_hour=LOW_FREQ_HOUR, tz_cn=TZ_CN,
                    )
                    if self._low_freq_done:
                        logger.info(
                            f"[调度器] 低频档回填: 今日已执行的小时档={sorted(self._low_freq_done)},"
                            f" 重启不重复触发"
                        )
                except Exception as _bfe:  # noqa: BLE001  回填失败不阻塞调度
                    logger.warning(f"[调度器] 低频档回填读库失败(忽略): {_bfe}")
                finally:
                    _bf_db.close()

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
                f"[调度器] 回填: 今日已成功槽位={sorted(slots_done) if slots_done else '（无）'},"
                f" 已熔断专项={sorted(halted_dt) if halted_dt else '无'} (biz_date={biz_key}),"
                f" 下一次计划窗口: {[s for s in _slots if s > now_cn.strftime('%H:%M')][:6]}"
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
          4. 在后台循环的第一次检查里执行「启动补跑」(当日已过但未成功执行的槽位立刻补一次)
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
        _slots, _sh, _eh, _im = self._load_schedule()
        logger.info(
            f"BMS 定时同步已启动, 高频节拍={_sh}:00~{_eh}:00 每 {_im} 分钟"
            f"({len(_slots)} 槽/天, 首槽={_slots[0] if _slots else '-'} 末槽={_slots[-1] if _slots else '-'}),"
            f" 补跑={CATCHUP_HOUR}:00, 低频档={LOW_FREQ_HOUR}:00,"
            f" 槽位宽容窗口=min({TRIGGER_WINDOW_MINUTES}, interval)"
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
        """主循环: 每分钟检查是否命中高频槽位; 首次检查先做启动补跑

        调度策略(#51 / A-20260910-006 LOCKED):
          - 高频档: 按 DB 配置的「起止时段 + 间隔」切分槽位(默认 8:00~24:00 每 30 分钟),
            命中槽位窗口且该槽未执行过 → 同步;
            若某 data_type(专项)失败 → 仅暂停该专项当天后续槽位; 其他专项不受影响
          - 凌晨 CATCHUP_HOUR (5:00): 对「前一天」做整体补跑,忽略所有专项熔断
          - 低频档 LOW_FREQ_HOUR (6:00): 低频源跑前一天整天, 每天 1 次(不受节拍影响)
        """
        TZ_CN = timezone(timedelta(hours=8))
        first_tick = True
        while self._running:
            try:
                now_cn = datetime.now(TZ_CN)
                date_str = now_cn.strftime("%Y-%m-%d")
                # #51: 当日已完成**槽位**集合(原 _last_run_hour 小时集合已升级)
                today_done = self._last_run_slot.setdefault(date_str, set())
                # 读高频节拍配置(带 60s 缓存) + 算当前命中槽位
                slots, sch_start, sch_end, sch_interval = self._load_schedule()
                cur_slot = current_slot(now_cn.hour, now_cn.minute, sch_interval, slots)
                # 业务日期(用于熔断判断): hour 0~3 仍归属前一天
                biz_date = now_cn.date() if now_cn.hour >= 4 else (now_cn - timedelta(days=1)).date()
                biz_key = biz_date.strftime("%Y-%m-%d")
                halted_today = self._halted_types.get(biz_key) or set()

                # ── 预警扫描(每槽位最多一次, scan_alerts 内部做当日幂等) ──
                alert_done = self._alert_scanned_slots.setdefault(date_str, set())
                if cur_slot and cur_slot not in alert_done:
                    alert_done.add(cur_slot)
                    try:
                        await self._scan_alerts_safe()
                    except Exception as ae:  # noqa: BLE001
                        logger.warning(f"[调度器] 预警扫描异常: {ae}")

                # ── 共享文档(腾讯文档)同步: 按间隔轮询, 到点且空闲时执行 ──
                try:
                    await self._maybe_sync_tencent_doc()
                except Exception as de:  # noqa: BLE001
                    logger.warning(f"[调度器] 共享文档同步检查异常: {de}")

                # ── 共享文档在线采集(doc_sync_sources): 同高频槽位节奏, 同槽只采一次(#35/#51) ──
                try:
                    await self._maybe_sync_shared_docs(now_cn, date_str, cur_slot)
                except Exception as sd:  # noqa: BLE001
                    logger.warning(f"[调度器] 共享文档在线采集检查异常: {sd}")

                # ── 启动补跑(仅一次): 今日已过但未执行的槽位, 立即补一次(#51 槽位语义) ──
                if first_tick and not self._catch_up_done:
                    self._catch_up_done = True
                    # 候选 = 今日已到时刻的槽位(严格早于当前时刻, 当前槽由正常分支处理)
                    now_hm = now_cn.strftime("%H:%M")
                    missed = [s for s in slots if s < now_hm and s not in today_done]
                    if missed:
                        # 专项级: 只要还有未熔断的专项就值得补跑(已熔断的会在 runner 里跳过)
                        logger.info(
                            f"[调度器] 启动补跑: 今日遗漏槽位={missed}"
                            f"(共{len(missed)}个), 已熔断专项={sorted(halted_today) if halted_today else '无'}, "
                            f"立刻触发一次全量(00:00~now)补同步"
                        )
                        await self._do_scheduled_sync(
                            why=f"启动补跑错过槽位{len(missed)}个(末个={missed[-1]})",
                            yesterday_full_sync=False,
                            biz_key=biz_key,
                        )
                        first_tick = False
                        for s in missed:
                            today_done.add(s)
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

                # ── 1.4) device_combo 历史切片回补档(#299): 01:00~04:45 夜间窗口 ──
                # BMS 套餐导出 10 万行硬上限 + 每日增量长期失效(搜索被日期面板拦截),
                # 2024 年起约 700+ 北京业务日缺失。夜间空闲档启动后台回补 task:
                #   - 服务内部 04:45 硬停止, 缺口未清完次夜按 combo_backfill_slices
                #     台账续跑; 无缺口夜 no-op(兼作未来漏采自愈);
                #   - 后台运行不阻塞主 tick; 与 sync_task_manager 互斥(同账号并发踢登录);
                #   - 让开 05:00 缺口补跑、06:00 低频档、08:00 起高频档。
                from app.services import combo_backfill

                if combo_backfill.in_night_window(now_cn):
                    _cb_key = f"{date_str}:combo-backfill"
                    if not self._combo_backfill_done.get(_cb_key, False):
                        if combo_backfill.is_running():
                            # 重启恢复/手动 API 已启动 → 只补登记, 不重复起
                            self._combo_backfill_done[_cb_key] = True
                            logger.info("[调度器] device_combo 回补已在运行, 当夜不重复触发")
                        elif sync_task_manager.is_busy():
                            logger.info("[调度器] 夜间回补窗口但同步任务忙碌, 下一 tick 顺延")
                        else:
                            self._combo_backfill_done[_cb_key] = True
                            logger.info(
                                "[调度器] 进入夜间窗口, 启动 device_combo 历史切片回补(#299, "
                                "04:45 硬停止, 缺口留档次夜续跑)"
                            )
                            self._combo_task = asyncio.create_task(
                                combo_backfill.run_backfill(triggered_by="scheduled")
                            )
                            self._combo_task.add_done_callback(self._on_combo_backfill_done)

                # ── 1.5) 低频源每日定时档(A-20260907-001 / #31) ──
                # 每天对"DB default_sync=true − 高频4源"的低频源跑一次, 窗口=前一天整天。
                # #191 (2026-09-24) 改为**按源可配小时**(options.low_freq_hour, 默认 LOW_FREQ_HOUR):
                #   实测上游 T+1 且**次日白天才发布**的源(退款记录报表 E 路)固定 06:00 永远取 0 行
                #   (生产 bms_sync_logs: scheduled 档 total_rows 恒 0, 靠人工手动触发才有数据),
                #   故允许把该源挪到发布之后的小时(当前配置 15:00), 仍是"每天 1 次"。
                # 触发/顺延规则(沿用 2026-09-07 语义, 只把"整档一小时"细化为"按源小时"):
                #   - 窗口 = 该源配置小时起 1 小时内(eg 15:00~15:59), 空闲即执行并记 done;
                #   - 窗口内 busy → **不记 done**, 下一分钟 tick 再试(忙顺延, 不静默丢档);
                #   - 到达下一整点仍忙 → 当日放弃该小时档(记 done + 告警), 靠失败进 gap
                #     → 次日 5:00 缺口清点兜底。
                if LOW_FREQ_HOUR > 0:
                    _lf_hour_key = f"{date_str}:{now_cn.hour}"
                    if not self._low_freq_done.get(_lf_hour_key, False):
                        _lf_db = SessionLocal()
                        try:
                            from app.services.sync_source_config_service import (
                                list_low_freq_schedules,
                                list_scheduled_bms_sources,
                            )

                            _low_types_all = list_scheduled_bms_sources(_lf_db).get("low") or []
                            _lf_sched = list_low_freq_schedules(_lf_db)
                        except Exception as _lfe:  # noqa: BLE001
                            logger.warning(f"[调度器] 低频档读库失败, 跳过本轮: {_lfe}")
                            _low_types_all, _lf_sched = [], {}
                        finally:
                            _lf_db.close()
                        # 该小时的到期源(未配置小时的源 → 默认 LOW_FREQ_HOUR 档)
                        _low_types = [
                            dt for dt in _low_types_all
                            if _lf_sched.get(dt, {}).get("hour", LOW_FREQ_HOUR) == now_cn.hour
                        ]
                        if not _low_types:
                            if now_cn.minute == 0 and _low_types_all:
                                logger.info(
                                    f"[调度器] 低频档 {now_cn.hour:02d}:00 无到期源"
                                    f"(低频源均按 options.low_freq_hour 配置各自小时), 跳过"
                                )
                        elif should_run_low_freq(
                            # 复用既有"忙顺延"判据(锁定语义): 该源小时窗口内且空闲才跑
                            now_cn.hour,
                            low_freq_hour=now_cn.hour,
                            done=False,
                            busy=sync_task_manager.is_busy(),
                        ):
                            self._low_freq_done[_lf_hour_key] = True
                            _wmap = {
                                dt: _lf_sched[dt]["window_days"]
                                for dt in _low_types
                                if _lf_sched.get(dt, {}).get("window_days")
                            }
                            logger.info(
                                f"[调度器] 低频档触发 {date_str} {now_cn.hour:02d}:{now_cn.minute:02d},"
                                f" 低频源={_low_types}"
                                f"(默认窗口=前一天整天; 自定义近N天={_wmap or '无'})"
                            )
                            await self._do_scheduled_sync(
                                why=f"低频源每日档 {now_cn.hour:02d}:00",
                                yesterday_full_sync=True,
                                biz_key=biz_key,
                                source_tier="low",
                                data_type_filter=set(_low_types),
                                window_days_map=_wmap,
                            )
                        elif now_cn.minute >= 59:
                            # 整小时内持续忙 → 当日放弃该小时档(忙顺延到小时的最后一分钟)
                            self._low_freq_done[_lf_hour_key] = True
                            # R-P1-17: 放弃 = 没有同步尝试 → 没有失败结果可记账;
                            # 必须直接记 open, 否则次日 5:00 缺口清点漏掉这些源(永久漏采)。
                            # 业务日: 默认昨天; 自定义 window_days 的源 → [今天-wd .. 昨天]。
                            try:
                                from app.services.gap_queue_service import record_abandoned_slots

                                _rows: list[tuple[str, str]] = []
                                for _dt in _low_types:
                                    _wd = int(_lf_sched.get(_dt, {}).get("window_days") or 1)
                                    for _k in range(1, max(_wd, 1) + 1):
                                        _d = (now_cn.date() - timedelta(days=_k)).strftime("%Y-%m-%d")
                                        _rows.append((_d, _dt))
                                _gap_db = SessionLocal()
                                try:
                                    _n = record_abandoned_slots(
                                        _gap_db, _rows,
                                        reason=f"低频档{now_cn.hour:02d}:00持续忙碌当日放弃",
                                    )
                                    if _n:
                                        logger.info(f"[调度器] 低频放弃档缺口记账: {_n} 条(open)")
                                finally:
                                    _gap_db.close()
                            except Exception as _ge:  # noqa: BLE001 记账失败不影响调度主流程
                                logger.warning(f"[调度器] 低频放弃档记账失败(忽略): {_ge}")
                            logger.warning(
                                f"[调度器] 低频档 {now_cn.hour:02d}:00 源={_low_types} 因持续忙碌错过,"
                                f" 当日放弃该档; 已记 gap → 次日 5:00 缺口清点兜底"
                            )
                        else:
                            logger.info(
                                f"[调度器] 低频档 {now_cn.hour:02d}:00 源={_low_types} 因忙碌顺延等待"
                            )

                # ── 2) 高频档槽位触发(#51: DB 可配时段+间隔, 默认 8:00~24:00 每 30 分钟) ──
                if cur_slot and cur_slot not in today_done:
                    today_done.add(cur_slot)
                    # 快速预检: 当前有资格同步的数据源 = (BMS 高频 + SCRM 有凭证) ∩ 未熔断
                    # #31: BMS 高频候选读 DB default_sync=true 的 high 档
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
                        logger.warning(
                            f"[调度器] {biz_key} 本次槽位 {cur_slot} 所有源均已熔断或无凭证"
                            f" (已暂停={sorted(halted_today)}), 跳过本轮"
                        )
                    else:
                        if halted_today:
                            logger.info(
                                f"[调度器] {biz_key} 槽位 {cur_slot} 触发高频同步,"
                                f" 已熔断专项(将跳过)={sorted(halted_today)},"
                                f" 本次执行专项={effective_types}"
                            )
                        else:
                            logger.info(
                                f"触发高频定时同步: {date_str} 槽位 {cur_slot}"
                                f" (当前 {now_cn.strftime('%H:%M')}, 窗口内)"
                            )
                        # 8:00 全量兜底: 由「首槽 = 8:00」判定(而非硬编码 hour==8),
                        #   保证管理员把时段改成 7:00 起始时, 首槽同样带全量兜底。
                        is_first_slot = bool(slots) and cur_slot == slots[0]
                        await self._do_scheduled_sync(
                            why=f"定时触发槽位 {cur_slot}",
                            yesterday_full_sync=False,
                            full_window_days=FULL_WINDOW_DAYS if is_first_slot else 0,
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

                # ── #191: 数据源时效巡检(每小时一次; 内部对超期源按"源+当日"节流告警) ──
                #   背景: E 路曾"定时档天天成功但取 0 行"连断两天无人知晓, 需主动巡检
                #   "最新业务时间距今天数", 而不是等看板/AI 报"暂无"才发现。
                if now_cn.minute == 5:
                    try:
                        from app.services.source_freshness_service import run_freshness_check

                        _fr = await asyncio.to_thread(run_freshness_check)
                        _stale = [r["source"] for r in _fr["rows"] if r["stale"]]
                        logger.info(
                            f"[调度器] 数据源时效巡检: 检查 {_fr['checked']} 个, "
                            f"超期 {_stale or '无'}, 新告警 {_fr['alerts']}"
                        )
                    except Exception as fe:  # noqa: BLE001 巡检失败不影响调度
                        logger.warning(f"[调度器] 数据源时效巡检失败(忽略): {fe}")

                # ── 日志保留期清理(每天 03:15 北京时间; 在 5:00 补跑/6:00 低频档前,
                #    纯 DELETE 不碰业务表; 按北京日去重, 进程重启当天不重复) ──
                if now_cn.hour == 3 and now_cn.minute == 15 \
                        and self._retention_done_key != now_cn.strftime("%Y-%m-%d"):
                    self._retention_done_key = now_cn.strftime("%Y-%m-%d")
                    try:
                        from app.services.log_retention_service import run_retention_purge

                        def _do_purge():
                            _db = SessionLocal()
                            try:
                                return run_retention_purge(_db)
                            finally:
                                _db.close()

                        _pr = await asyncio.to_thread(_do_purge)
                        logger.info(
                            f"[调度器] 日志保留期清理完成: 共删 {_pr['total']} 行"
                            f"{', 错误表=' + str(list(_pr['errors'])) if _pr['errors'] else ''}"
                        )
                    except Exception as re_:  # noqa: BLE001 清理失败不影响调度
                        logger.warning(f"[调度器] 日志保留期清理失败(忽略): {re_}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"调度器异常: {e}", exc_info=True)

            await asyncio.sleep(CHECK_INTERVAL)

    async def _maybe_half_open_probe(self, biz_key: str, now_cn) -> None:
        """半开探测(A-20260904-002 / 工单#2 3.2): 熔断源周期性单片试探。

        - 触发条件: 落在高频时段内、避开首槽窗口、当前无任务忙、
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
        # 仅高频时段内探测(5:00 补跑/低频档本就忽略熔断强制全跑, 无需半开)
        # #51: 判定从「命中 DAYTIME_HOURS」改为「落在高频时段 [start, end) 内」
        _slots, _sh, _eh, _im = self._load_schedule()
        if not (_sh <= now_cn.hour < _eh):
            return
        if sync_task_manager.is_busy():
            return  # 有任务在跑, 下一分钟再探
        # 避开首槽(可能带全量兜底)与 5:00 补跑窗口, 减少撞车
        first_slot = _slots[0] if _slots else None
        if first_slot:
            fh, fm = (int(x) for x in first_slot.split(":"))
            f_min = fh * 60 + fm
            if f_min <= now_cn.hour * 60 + now_cn.minute < f_min + slot_trigger_window(_im):
                return
        if now_cn.hour == CATCHUP_HOUR and now_cn.minute < TRIGGER_WINDOW_MINUTES:
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
                # #203: 恢复 → 平息熔断登记的 Incident(同源同业务日, 幂等)
                self._close_circuit_incident(biz_key, dt, note="半开探测恢复自动平息")
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

        # ── #94 (2026-09-14): 派生别名必须映射到其宿主源 ──────────────────
        # `ticket_finish` 是 `ticket` 的**按完成时间补充抓取**别名
        # （`bms_sync.sync_all` 里由 `data_type == "ticket"` 分支派生出同批任务），
        # 它**不对应 sync_source_configs 里的独立配置行**，也不会出现在
        # `list_scheduled_bms_sources()` 的结果里 → 熔断后走 else 分支报
        # 「未知 data_type=ticket_finish, 无法定向试探」（生产实测 10 次），
        # 导致该源熔断后**只能等整点槽位**（最长 +1h），半开探测这条恢复路径被堵死。
        #
        # 正解：把它映射回宿主源 `ticket` 探测 —— 二者抓的是同一份工单数据
        # （`bms_sync` 导入时 `ticket_finish` 明确按 `ticket` 记账），
        # 探测 ticket 可用即等价于 ticket_finish 可用。
        # 这样既不必往 DB 塞"假配置行"，也不需要特殊分支。
        _PROBE_ALIAS = {"ticket_finish": "ticket"}
        _probe_dt = _PROBE_ALIAS.get(dt, dt)
        if _probe_dt != dt:
            logger.info(
                "[调度器] 半开探测: data_type=%s 是派生别名, 按宿主源 %s 定向试探",
                dt, _probe_dt,
            )
        dt = _probe_dt

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
            # #94: 告警文案必须可行动 —— 原「未知 data_type=xxx」无法区分
            #   「代码有 bug」与「只是没登记进 DB 名单」，运维看到只能猜。
            #   现在显式区分并给出修复指引。
            _hint = (
                "该源未登记为 default_sync=true（或已被禁用）→ 请在"
                "「同步设置」中开启『纳入周期采集』；"
                "若它是派生别名（如 ticket_finish 之于 ticket），"
                "应在 bms_scheduler._half_open_probe_once 的 _PROBE_ALIAS 中映射到宿主源"
            )
            logger.warning(
                "[调度器] 半开探测: data_type=%s 不在可调度名单, 无法定向试探。%s",
                dt, _hint,
            )
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

    async def _maybe_sync_shared_docs(self, now_cn, date_str: str, cur_slot: str | None):
        """共享文档在线采集(doc_sync_sources) — 同高频槽位节奏(#35 统一调度, #51 改槽位语义)。

        与 BMS 高频**同一槽位**触发: `cur_slot` 为当前命中的槽位标识(由主循环传入,
        与 BMS 判定共用同一个 `current_slot()` → 两者永不脱钩, 见架构 §六.6)。
        同槽位不重复(用 _shared_doc_done 记录已完成槽位, 防主循环 60s 多次 tick 重复触发)。

        源级 interval 仍生效(due_doc_sources): 槽位触发时, 距上次 >= interval 的源才采
        (interval=120 的源可跳过若干槽位)。空闲才跑, 单源失败不影响其它源。
        """
        from app.services.shared_doc_sync_service import due_doc_sources, sync_doc_source_safe

        # 1) 槽位守卫: 当前命中槽位 + 同槽位只采一次
        done_set = self._shared_doc_done.setdefault(date_str, set())
        if not should_sync_shared_docs(cur_slot, done=cur_slot in done_set):
            return
        # 2) 空闲才跑(避免与 BMS/SCRM 大同步并发 playwright)
        if sync_task_manager.is_busy():
            return

        db = SessionLocal()
        try:
            due = due_doc_sources(db)
        finally:
            db.close()
        if not due:
            # 无到点源也算该槽位已检查过(避免每 tick 空转查库)
            done_set.add(cur_slot)
            return
        logger.info(f"[调度器] 共享文档在线采集(高频槽位 {cur_slot}) 到点 "
                    f"{len(due)} 个源, 开始采集")
        for row in due:
            try:
                await asyncio.to_thread(sync_doc_source_safe, row.id, "scheduled")
            except Exception as e:  # noqa: BLE001
                logger.error(f"[调度器] 共享文档源 #{row.id} 采集异常: {e}", exc_info=True)
        # 无论成败, 该槽位已处理过。失败源 last_attempt_at 已刷但 last_sync_status=failed
        # (last_sync_at 成功水位不被污染, R-P1-16 #241): due_doc_sources 对 failed 源按
        # min(interval, DOC_FAILED_RETRY_CAP_MINUTES=30min) 判定 → 60min 档源约在下一个
        # 高频槽位(30min)即到期重试, 不再静默等满整个 interval。
        done_set.add(cur_slot)

    async def _do_scheduled_sync(
        self,
        why: str | None = None,
        yesterday_full_sync: bool = False,
        full_window_days: int = 0,
        biz_key: str | None = None,
        source_tier: str = "high",
        data_type_filter: set[str] | None = None,
        window_days_map: dict[str, int] | None = None,
    ):
        """执行定时同步 - 通过 SyncTaskManager 提交(全局互斥)

        Args:
            why: 人类可读的触发原因(打日志用)
            data_type_filter(#191): 只跑这些源(低频档按源小时分组时用, 避免"某小时档"
                把所有低频源都拖上); None=不额外过滤(沿用档位集合)。
            window_days_map(#191): {data_type: 近N天} 覆盖该源本轮的窗口(取"今天-N 天 0 点~now"),
                用于吸收上游 T+N 发布延迟; 未列出的源沿用档位默认窗口。
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
        if data_type_filter:
            # #191: 低频档按"源小时"分组 → 本轮只跑到期的那几个源(而非全部低频源)
            _tier_types = [dt for dt in _tier_types if dt in data_type_filter]
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
        # #191: 按源覆盖窗口(近 N 天 0 点 ~ now) —— 吸收上游 T+N 发布延迟
        #   (如 E 路退款记录报表 T+1 且次日白天才发布, 窗口只给"昨天整天"时会天天取 0 行)
        _wmap = window_days_map or {}
        for _dt, _days in _wmap.items():
            if _dt in time_ranges_bms_dt and _days > 0:
                _ws = now_cn - timedelta(days=_days)
                time_ranges_bms_dt[_dt] = (
                    _ws.replace(hour=0, minute=0, second=0, microsecond=0), now_cn
                )
                logger.info(f"[定时同步{reason}] {_dt} 窗口覆盖为 近{_days}天(源级配置)")
        time_ranges_display = {}
        for dt in sync_types_bms:
            if dt in _wmap and _wmap[dt] > 0:
                time_ranges_display[dt] = (
                    f"近{_wmap[dt]}天 00:00",
                    range_end.strftime("%m-%d %H:%M:%S"),
                )
            else:
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
        """熔断触发 → 站内信通知 + Incident 自动登记(A-20260904-002 §五#5 / #203)。

        复用 notifier 通知管理员/主管; Incident 登记走 ingest_incident_service
        (与 data_arrival 告警同一幂等键: 同源同业务日只登记一次), 半开恢复/
        手动清除熔断时自动平息(见 _close_circuit_incident)。
        #203 修复: 原实现 notify 后未 commit 即 close → 站内信实际未落库;
        现与 Incident 登记同事务显式提交。
        """
        try:
            from app.services.ingest_incident_service import register_ingest_incident
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
                # #203: 熔断 → Incident 自动登记(幂等; 恢复时自动平息)
                register_ingest_incident(
                    db,
                    metric_code=data_type,
                    period_key=biz_key,
                    subject=f"{data_type} 采集熔断",
                    detail=(
                        f"采集熔断自动登记(工单 #203, A-20260904-002 §五#5)。\n"
                        f"数据源: {data_type}\n业务日: {biz_key}\n"
                        f"处置: 每 {settings.CIRCUIT_HALF_OPEN_INTERVAL_SECONDS // 3600}h "
                        f"自动半开探测, 上游恢复即自动续采; 也可手动触发同步恢复。\n"
                        f"处置指引: docs/ops/数据采集-运维手册.md §4.1 单日补跑"
                    ),
                    impact=f"{data_type} 当日数据缺口(影响该源相关分析口径)",
                )
                db.commit()  # 站内信 + Incident 同事务落库(close 不丢变更)
            except Exception:  # noqa: BLE001 登记失败不影响熔断主流程
                db.rollback()
                raise
            finally:
                db.close()
        except Exception as e:  # noqa: BLE001 通知/登记失败不影响熔断主流程
            logger.warning(f"[调度器] 熔断告警/Incident 登记失败(忽略): {e}")

    def _close_circuit_incident(self, biz_key: str, data_type: str, *, note: str) -> None:
        """熔断恢复(半开探测成功/手动清除) → 平息同源同业务日 Incident(#203)。

        独立 session 显式 commit(恢复路径不在请求事务内); 失败仅 log 不影响恢复。
        """
        try:
            from app.services.ingest_incident_service import close_ingest_incidents
            db = SessionLocal()
            try:
                n = close_ingest_incidents(
                    db, metric_code=data_type, period_key=biz_key,
                    now=datetime.now(timezone.utc), note=note,
                )
                db.commit()
                if n:
                    logger.info(f"[调度器] 熔断恢复平息: {biz_key}/{data_type} 平息 {n} 条 Incident")
            finally:
                db.close()
        except Exception as e:  # noqa: BLE001 平息失败不影响恢复主流程
            logger.warning(f"[调度器] Incident 平息失败(忽略): {biz_key}/{data_type}: {e}")


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

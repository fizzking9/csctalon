"""同步任务管理器 - 全局单例

职责:
  1. 任务互斥锁: 防止同时发起多个同步任务(含手动+定时,跨用户共享)
  2. 实时进度: 进度事件写入内存状态,支持轮询查询
  3. 冷却判断: 上次成功完成后短时间内(默认5分钟)禁止再次发起,避免无意义重复
  4. 异步提交: 支持后台 asyncio task 执行,API 立即返回 task_id

说明:
  - 状态保存在**内存**中(单进程模型),重启会丢失。
  - 部署: 使用单worker(-w 1)保证互斥锁有效; 数据库层唯一约束是重复数据的最后防线。
  - 每个 task_id = sync_{timestamp}_{rand6}
  - 历史保留最近 20 条进度,轮询足够用
"""

import asyncio
import logging
import random
import string
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.bms_sync import _get_last_sync_time

logger = logging.getLogger(__name__)

# 冷却时间(秒): 上次成功完成后, 此间隔内若无 custom 时间范围则拒绝
DEFAULT_COOLDOWN_SECONDS = 5 * 60

# 保留历史任务数
MAX_HISTORY = 20

# 东八区
TZ_CN = timezone(timedelta(hours=8))


@dataclass
class SyncTaskProgress:
    """单个数据源的进度切片"""
    data_type: str
    phase: str  # pending / fetching / waiting / fetched / importing / imported / failed / skipped
    message: str = ""
    detail: dict | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(TZ_CN))


@dataclass
class SyncTask:
    """一个同步任务(一键同步 N 个数据源 = 一个 task)"""
    task_id: str
    trigger_type: str  # manual / scheduled
    triggered_by: str
    data_types: list[str]
    # 时间范围映射: data_type -> (start_str, end_str), UI 展示用
    time_ranges_display: dict[str, tuple[str, str]]
    created_at: datetime = field(default_factory=lambda: datetime.now(TZ_CN))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: str = "queued"  # queued / running / success / failed / partial / rejected
    error: str | None = None
    # 每个数据源的最新进度切片
    per_source: dict[str, SyncTaskProgress] = field(default_factory=dict)
    # 日志流(最近 N 条,用于轮询展示)
    recent_logs: list[dict] = field(default_factory=list)
    # 聚合结果(完成后填充)
    results: list[dict] | None = None

    def to_public_dict(self) -> dict:
        """序列化为前端可消费的结构"""
        total = len(self.data_types)
        done = 0
        for dt in self.data_types:
            p = self.per_source.get(dt)
            if p and p.phase in ("imported", "failed", "skipped"):
                done += 1
        percent = 0 if total == 0 else int(done * 100 / total)
        # 当前正在处理哪个源
        current_source = None
        current_phase = None
        for dt in self.data_types:
            p = self.per_source.get(dt)
            if not p:
                current_source = dt
                current_phase = "等待开始"
                break
            if p.phase in ("fetching", "waiting", "importing", "fetched"):
                current_source = dt
                current_phase = {
                    "fetching": "正在抓取",
                    "waiting": "等待 BMS 冷却",
                    "fetched": "抓取完成,等待导入",
                    "importing": "正在导入",
                }.get(p.phase, p.phase)
                break
            if p.phase in ("imported", "failed", "skipped"):
                continue
        # 所有数据源完成后,检查 post-sync 钩子是否仍在运行(__post_sync__ 不在 data_types 里)
        if current_source is None:
            post_sync = self.per_source.get("__post_sync__")
            if post_sync and post_sync.phase == "importing":
                current_source = "__post_sync__"
                current_phase = "后处理中"
        # 如果全部处理完
        if self.status in ("success", "failed", "partial") and current_source is None:
            current_phase = "全部完成" if self.status == "success" else "完成(部分失败)" if self.status == "partial" else "失败"

        return {
            "task_id": self.task_id,
            "trigger_type": self.trigger_type,
            "triggered_by": self.triggered_by,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "percent": percent,
            "total_sources": total,
            "done_sources": done,
            "current_source": current_source,
            "current_phase": current_phase,
            "time_ranges": self.time_ranges_display,
            "per_source": {
                dt: {
                    "phase": p.phase,
                    "message": p.message,
                    "detail": p.detail,
                    "updated_at": p.updated_at.isoformat(),
                }
                for dt, p in self.per_source.items()
            },
            "recent_logs": self.recent_logs[-30:],
            "results": self.results,
            "error": self.error,
        }


class SyncTaskManager:
    """全局同步任务管理器(单例) - 单worker部署使用内存锁"""

    def __init__(self):
        self._lock = asyncio.Lock()  # 内部操作锁(保护状态读写)
        self._executing = False  # 是否有任务在跑(跨 task 互斥)
        self._current_task: SyncTask | None = None
        self._history: list[SyncTask] = []
        # 最近一次成功完成的时间(所有数据源最后一个 success 的 finished_at 中最大值)
        self._last_success_at: datetime | None = None
        # 冷却阈值(秒)
        self.cooldown_seconds = DEFAULT_COOLDOWN_SECONDS
        # 同步后预热任务的引用持有器(防 GC 中断预热)
        self._warm_bg_tasks: set = set()

    # ---------- 对外: 预检 / 锁判断 ----------

    def is_busy(self) -> bool:
        # 注意: Python 的 a or b 在 a=False, b=None 时返回 None, 必须显式转 bool
        busy = bool(self._executing)
        if not busy and self._current_task is not None:
            busy = bool(self._current_task.status == "running")
        return busy

    def is_writing_heavy(self) -> bool:
        """当前是否处于「写入高峰期」(导入/匹配/宽表构建阶段),
        用于缓存策略:高峰期自动延长TTL防DB击穿,以及UI提示黄条"""
        if not self.is_busy() or self._current_task is None:
            return False
        task = self._current_task
        # __post_sync__ 进行中 = 匹配/宽表/ANALYZE = 写入高峰
        post_sync = task.per_source.get("__post_sync__")
        if post_sync and post_sync.phase == "importing":
            return True
        # 任一数据源在 importing 阶段 = 正在 upsert,写入高峰
        for dt in task.data_types:
            p = task.per_source.get(dt)
            if p and p.phase == "importing":
                return True
        return False

    def get_global_status(self) -> dict:
        """返回给全局 API/前端的状态(跨任务)"""
        cur = self._current_task
        return {
            "is_syncing": self.is_busy(),
            "is_writing_heavy": self.is_writing_heavy(),
            "cooldown_remaining": self.remaining_cooldown_seconds(),
            "last_success_at": self._last_success_at.isoformat() if self._last_success_at else None,
            "current_task_id": cur.task_id if cur else None,
            "current_status": cur.status if cur else None,
            "current_phase": cur.to_public_dict().get("current_phase") if cur else None,
            "current_source": cur.to_public_dict().get("current_source") if cur else None,
            "percent": cur.to_public_dict().get("percent") if cur else 0,
        }

    def get_current_task(self) -> SyncTask | None:
        return self._current_task

    def get_task(self, task_id: str | None) -> SyncTask | None:
        if not task_id:
            return self._current_task
        if self._current_task and self._current_task.task_id == task_id:
            return self._current_task
        for t in self._history:
            if t.task_id == task_id:
                return t
        return None

    def remaining_cooldown_seconds(self) -> int:
        """剩余冷却秒数; <=0 表示可执行"""
        if not self._last_success_at:
            return 0
        elapsed = (datetime.now(TZ_CN) - self._last_success_at).total_seconds()
        return max(0, int(self.cooldown_seconds - elapsed))

    def compute_incremental_ranges(self, db_session, data_types: list[str]) -> dict[str, tuple[datetime, datetime, str, str]]:
        """计算每个数据源的增量时间范围

        Returns:
            {data_type: (start_dt, end_dt, start_display_str, end_display_str)}
        """
        now_cn = datetime.now(TZ_CN)
        today_start = now_cn.replace(hour=0, minute=0, second=0, microsecond=0)
        result: dict[str, tuple[datetime, datetime, str, str]] = {}
        for dt in data_types:
            last = _get_last_sync_time(db_session, dt)  # naive or aware
            if last is None:
                start = today_start
                note = "首次同步/今日无记录,从00:00开始"
            else:
                # 统一转东八区 naive 比较 date
                if last.tzinfo is None:
                    last_cn_naive = last
                else:
                    last_cn_aware = last.astimezone(TZ_CN)
                    last_cn_naive = last_cn_aware.replace(tzinfo=None)
                if last_cn_naive.date() == now_cn.date():
                    # 当天: 从上次 end_time +1 秒开始(避免重复抓同一秒,可重复数据 upsert 幂等不影响但浪费)
                    start = last_cn_naive + timedelta(seconds=1)
                    note = f"上次截止 {last_cn_naive.strftime('%H:%M:%S')},接续增量"
                    # 兜底,不能早于今天0点
                    if start.date() < now_cn.date():
                        start = today_start.replace(tzinfo=None)
                else:
                    start = today_start.replace(tzinfo=None)
                    note = "跨日,从今日00:00开始"
            end = now_cn.replace(tzinfo=None)
            start_disp = f"{start.strftime('%m-%d %H:%M:%S')}（{note}）"
            end_disp = end.strftime('%m-%d %H:%M:%S')
            # 返回 aware: start/end 用 TZ_CN aware,和 bms_sync 的 calculate_time_range 风格对齐
            start_aware = start.replace(tzinfo=TZ_CN) if start.tzinfo is None else start
            end_aware = end.replace(tzinfo=TZ_CN) if end.tzinfo is None else end
            result[dt] = (start_aware, end_aware, start_disp, end_disp)
        return result

    # ---------- 对外: 提交任务 ----------

    async def submit(
        self,
        trigger_type: str,
        triggered_by: str,
        data_types: list[str],
        time_ranges: dict[str, tuple[datetime, datetime]],
        time_ranges_display: dict[str, tuple[str, str]],
        runner_coro,  # async coroutine: (task_id, on_progress_cb) -> list[dict]
        force: bool = False,
        ignore_cooldown: bool = False,
    ) -> tuple[str, str]:
        """提交异步任务

        Returns:
            (task_id, message)
        Raises:
            RuntimeError: 无法获取锁或冷却中
        """
        async with self._lock:
            if self.is_busy():
                ct = self._current_task
                who = f"{ct.triggered_by} 触发的任务" if ct else "任务"
                raise RuntimeError(f"当前有同步任务正在执行({who}),请等待其完成或稍后重试")

            # 冷却校验(仅当没有指定 custom 时间时生效;force/ignore_cooldown 跳过)
            if not force and not ignore_cooldown:
                cool = self.remaining_cooldown_seconds()
                if cool > 0:
                    raise RuntimeError(
                        f"距离上次成功完成仅 {int(self.cooldown_seconds/60)} 分钟内,"
                        f"还需等待 {cool} 秒。若无特殊需要建议稍后执行,或手动选择自定义时间范围强制同步"
                    )

            task_id = "sync_{ts}_{rand}".format(
                ts=datetime.now(TZ_CN).strftime("%Y%m%d%H%M%S"),
                rand="".join(random.choices(string.ascii_lowercase + string.digits, k=6)),
            )
            task = SyncTask(
                task_id=task_id,
                trigger_type=trigger_type,
                triggered_by=triggered_by,
                data_types=list(data_types),
                time_ranges_display=time_ranges_display,
            )
            # 初始化每个源的 pending 进度
            for dt in data_types:
                task.per_source[dt] = SyncTaskProgress(data_type=dt, phase="pending", message="排队中")
            self._current_task = task
            self._prepend_history(task)
            self._executing = True
            task.started_at = datetime.now(TZ_CN)
            task.status = "running"

        # 构造 on_progress 回调,写内存状态
        async def on_progress(data_type: str, phase: str, data: Any):
            await self._on_progress(task_id, data_type, phase, data)

        # 启动后台 task(用当前 event loop)
        asyncio.create_task(self._run_background(task, runner_coro, on_progress))

        return task_id, "任务已提交,可通过 /fetch/task/status 轮询进度"

    async def submit_multi(
        self,
        trigger_type: str,
        triggered_by: str,
        data_types: list[str],
        time_ranges: dict[str, tuple[datetime, datetime]],
        time_ranges_display: dict[str, tuple[str, str]],
        runners: list,
        force: bool = False,
        ignore_cooldown: bool = False,
    ) -> tuple[str, str]:
        """提交并发多 runner 任务(冷却期间穿插其他数据源)。

        BMS 与 SCRM 等相互独立的数据源拆成多个 runner 后通过 asyncio.gather
        并发执行: 当 BMS 内部因「同域 1 分钟下载间隔」冷却 sleep 时, SCRM(不同域)
        的抓取可继续推进, 显著缩短整体同步耗时。结果自动合并为 list[dict]。
        """
        async def combined_runner(task_id: str, on_progress_cb) -> list[dict]:
            gathered = await asyncio.gather(
                *[r(task_id, on_progress_cb) for r in runners],
                return_exceptions=True,
            )
            combined: list[dict] = []
            for res in gathered:
                if isinstance(res, BaseException):
                    logger.error(f"[SyncTask:{task_id}] 并发子 runner 异常: {res}")
                    combined.append({"success": False, "error": str(res)[:500]})
                elif isinstance(res, list):
                    combined.extend(res)
            return combined

        return await self.submit(
            trigger_type=trigger_type,
            triggered_by=triggered_by,
            data_types=data_types,
            time_ranges=time_ranges,
            time_ranges_display=time_ranges_display,
            runner_coro=combined_runner,
            force=force,
            ignore_cooldown=ignore_cooldown,
        )

    async def _run_background(self, task: SyncTask, runner_coro, on_progress):
        try:
            results = await runner_coro(task.task_id, on_progress)
            task.results = results
            # 判断整体 status
            if all(r.get("success") for r in results):
                task.status = "success"
            elif any(r.get("success") for r in results):
                task.status = "partial"
            else:
                task.status = "failed"
        except Exception as e:
            logger.exception(f"[SyncTask:{task.task_id}] 后台执行异常")
            task.status = "failed"
            task.error = str(e)[:2000]
        finally:
            task.finished_at = datetime.now(TZ_CN)
            # 更新最近成功时间(只要有一个源成功就记,保守策略)
            if task.results and any(r.get("success") for r in task.results):
                self._last_success_at = task.finished_at
            async with self._lock:
                self._executing = False
                # _current_task 保留到下一个任务开始前,方便查询
            # 缓存失效兜底: 同步完成后统一失效看板缓存 (含 BMS/SCRM 并发合并任务),
            # 避免同步后仍读到旧聚合结果。complaint:* 一并失效(BMS 投诉工单同步后会变)。
            # reception:* (#228): SCRM 同步后接待看板缓存失效, 否则接待分析页读到旧聚合。
            try:
                from app.core.redis_client import cache_invalidate_recent_days
                for pat in ("today:*", "refund:*", "complaint:*", "reception:*"):
                    cache_invalidate_recent_days(pat)
            except Exception as ce:  # noqa: BLE001
                logger.debug(f"缓存失效兜底失败(不影响同步): {ce}")
            # 同步后预热今日看板: 数据已更新, 主动填充缓存让下次访问命中热缓存。
            # 仅在有源成功(success/partial)时预热, 全失败则跳过(无新数据)。
            if task.status in ("success", "partial"):
                try:
                    from app.services.cache_warmer import warm_after_sync
                    wt = asyncio.create_task(asyncio.to_thread(warm_after_sync))
                    self._warm_bg_tasks.add(wt)
                    wt.add_done_callback(self._warm_bg_tasks.discard)
                except Exception as we:  # noqa: BLE001  预热调度失败不影响同步
                    logger.debug(f"同步后预热调度失败(不影响): {we}")
                # #129 高频同步成功后刷新当日日报(warm_on_sync_complete 内部含单飞锁
                # + daily 20min 节流, weekly/monthly 不触发); 后台线程执行不阻塞同步收尾。
                try:
                    from app.services.ai_report_worker import warm_on_sync_complete
                    at = asyncio.create_task(asyncio.to_thread(warm_on_sync_complete))
                    self._warm_bg_tasks.add(at)
                    at.add_done_callback(self._warm_bg_tasks.discard)
                except Exception as ae:  # noqa: BLE001  AI 刷新失败不影响同步
                    logger.debug(f"同步后 AI 日报刷新调度失败(不影响): {ae}")

    async def _on_progress(self, task_id: str, data_type: str, phase: str, data: Any):
        """进度回调写入 task 状态"""
        task = self.get_task(task_id)
        if not task:
            return
        # 构造可读 message
        phase_zh = {
            "pending": "排队中",
            "fetching": "正在抓取",
            "waiting": "等待 BMS 冷却中",
            "fetched": "抓取完成",
            "importing": "正在导入",
            "imported": "导入完成",
            "failed": "失败",
            "skipped": "跳过",
            "done": "抓取完成",
        }.get(phase, phase)

        detail = None
        msg = phase_zh
        if isinstance(data, dict):
            detail = data
            if "success" in data:
                if not data["success"] and data.get("error"):
                    msg = f"{phase_zh}: {data['error'][:120]}"
            if data.get("file"):
                msg = f"{phase_zh}: {data['file']}"
            if data.get("name"):
                msg = f"{phase_zh}: {data['name']}"
        progress = SyncTaskProgress(
            data_type=data_type,
            phase=phase if phase != "done" else "fetched",
            message=msg,
            detail=detail,
        )
        task.per_source[data_type] = progress
        # recent_logs 追加
        task.recent_logs.append({
            "t": datetime.now(TZ_CN).strftime("%H:%M:%S"),
            "dt": data_type,
            "phase": progress.phase,
            "msg": msg,
        })
        if len(task.recent_logs) > 200:
            task.recent_logs = task.recent_logs[-200:]

    def _prepend_history(self, task: SyncTask):
        self._history.insert(0, task)
        if len(self._history) > MAX_HISTORY:
            self._history = self._history[:MAX_HISTORY]


# 全局单例
sync_task_manager = SyncTaskManager()

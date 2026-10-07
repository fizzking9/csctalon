"""LLM 调度仲裁层 (工单 #123 简化版 → 第三阶段 #127 真优先级队列)

#123(已关): 仅活跃计数 + /ai/queue-status 忙时提示, `queue_length` 恒为 0(asyncio 直接并发)。

#127(本实现)升级为**真优先级队列 + 抢占**, 文件头原"未来扩展"落地:

优先级定义(以 #127 工单正文为准, 数值越小越优先):
  - L0(最高) = 后台定时预生成(sync-triggered 等, generate_for_template 默认)
  - L1(中)   = admin 手动触发(`trigger` 按钮)
  - L2(最低) = 用户实时问答(assistant drawer)
  - 抢占规则: 高优先级任务提交时, 取消(cancel)当前运行的低优先级任务。

设计(进程级线程安全, 单 worker 跨 asyncio 循环可用):
- 门闸(_Gate, threading.Condition): 任一时刻仅 1 个 LLM 调用在飞 → 互斥锁仍有效,
  单 ollama 端点不被并发打爆。
- 队列: 等待者按 (level, FIFO seq) 升序; 运行/排队分离, queue_length 真实化(不再恒 0)。
- 抢占: 更高优先级到达时, 经 `holder.loop.call_soon_threadsafe(holder.task.cancel)`
  异步中断在飞低优先级协程(打断其 httpx 请求), 新任务立即接管(不等待被抢占者退出)。
  被抢占者在 release 时凭 seq 认出自己不再是 holder → 幂等释放。
- 可恢复性: 不实现"现场恢复"(超出 #127 且属过度工程); 上层捕获 asyncio.CancelledError
  置"被抢占, 可重试"提示 → 满足验收"可恢复/可重试"(取"可重试")。

多 provider 分流(验收第 4 条): 沿用 #114 llm_client._call_multi_provider 单请求内按
priority 遍历兜底 —— 本层只对"一次 LLM 任务"做队列/抢占, provider 兜底留在 llm_client。
"每 provider 独立并发队列"对单一 qwen 端点无分流收益, 本期不实现(内存决策, 不过度工程)。

db 线程安全说明: acquire/release 用 asyncio.to_thread 走线程桥, 但真正的 LLM 协程
(chat_completion + 其内部 db 调用)仍在**调用方线程自己的事件循环**上执行 —— 保持
db session 不跨线程(单 worker + 各调用点自建 SessionLocal), 与既有行为一致。

对外 API(原 register/deregister 已升级为 async acquire/release):
  - await acquire(level, name=None) -> handle  提交任务并按优先级排队, 上位后返回
  - await release(handle)                      结束占用(幂等)
  - queue_status() -> dict                     /ai/queue-status 数据源

调用点(#202 起全链路收口, 不再存在绕过本层的直调):
  - ai.py::_runner                        用户实时问答(主答)  → acquire(2)
  - ai_extract_service.extract_section    实时问答·报告摘取   → acquire(2)
  - ai_semantic_matcher.semantic_match    实时问答·语义匹配   → acquire(2)
  - ai_time_parser.llm_parse_time_intent  实时问答·时间兜底   → acquire(2)
  - prompt_config_service._smoke_once     admin 金丝雀冒烟    → acquire(1)
  - governance_detector.scan_semantic_anomaly  凌晨完整体检 D7 → acquire(0)
  - ai_report_worker.generate_for_template     后台预生成      → acquire(0); admin 触发 → acquire(1)
门闸 key 约定: 调用方给 chat_completion 指定了 provider 链的, acquire 传链首
(同一首选 provider 互斥); 未指定 provider 的(内部全链遍历)传 None 走 default 门闸。
"""

import asyncio
import heapq
import itertools
import logging
import threading
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# 优先级: 数值越小越优先(#127 工单正文)
LEVEL_NAMES = {
    0: "L0 后台预生成",
    1: "L1 admin触发",
    2: "L2 用户实时",
}
_ALLOWED_LEVELS = (0, 1, 2)

# 预计单次 LLM 调用平均耗时(ms; 基于 #115/#117 实测 ~35s)
_EST_AVG_MS = 35000

# 忙判定阈值(兼容 #126 前端)
_BUSY_ACTIVE_THRESHOLD = 2
_BUSY_WAIT_S = 30

_TZ_CN = timezone(timedelta(hours=8))


class _Job:
    """一个已排队的 LLM 调用任务(进程级快照; 不持 db 引用, 避免跨线程)。"""

    __slots__ = ("level", "name", "loop", "task", "seq", "enqueued_at", "provider_code")

    def __init__(self, level: int, name: str, loop, task, provider_code: str):
        self.level = level
        self.name = name
        self.loop = loop            # 该任务所在事件循环(用于跨循环投递 cancel)
        self.task = task            # 该任务所在 asyncio.Task(被抢占时 cancel 它)
        self.seq: int = -1          # FIFO 序号(同 level 内先到先服务)
        self.enqueued_at = datetime.now()  # naive wall(与 ai_task_manager 一致, 便于求排队时长)
        self.provider_code = provider_code  # 归属 provider 独立门闸(#127 多 provider 分流)


def _job_token(job: _Job) -> str:
    return f"{job.seq}:{id(job):x}"


class _Gate:
    """进程级优先门闸: 任一时刻仅一个 holder 在飞, 等待者按 (level, seq) 出队。"""

    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._holder: _Job | None = None
        self._holder_since: datetime | None = None
        self._waiting: list[tuple] = []  # heap of (level, seq, token, _Job); 数值越小越优先
        self._seq = itertools.count()

    # -- 以下在 threadpool 线程中执行(_grant/_free), 用 threading 互斥, 不占事件循环 --
    def register(self, level: int, name: str, loop, task, provider_code: str) -> _Job:
        level = level if level in _ALLOWED_LEVELS else 2
        job = _Job(level, name, loop, task, provider_code)
        with self._cv:
            job.seq = next(self._seq)
            heapq.heappush(self._waiting, (job.level, job.seq, _job_token(job), job))
            self._give_slot()
            # 排队等待自身上位为 holder(可能被比自己更高优先级的任务插队)
            while self._holder is not job:
                self._cv.wait()
        return job

    def release(self, job: _Job) -> None:
        with self._cv:
            if self._holder is job:
                self._holder = None
                self._holder_since = None
            token = _job_token(job)
            self._waiting = [it for it in self._waiting if it[2] != token]
            heapq.heapify(self._waiting)
            self._give_slot()
            self._cv.notify_all()

    def _give_slot(self) -> None:
        """让更高优先级的任务抢占在飞的低优先级任务, 并给下一个任务开位。

        仅在"新 holder 为空"时提升; 抢占场景显式置 holder=None 后再提升。
        """
        if self._holder is not None and self._waiting:
            # 队首等待者的优先级数值小于当前 holder(即更高优先) → 抢占 holder
            if self._waiting[0][0] < self._holder.level:
                self._preempt(self._holder)
                self._holder = None  # 清除, 交 _promote_top 接管给更高优先者
            else:
                return
        if self._holder is None and self._waiting:
            self._promote_top()

    def _promote_top(self) -> None:
        if self._holder is not None or not self._waiting:
            return
        _, _, _, job = heapq.heappop(self._waiting)
        self._holder = job
        self._holder_since = datetime.now()

    def _preempt(self, job: _Job) -> None:
        """跨循环投递取消: 通知被抢占者的协程中断, 新任务随即接管(不等待其退出)。"""
        try:
            if job.loop is not None and job.task is not None and not job.task.done():
                job.loop.call_soon_threadsafe(job.task.cancel)
                logger.info("llm_scheduler 抢占: %s(%s) 被更高优先级抢占, cancel 已投递",
                            LEVEL_NAMES.get(job.level, job.level), job.name)
            else:
                logger.warning("llm_scheduler 抢占目标无 loop/task 不可取消: %s(%s)",
                               LEVEL_NAMES.get(job.level, job.level), job.name)
        except Exception:  # noqa: BLE001  抢占投递失败仅 log(不阻塞新任务)
            logger.exception("llm_scheduler 抢占 cancel 投递异常")

    def snapshot(self) -> dict:
        with self._cv:
            return {
                "holder": self._holder,
                "holder_since": self._holder_since,
                "waiting": list(self._waiting),
            }


# 多 provider 独立门闸(#127 分流): 每个 provider_code 一个 _Gate,
# 同 provider 内排队+抢占, 不同 provider 并行互不阻塞。
# provider_code=None 或 "default" 走默认门闸(用户实时路径未指定 provider 时)。
_gates: dict[str, _Gate] = {}
_gates_lock = threading.Lock()
_DEFAULT_GATE = "default"


def _get_gate(provider_code: str | None) -> _Gate:
    """按 provider_code 取/建独立门闸(线程安全)。"""
    key = provider_code or _DEFAULT_GATE
    with _gates_lock:
        g = _gates.get(key)
        if g is None:
            g = _Gate()
            _gates[key] = g
        return g


# ─────────────────────────────────────────────────────────────────────
# 对外 API(调用点均改为 async acquire/release)
# ─────────────────────────────────────────────────────────────────────
async def acquire(level: int = 2, *, name: str | None = None,
                  provider_code: str | None = None) -> _Job:
    """提交一次 LLM 任务进优先队列, 等到自身上位为运行者后返回 handle。

    provider_code(#127 多 provider 分流): 指定后进入该 provider 的独立门闸,
    与其它 provider 并行不互斥; None 走 default 门闸(用户实时路径)。
    抢占语义: 若自身上位后被更高优先级任务抢占, 本协程会被 task.cancel() 中断,
    调用方应捕获 asyncio.CancelledError 置"被抢占, 可重试"。
    """
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    _name = name or LEVEL_NAMES.get(level, f"L{level}")
    return await asyncio.to_thread(_grant, level, _name, loop, task, provider_code)


def _grant(level: int, name: str, loop, task, provider_code: str | None) -> _Job:
    """线程桥: 在 threadpool 线程中执行 blocking 入队(不占事件循环)。"""
    gate = _get_gate(provider_code)
    return gate.register(level, name, loop, task, provider_code or _DEFAULT_GATE)


async def release(job: _Job) -> None:
    """结束一次占用, 允许队列下一任务上位。幂等(已被抢占替换时 no-op)。"""
    await asyncio.to_thread(_free, job)


def _free(job: _Job) -> None:
    gate = _get_gate(job.provider_code)
    gate.release(job)


def queue_status() -> dict:
    """返回当前队列状态(供 /ai/queue-status 路由)。

    兼容 #126 前端键(active_count/queue_length/est_wait_s/is_busy/threshold_*/timestamp),
    新增真队列可见性: running_level/running_name/run_s/queued(逐项 level+waited_s)。
    #127 多 provider: 汇总所有 provider 门闸; providers 列出每个 provider 的在飞/排队。

    est_wait_s: 对"新增一位 L2 用户"的粗估等待秒数 = 前方位置数(在飞+排队)×平均耗时。
    is_busy: 有排队 或 总活跃 >= 2(#126 兼容语义)。
    """
    with _gates_lock:
        gate_items = list(_gates.items())

    now_naive = datetime.now()
    total_running = 0
    total_queue = 0
    first_holder: _Job | None = None
    first_holder_since: datetime | None = None
    queued: list[dict] = []
    provider_breakdown: dict[str, dict] = {}

    for pcode, gate in gate_items:
        snap = gate.snapshot()
        holder = snap["holder"]
        waiting = snap["waiting"]
        running = 1 if holder is not None else 0
        total_running += running
        total_queue += len(waiting)
        # 全局 running_level/name 取第一个有 holder 的 provider(展示用)
        if holder is not None and first_holder is None:
            first_holder = holder
            first_holder_since = snap["holder_since"]
        for level, _seq, _tok, job in sorted(waiting, key=lambda x: (x[0], x[1])):
            queued.append({
                "level": level,
                "name": job.name,
                "label": LEVEL_NAMES.get(level, f"L{level}"),
                "waited_s": max(0, int((now_naive - job.enqueued_at).total_seconds())),
                "provider_code": pcode,
            })
        provider_breakdown[pcode] = {
            "running": running,
            "queue_length": len(waiting),
            "running_name": holder.name if holder else None,
            "running_level": holder.level if holder else None,
        }

    active_count = total_running + total_queue
    positions_ahead = total_queue + total_running
    est_wait_s = int(positions_ahead * (_EST_AVG_MS / 1000))
    is_busy = total_queue >= 1 or active_count >= _BUSY_ACTIVE_THRESHOLD

    run_s = None
    if first_holder is not None and first_holder_since is not None:
        run_s = max(0, int((now_naive - first_holder_since).total_seconds()))

    return {
        "active_count": active_count,
        "queue_length": total_queue,   # 第三阶段: 真实排队数(不再恒 0)
        "est_wait_s": est_wait_s,
        "is_busy": is_busy,
        "threshold_active": _BUSY_ACTIVE_THRESHOLD,
        "threshold_wait_s": _BUSY_WAIT_S,
        # 新增真队列可见性(#127)
        "running_count": total_running,
        "running_level": first_holder.level if first_holder else None,
        "running_name": first_holder.name if first_holder else None,
        "run_s": run_s,
        "queued": queued,
        "providers": provider_breakdown,   # #127 多 provider 分流明细
        "timestamp": datetime.now(_TZ_CN).isoformat(),
    }

"""AI 分析助手 - 内存任务管理器 (A-20260904-003 LOCKED)

一期纯内存(单 worker 保证互斥; 重启丢历史可接受, 不落库/无审计)。
长耗时 LLM 生成几十秒: 提交任务即返回 task_id, 前端轮询 GET /tasks/{id}。

同构参考: sync_task_manager(状态机/单例/历史保留/互斥锁)。
"""

import asyncio
import logging
import random
import string
import time
from dataclasses import dataclass, field
from datetime import datetime

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class AiTask:
    """一次 AI 分析任务(内存态)。"""
    task_id: str
    question: str
    period: str
    date: str | None
    start: str | None = None          # v1.1: 任意区间起点(纯日期, 与 external brief 同源)
    end: str | None = None            # v1.1: 任意区间终点
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: str = "queued"   # queued / running / success / failed
    result_md: str | None = None
    error: str | None = None

    def to_public_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "question": self.question,
            "period": self.period,
            "date": self.date,
            "start": self.start,
            "end": self.end,
            "result_md": self.result_md,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class AiTaskManager:
    """AI 任务单例: 同刻最多 1 个 running, 其余排队; 历史保留 AI_TASK_HISTORY 条。

    由 /api/v1/ai 路由调用:
      task_id = manager.submit(question, period, date, runner_coro_factory)
      manager.get(task_id) / manager.get_latest()
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tasks: dict[str, AiTask] = {}
        self._order: list[str] = []          # 按创建序(用于裁剪历史)

    def _new_task_id(self) -> str:
        ts = time.strftime("%Y%m%d%H%M%S")
        rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"ai_{ts}_{rand}"

    def _prune(self) -> None:
        """超出历史上限裁剪最旧的终态任务。"""
        max_hist = max(1, int(getattr(settings, "AI_TASK_HISTORY", 20) or 20))
        while len(self._order) > max_hist:
            oldest = self._order.pop(0)
            self._tasks.pop(oldest, None)

    def submit(self, question: str, period: str, date: str | None,
               runner, start: str | None = None, end: str | None = None) -> str:
        """创建任务并后台执行。

        runner: 可等待协程工厂 `async def runner(task) -> None`(内部把结果写回 task)。
        start/end: v1.1 任意区间(纯日期); 透传存任务供展示/调试。
        返回 task_id(立即)。
        """
        task_id = self._new_task_id()
        task = AiTask(task_id=task_id, question=question, period=period, date=date,
                      start=start, end=end)
        self._tasks[task_id] = task
        self._order.append(task_id)
        self._prune()

        async def _run():
            # 简单串行: 单 worker + 事件循环, 顺序执行即可; 用锁防并发提交
            task.status = "running"
            task.started_at = datetime.now()
            try:
                await runner(task)
                task.status = "success"
            except Exception as e:  # noqa: BLE001 任务异常置 failed
                logger.exception("ai task %s failed", task_id)
                task.status = "failed"
                task.error = str(e)[:500]
            finally:
                task.finished_at = datetime.now()

        asyncio.create_task(_run())
        return task_id

    def get(self, task_id: str) -> AiTask | None:
        t = self._tasks.get(task_id)
        if t is None:
            return None
        # 终态保留 TTL 后清除(懒清理)
        ttl = int(getattr(settings, "AI_TASK_TTL_S", 600) or 600)
        if t.status in ("success", "failed") and t.finished_at:
            import datetime as _dt
            # datetime.now 已 tz-naive, 直接用 wall time 比较
            now = _dt.datetime.now()
            # finished_at 也是 naive(datetime.now), 直接相减
            if (now - t.finished_at).total_seconds() > ttl:
                self._tasks.pop(task_id, None)
                if task_id in self._order:
                    self._order.remove(task_id)
                return None
        return t

    def get_latest(self) -> AiTask | None:
        for task_id in reversed(self._order):
            t = self._tasks.get(task_id)
            if t:
                return t
        return None


ai_task_manager = AiTaskManager()

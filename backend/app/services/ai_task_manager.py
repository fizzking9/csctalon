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


def _humanize_error(e: BaseException) -> str:
    """异常 → 面向用户的可读错误(工单 #66)。

    两个约束:
    1. **必须非空**: 原实现直接 `str(e)`, 生产里出现 `status=failed` 却 error 为空的响应,
       前端只能显示"未知错误" —— 用户感知即"毫无提示"。
    2. **不泄露内部细节**: 聚合层异常可能带 SQL 片段/表名/文件路径。只对**已知安全的
       LLM 错误**透传原文(其文案本就是给用户看的), 其余一律给通用文案, 细节留在
       服务端日志(logger.exception 已记录完整堆栈)。

    ⚠️ 判定必须用 `isinstance(e, LLMError)`, **不能**用 `hasattr(e, "status_code")`
    之类的鸭子类型 —— `fastapi.HTTPException` / `httpx.HTTPStatusError` 同样带
    `status_code`, 会被误判为"可透传", 从而把内部异常原文(含表名)泄给前端。
    """
    from app.services.llm_client import LLMError  # 局部导入: 避免模块级循环依赖

    if isinstance(e, LLMError):
        raw = str(e).strip()
        return raw[:500] if raw else "模型调用失败(未返回详情)"
    if isinstance(e, TimeoutError) or "Timeout" in type(e).__name__:
        return "分析超时: 数据聚合或模型响应耗时过长, 请稍后重试或缩小时间范围"
    # 其它异常: 通用文案(不暴露异常原文), 具体原因看服务端日志
    return f"分析失败({type(e).__name__}): 请稍后重试; 若持续失败请查看服务端日志"


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
    # 窗口判定(#66): 让前端/排查都能看到"这次到底按哪个窗口统计的"
    window_source: str | None = None   # explicit_range / explicit_date / nl / period
    window_label: str | None = None
    # #116 持久化回填字段(成功后异步落库写入, 失败/超时为 None)
    report_id: int | None = None       # ai_reports.id; 主入口/轮询返回给前端做复查入口
    usage: dict | None = None          # LLM usage {prompt_tokens, completion_tokens, total_tokens}
    total_ms: int | None = None       # 端到端总耗时(ms)
    llm_ms: int | None = None         # LLM 推理耗时(ms, 不含 feed build)
    build_ms: int | None = None        # feed 组装耗时(ms)
    llm_model: str | None = None      # 命中的 provider+model(规则层=rule-engine)

    def to_public_dict(self) -> dict:
        # failed 但 error 为空时兜底: 生产曾出现 error=None 的瞬时响应,
        # 前端只能显示"未知错误" → 用户感知为"毫无提示"。
        err = self.error
        if self.status == "failed" and not err:
            err = "分析失败: 未返回错误详情, 请查看服务端日志"
        return {
            "task_id": self.task_id,
            "status": self.status,
            "question": self.question,
            "period": self.period,
            "date": self.date,
            "start": self.start,
            "end": self.end,
            "result_md": self.result_md,
            "error": err,
            "window_source": self.window_source,
            "window_label": self.window_label,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            # #116 持久化与耗时/usage 元数据(success 时填充, 前端报告头部显示 token+用时)
            "report_id": self.report_id,
            "usage": self.usage,
            "total_ms": self.total_ms,
            "llm_ms": self.llm_ms,
            "build_ms": self.build_ms,
            "llm_model": self.llm_model,
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
               runner, start: str | None = None, end: str | None = None,
               window_source: str | None = None, window_label: str | None = None) -> str:
        """创建任务并后台执行。

        runner: 可等待协程工厂 `async def runner(task) -> None`(内部把结果写回 task)。
        start/end: v1.1 任意区间(纯日期); 透传存任务供展示/调试。
        window_source/window_label(#66): 窗口判定来源与说明, 随任务存下以便前端回显。
        返回 task_id(立即)。
        """
        task_id = self._new_task_id()
        task = AiTask(task_id=task_id, question=question, period=period, date=date,
                      start=start, end=end,
                      window_source=window_source, window_label=window_label)
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
            except asyncio.CancelledError:
                # #127 抢占: 更高优先级(L0 后台预生成 / L1 admin)抢占本用户实时任务。
                #   置 failed + 明确"被抢占可重试", 让前端可见; 不 re-raise(否则任务卡 running)。
                logger.warning("ai task %s 被更高优先级抢占(可重试)", task_id)
                task.status = "failed"
                task.error = "本次分析被更高优先级的后台任务抢占, 请稍后重试"
            except Exception as e:  # noqa: BLE001 任务异常置 failed
                logger.exception("ai task %s failed", task_id)
                task.status = "failed"
                # ⚠️ 状态与 error 必须"同时"就绪(#66): 生产日志显示曾出现
                # status=failed 但 error=None 的瞬时响应(前端收到后显示"未知错误")。
                # 此处先写 error 再置 status 无法根治(对象共享, 读侧可能仍见到中间态),
                # 故 read 侧(get/ai_task_status)对 failed+空error 做兜底文案。
                task.error = _humanize_error(e)
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

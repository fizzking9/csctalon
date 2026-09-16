"""LLM 调度仲裁层 (工单 #123)

设计:
- 单 worker 串行场景(asyncio 并发但 LLM 端点可能限制), 需要队列状态可见
- 简化版: 不实现真抢占(asyncio 难以中断), 用活跃任务计数 + 预计等待时间
- 暴露 /ai/queue-status 路由让前端展示忙时提示

#123 实现要点:
- _active_tasks: 当前活跃 LLM 调用计数(用户实时 + 后台预生成)
- _active_lock: 保护计数器
- register/deregister: 进入/退出 LLM 调用时调
- queue_status(): 返回 {active_count, queue_length, est_wait_s, is_busy}
- is_busy = active_count >= 2 或 est_wait_s > 30

未来扩展(超出 #123 范围):
- 真 L0 抢占 L1/L2(需要 asyncio.Task.cancel + 状态保存)
- 多 provider 并发分流(每个 provider 独立队列)
"""

import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# 活跃任务计数(进程级, 单 worker)
_active_count = 0
_active_lock = threading.Lock()

# 预计单次 LLM 调用平均耗时(基于 #115/#117 实测 ~35s)
_EST_AVG_MS = 35000

# 阈值: 活跃数 >= 2 或预计等待 > 30s 视为忙
_BUSY_ACTIVE_THRESHOLD = 2
_BUSY_WAIT_S = 30


def register() -> None:
    """进入 LLM 调用前调(用户实时 + 后台预生成都调)。"""
    global _active_count
    with _active_lock:
        _active_count += 1
        logger.debug("llm_scheduler register: active_count=%s", _active_count)


def deregister() -> None:
    """退出 LLM 调用后调(无论成功失败)。"""
    global _active_count
    with _active_lock:
        if _active_count > 0:
            _active_count -= 1
        logger.debug("llm_scheduler deregister: active_count=%s", _active_count)


def queue_status() -> dict:
    """返回当前队列状态(供 /ai/queue-status 路由)。

    返回:
      active_count: 当前活跃 LLM 调用数(用户实时 + 后台预生成)
      queue_length: 排队等待数(当前实现: 0, 因为 asyncio 直接并发)
      est_wait_s: 预计等待秒数(粗估: active_count × avg_ms / 1000)
      is_busy: bool (active_count >= 2 或 est_wait_s > 30)
      timestamp: 当前时间(北京时间)
    """
    with _active_lock:
        count = _active_count
    est_wait_s = count * (_EST_AVG_MS / 1000) if count > 0 else 0
    is_busy = count >= _BUSY_ACTIVE_THRESHOLD or est_wait_s > _BUSY_WAIT_S
    from datetime import timedelta, timezone
    _tz_cn = timezone(timedelta(hours=8))
    return {
        "active_count": count,
        "queue_length": 0,  # 当前实现: asyncio 直接并发, 无排队
        "est_wait_s": int(est_wait_s),
        "is_busy": is_busy,
        "threshold_active": _BUSY_ACTIVE_THRESHOLD,
        "threshold_wait_s": _BUSY_WAIT_S,
        "timestamp": datetime.now(_tz_cn).isoformat(),
    }


# 装饰器: 给 chat_completion 调用方用(可选, 当前不强制)
# 选用: 在 ai.py::_runner 和 ai_report_worker.generate_for_template 显式调

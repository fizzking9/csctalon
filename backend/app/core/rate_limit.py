"""登录限速: 防暴力破解(M3)

简单内存滑动窗口, 按 IP 限流。部署统一为单 worker(compose / Dockerfile /
Dockerfile.patch 均 -w 1, 见 #212), 本进程内计数即全局限流; 若将来改为多 worker,
每进程各持一份计数会使限流变松, 届时须改用 Redis 做跨进程防护(v0.7 不做多 worker)。
"""
import time
from collections import defaultdict
from threading import Lock

# 每 IP 在 WINDOW 秒内最多 MAX_ATTEMPTS 次登录尝试(含成功/失败)
_WINDOW = 300  # 5 分钟
_MAX_ATTEMPTS = 10  # 5 分钟内最多 10 次(正常用户足够, 爆破会迅速触顶)

_attempts: dict[str, list[float]] = defaultdict(list)
_lock = Lock()


def check_login_rate(ip: str | None) -> tuple[bool, int]:
    """检查 IP 是否超过登录尝试限额。

    每次调用即记录一次尝试。返回 (是否允许, 重试等待秒数)。
    超额时返回 False 并给出距窗口过期的等待秒数。
    """
    if not ip:
        return True, 0  # 取不到 IP 时不限流(不阻塞正常请求)
    now = time.monotonic()
    with _lock:
        # 清理过期记录, 保留窗口内的
        bucket = [t for t in _attempts[ip] if now - t < _WINDOW]
        if len(bucket) >= _MAX_ATTEMPTS:
            retry = int(_WINDOW - (now - bucket[0])) + 1
            return False, max(retry, 1)
        bucket.append(now)
        _attempts[ip] = bucket
        return True, 0


def reset_login_rate(ip: str | None) -> None:
    """重置某 IP 的计数(账号锁定场景或维护时用, 登录成功不自动清——防爆破者
    偶发命中后重置窗口)。窗口自然过期, 无需手动清。"""
    if not ip:
        return
    with _lock:
        _attempts.pop(ip, None)

"""Redis 客户端封装 + 简单缓存工具

设计要点:
- 同步客户端(redis.Redis): analytics 等同步路由直接用
- 全局单例 + get_redis() 依赖注入, 避免重复连接
- Redis 不可用时静默降级(返回 None), 不阻塞业务请求
- 缓存序列化用 JSON, 兼容 FastAPI 响应模型
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

import redis
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)

# 全局单例(进程级复用连接池)
_redis_client: redis.Redis | None = None


def get_redis() -> redis.Redis | None:
    """获取 Redis 客户端单例; 连接失败时返回 None(业务降级到无缓存)"""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,   # 直接返回 str, 便于 JSON 反序列化
            socket_connect_timeout=2,
            socket_timeout=2,
            retry_on_timeout=False,
        )
        client.ping()
        _redis_client = client
        logger.info("Redis 连接成功: %s", settings.redis_url)
        return _redis_client
    except RedisError as e:
        # Redis 不可用不影响业务, 仅记日志, 后续走无缓存路径
        logger.warning("Redis 连接失败, 缓存将不可用: %s", e)
        _redis_client = None
        return None


def cache_get_json(key: str) -> Any | None:
    """读缓存; 命中返回反序列化后的对象, 未命中或异常返回 None"""
    r = get_redis()
    if r is None:
        return None
    try:
        raw = r.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except (RedisError, json.JSONDecodeError, TypeError) as e:
        logger.debug("缓存读失败 key=%s: %s", key, e)
        return None


def cache_set_json(key: str, value: Any, ttl: int = 60) -> bool:
    """写缓存; ttl 秒后过期. 失败静默(不影响业务)"""
    r = get_redis()
    if r is None:
        return False
    try:
        r.setex(key, ttl, json.dumps(value, default=str, ensure_ascii=False))
        return True
    except (RedisError, TypeError) as e:
        logger.debug("缓存写失败 key=%s: %s", key, e)
        return False


def cache_invalidate_pattern(pattern: str) -> int:
    """按 pattern 批量失效缓存(如同步后清空 today:* 所有缓存); 返回删除条数"""
    r = get_redis()
    if r is None:
        return 0
    try:
        # SCAN 避免大 key 阻塞 (不用 KEYS)
        deleted = 0
        for key in r.scan_iter(match=pattern, count=500):
            r.delete(key)
            deleted += 1
        return deleted
    except RedisError as e:
        logger.debug("缓存批量失效失败 pattern=%s: %s", pattern, e)
        return 0


def cached_call(key: str, ttl: int, fn: Callable, *args, **kwargs) -> Any:
    """带 Redis 缓存的函数调用 (同步版)

    用法:
        return R(data=cached_call(f"today:kpi:{date}:{start}:{end}", 60,
                                  A.today_kpi, db, date, start, end))

    - 命中缓存: 直接返回反序列化结果, 跳过 fn 调用
    - 未命中: 调用 fn, 结果非 None 则写缓存, 返回结果
    - Redis 不可用: 静默降级为直接调用 fn (不影响业务)
    """
    hit = cache_get_json(key)
    if hit is not None:
        return hit
    result = fn(*args, **kwargs)
    if result is not None:
        cache_set_json(key, result, ttl)
    return result


def cached_json(key_fn: Callable[..., str], ttl: int = 60) -> Callable:
    """装饰器: 给 FastAPI 同步路由加 JSON 缓存

    用法:
        @router.get("/today/kpi")
        @cached_json(lambda date, **kw: f"today:kpi:{date}:{kw}")
        def today_kpi(date: str = None):
            ...

    注意: 装饰器需在 @router.get 之下(先应用), 这样 FastAPI 拿到的是包装后的函数.
    """

    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            try:
                key = key_fn(*args, **kwargs)
            except Exception:
                key = None
            if key:
                hit = cache_get_json(key)
                if hit is not None:
                    return hit
            result = func(*args, **kwargs)
            if key and result is not None:
                cache_set_json(key, result, ttl)
            return result

        # 保留原函数元数据(FastAPI 依赖注入需要)
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        wrapper.__wrapped__ = func
        return wrapper

    return decorator

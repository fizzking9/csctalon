"""Redis 客户端封装 + 简单缓存工具

设计要点:
- 同步客户端(redis.Redis): analytics 等同步路由直接用
- 全局单例 + get_redis() 依赖注入, 避免重复连接
- Redis 不可用时静默降级(返回 None), 不阻塞业务请求
- 缓存序列化用 JSON, 兼容 FastAPI 响应模型
- 同步写入高峰期: 自动延长缓存 TTL 防 DB 缓存击穿 + 雪崩

日期感知 TTL(2026-08-31 新增):
  历史窗口(end < 今天)数据已定型不再变化, TTL 拉长到 24h, 避免用户切历史日期
  反复冷查询(冷聚合 2~4s); 含今天的窗口(数据还在同步变动)保持短 TTL。
  数据一致性兜底: 每次同步完成后仍会失效「今日+近 N 天」缓存(见
  cache_invalidate_recent_days), 历史 TTL 拉长不会造成数据陈旧。
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import redis
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)

# 东八区(与全项目对齐)
TZ_CN = timezone(timedelta(hours=8))

# 日期感知 TTL(秒): 含今天窗口保持短 TTL; 纯历史窗口拉长到 24h(数据已定型)
HISTORY_TTL_SECONDS = 24 * 3600
# 同步后失效保留窗口(天): 仅失效「含今天 + 近 N 天」的缓存, 更早的历史缓存长期驻留
# (历史 TTL 已拉到 24h, 同步不涉及历史窗口, 无需清掉)
SYNC_INVALIDATE_RECENT_DAYS = 7
# 缓存 key 中的日期片段模式(用于按窗口范围失效)
_CACHE_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

# 全局单例(进程级复用连接池)
_redis_client: redis.Redis | None = None

# 标记"同步写入高峰期"的 Redis key(含 TTL 兜底自动过期,防进程崩溃后永远不清除)
SYNC_WRITING_FLAG_KEY = "sys:sync_writing_active"
SYNC_WRITING_FLAG_DEFAULT_TTL = 600  # 10 分钟兜底
# 写入高峰期缓存 TTL 系数 & 上下限: 原 TTL × WRITING_TTL_MULTIPLY, clamp [300, 3600]
WRITING_TTL_MULTIPLY = 5
WRITING_TTL_MIN = 300
WRITING_TTL_MAX = 3600


def _sanitize_redis_url(url: str) -> str:
    """日志脱敏: redis://:PASSWORD@host:port/db → redis://:***@host:port/db (2026-08-20 审计 SEC-09)"""
    if "://:" in url and "@" in url:
        scheme, rest = url.split("://:", 1)
        if "@" in rest:
            host_part = rest.split("@", 1)[1]
            return f"{scheme}://:***@{host_part}"
    return url


# Redis 连接失败告警节流: get_redis() 失败会高频重试, 每 N 秒最多写一条事务通知避免刷屏
_REDIS_ALERT_INTERVAL = 300  # 5 分钟
_last_redis_alert_ts: float = 0.0


def _record_redis_failure_alert(err: Exception) -> None:
    """Redis 连接失败时写一条 alert 级事务通知(节流 5min/条)。

    Redis 不可用会让所有缓存降级直连 DB(看板/接口变慢), 属重要运维事件,
    需让管理员在「事务通知」中心看到。节流避免 get_redis() 高频重试刷屏。
    延迟 import 避免与 system_event_service/database 的循环导入。
    """
    global _last_redis_alert_ts
    now = time.monotonic()
    if now - _last_redis_alert_ts < _REDIS_ALERT_INTERVAL:
        return
    _last_redis_alert_ts = now
    try:
        from app.core.database import SessionLocal
        from app.services.system_event_service import record_system_event

        db = SessionLocal()
        try:
            record_system_event(
                db,
                category="alert",
                action="redis_unavailable",
                title="Redis 缓存连接失败",
                summary=f"Redis 不可用, 所有缓存已降级直连 DB(看板/接口变慢)。错误: {err}",
                status="failed",
                detail=f"redis_url={_sanitize_redis_url(settings.redis_url)}\nerror: {err}",
            )
        finally:
            db.close()
    except Exception as notify_err:  # noqa: BLE001  DB/通知写入失败不影响业务
        logger.debug("记录 Redis 失败告警失败(不影响业务): %s", notify_err)


def get_redis() -> redis.Redis | None:
    """获取 Redis 客户端单例; 连接失败时返回 None(业务降级到无缓存)"""
    global _redis_client, _last_redis_alert_ts
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
        _last_redis_alert_ts = 0.0  # 恢复后重置节流, 下次失败能立即告警
        # 只打 host:port, 不打密码明文
        logger.info("Redis 连接成功: %s", _sanitize_redis_url(settings.redis_url))
        return _redis_client
    except RedisError as e:
        # Redis 不可用不影响业务, 仅记日志, 后续走无缓存路径
        logger.warning("Redis 连接失败, 缓存将不可用: %s", e)
        _redis_client = None
        _record_redis_failure_alert(e)  # 写事务通知让管理员知晓
        return None


def mark_sync_writing(active: bool, ttl: int = SYNC_WRITING_FLAG_DEFAULT_TTL) -> bool:
    """标记/清除『同步写入高峰期』标志(供缓存策略使用)

    Args:
        active: True=进入写入高峰期(延长缓存TTL防DB打爆); False=结束
        ttl: 兜底TTL(秒),active=True时有效;即使进程崩溃忘了清除,10分钟后也自动失效
    """
    r = get_redis()
    if r is None:
        return False
    try:
        if active:
            r.setex(SYNC_WRITING_FLAG_KEY, ttl, "1")
        else:
            r.delete(SYNC_WRITING_FLAG_KEY)
        return True
    except RedisError as e:
        logger.debug("mark_sync_writing=%s 失败: %s", active, e)
        return False


def is_sync_writing_active() -> bool:
    """当前是否处于同步写入高峰期(Redis flag 或 in-memory 兜底)"""
    r = get_redis()
    if r is None:
        # 回退: 调用 sync_task_manager (运行时单例,无循环导入风险)
        try:
            from app.services.sync_task_manager import sync_task_manager
            return bool(sync_task_manager.is_writing_heavy())
        except Exception:
            return False
    try:
        v = r.get(SYNC_WRITING_FLAG_KEY)
        if v is not None:
            return True
        # Redis 有连接但 flag 未设 → 兜底再查内存单例(跨进程可能有偏差, 但作为降级没问题)
        try:
            from app.services.sync_task_manager import sync_task_manager
            return bool(sync_task_manager.is_writing_heavy())
        except Exception:
            return False
    except RedisError as e:
        logger.debug("is_sync_writing_active 失败: %s", e)
        return False


def _effective_ttl(base_ttl: int) -> int:
    """根据当前是否写入高峰期,算出最终生效的缓存 TTL"""
    if not isinstance(base_ttl, int) or base_ttl <= 0:
        # 未设置 TTL(永久) → 保持
        return base_ttl
    if not is_sync_writing_active():
        return base_ttl
    scaled = int(base_ttl * WRITING_TTL_MULTIPLY)
    clamped = max(WRITING_TTL_MIN, min(WRITING_TTL_MAX, scaled))
    return clamped


def _maybe_extend_hit_ttl(key: str, r: redis.Redis) -> None:
    """写入高峰期: 命中缓存但若剩余 TTL 较短(<60s),续期避免缓存雪崩"""
    if not is_sync_writing_active():
        return
    try:
        ttl_left = r.ttl(key)
        if ttl_left < 60:
            # 续期到 WRITING_TTL_MIN
            r.expire(key, WRITING_TTL_MIN)
    except Exception:
        pass


def parse_date(s: str | None) -> datetime | None:
    """解析 'YYYY-MM-DD' → naive 北京时间 datetime; 无法解析返回 None。

    用于日期感知 TTL: 路由层把 start/end(字符串)传进来判断窗口是否含今天。
    """
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.strptime(s.strip()[:10], "%Y-%m-%d").replace(tzinfo=TZ_CN)
    except (ValueError, TypeError):
        return None


def is_historical_window(start: str | None, end: str | None, *, now: datetime | None = None) -> bool:
    """判断窗口是否为「纯历史」(end 严格早于今天) → 数据已定型, 可长缓存。

    - 未给 end(静态端点/空窗口) → 视为含今天, 走短 TTL(数据可能实时变动)
    - end 落在今天或未来(含今天) → 短 TTL
    - end 严格 < 今天 00:00 → 纯历史 → 长 TTL
    """
    now = now or datetime.now(TZ_CN)
    end_dt = parse_date(end)
    if end_dt is None:
        return False
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return end_dt < today0


def cache_ttl_for(start: str | None, end: str | None, base_ttl: int) -> int:
    """日期感知 TTL: 纯历史窗口 → 24h; 含今天窗口 → 原 base_ttl。

    用法: 路由层 cached_call(key, cache_ttl_for(start, end, 60), ...)
    注意: 若 base_ttl 本身较大(如 120), 历史窗口仍用 HISTORY_TTL 统一 24h。
    """
    if is_historical_window(start, end):
        return HISTORY_TTL_SECONDS
    return base_ttl


def cache_invalidate_recent_days(
    prefix_pattern: str,
    *,
    recent_days: int = SYNC_INVALIDATE_RECENT_DAYS,
) -> int:
    """按前缀失效「含今天 + 近 N 天窗口」的缓存, 保留更早的历史缓存。

    背景(2026-08-31): 此前同步后全量失效 today:*/complaint:*/refund:*, 会把
    「历史窗口缓存(已拉长到 24h TTL)」一并清掉 → 历史缓存永远驻留不住。
    现改为: 仅失效 key 中任一日期落在 [今天-N 天, 今天] 内的条目。
    同步只影响今天/近几天数据, 历史窗口不受影响, 无需清掉。

    实现: SCAN 前缀 → 提取 key 中所有 YYYY-MM-DD 片段 → 若任一在近 N 天内则删除。
    无日期片段的 key(如静态 summary/op:status)视为敏感, 全部失效。
    返回删除条数。
    """
    r = get_redis()
    if r is None:
        return 0
    now = datetime.now(TZ_CN)
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    min_date = today0 - timedelta(days=recent_days)
    deleted = 0
    try:
        for key in r.scan_iter(match=prefix_pattern, count=500):
            dates = _CACHE_DATE_RE.findall(key)
            if not dates:
                # 静态 key(无日期窗口, 如 summary / op:status): 数据实时变动, 全量失效
                r.delete(key)
                deleted += 1
                continue
            keep = True
            for ds in dates:
                try:
                    d = datetime.strptime(ds, "%Y-%m-%d").replace(tzinfo=TZ_CN)
                except ValueError:
                    continue
                if min_date <= d <= now:
                    keep = False  # 命中近 N 天窗口 → 需失效
                    break
            if not keep:
                r.delete(key)
                deleted += 1
    except RedisError as e:
        logger.debug("缓存按近N天失效失败 prefix=%s: %s", prefix_pattern, e)
    return deleted


def cache_get_json(key: str) -> Any | None:
    """读缓存; 命中返回反序列化后的对象, 未命中或异常返回 None"""
    r = get_redis()
    if r is None:
        return None
    try:
        raw = r.get(key)
        if raw is None:
            return None
        # 写入高峰期:命中后顺手续期短 TTL 条目,避免紧接着过期造成DB雪崩
        _maybe_extend_hit_ttl(key, r)
        return json.loads(raw)
    except (RedisError, json.JSONDecodeError, TypeError) as e:
        logger.debug("缓存读失败 key=%s: %s", key, e)
        return None


def _json_default(obj: Any) -> Any:
    """json.dumps 的 default: 处理非原生类型, 避免把 pydantic 响应对象序列化成 repr 字符串。

    背景(2026-08-25 修复投毒 bug): 此前用 default=str, 把 FastAPI 路由返回的 R(pydantic)
    响应对象直接 json.dumps → 落进 Redis 的是 R 的 repr 字符串而非 JSON 对象; 命中缓存
    读出 str 后, cached_json 原样返回, FastAPI response_model=R 校验 str 失败 → 500。
    现统一转成 JSON 安全结构: pydantic→model_dump(mode='json'), 日期→isoformat,
    Decimal→float, set→list, 其余兜底 str(只对真正无法结构化的对象)。
    """
    # pydantic v2 响应对象(R/BaseModel)→ JSON 安全 dict
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    import datetime as _dt
    if isinstance(obj, (_dt.datetime, _dt.date)):
        return obj.isoformat()
    from decimal import Decimal as _Decimal
    if isinstance(obj, _Decimal):
        return float(obj)
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    return str(obj)


def cache_set_json(key: str, value: Any, ttl: int = 60) -> bool:
    """写缓存; ttl 秒后过期. 写入高峰期自动延长 TTL 防 DB 打爆. 失败静默(不影响业务)"""
    r = get_redis()
    if r is None:
        return False
    try:
        eff_ttl = _effective_ttl(ttl)
        if eff_ttl > 0:
            r.setex(key, eff_ttl, json.dumps(value, default=_json_default, ensure_ascii=False))
        else:
            r.set(key, json.dumps(value, default=_json_default, ensure_ascii=False))
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


def cache_stats(pattern: str, *, limit: int = 500) -> dict:
    """统计指定 pattern 下缓存 key 的情况(只读, 不修改)。

    供缓存管理页展示「缓存了哪些 / 各多少 / 即将过期多少」。
    返回:
      available: Redis 是否可用
      count: 匹配到的 key 数(limit 截断, 见 truncated)
      truncated: 是否因 limit 截断(实际可能更多)
      min_ttl / avg_ttl: 有效 TTL(key=-1 无过期 不计入)的统计(秒)
      expiring_count: ttl <= 60 的数量(即将过期, 预热/访问会重算)
      no_ttl_count: ttl == -1(无过期, 异常) 数量
    """
    r = get_redis()
    if r is None:
        return {"available": False, "count": 0, "truncated": False,
                "min_ttl": 0, "avg_ttl": 0, "expiring_count": 0, "no_ttl_count": 0}
    try:
        keys = []
        truncated = False
        for key in r.scan_iter(match=pattern, count=500):
            keys.append(key)
            if len(keys) >= limit:
                truncated = True
                break
        count = len(keys)
        if count == 0:
            return {"available": True, "count": 0, "truncated": truncated,
                    "min_ttl": 0, "avg_ttl": 0, "expiring_count": 0, "no_ttl_count": 0}
        ttls: list[int] = []
        expiring = 0
        no_ttl = 0
        for k in keys:
            try:
                ttl = r.ttl(k)  # -2=不存在, -1=无过期, 否则剩余秒
            except Exception:
                continue
            if not isinstance(ttl, int):
                continue
            if ttl == -1:
                no_ttl += 1
            elif ttl > 0:
                ttls.append(ttl)
                if ttl <= 60:
                    expiring += 1
        return {
            "available": True,
            "count": count,
            "truncated": truncated,
            "min_ttl": min(ttls) if ttls else 0,
            "avg_ttl": round(sum(ttls) / len(ttls)) if ttls else 0,
            "expiring_count": expiring,
            "no_ttl_count": no_ttl,
        }
    except RedisError as e:
        logger.debug("缓存统计失败 pattern=%s: %s", pattern, e)
        return {"available": False, "count": 0, "truncated": False,
                "min_ttl": 0, "avg_ttl": 0, "expiring_count": 0, "no_ttl_count": 0}


def cached_call(key: str, ttl: int, fn: Callable, *args, **kwargs) -> Any:
    """带 Redis 缓存的函数调用 (同步版)

    用法:
        return R(data=cached_call(f"today:kpi:{date}:{start}:{end}", 60,
                                  A.today_kpi, db, date, start, end))

    - 命中缓存: 直接返回反序列化结果, 跳过 fn 调用(写入高峰期会顺带续期短TTL)
    - 未命中: 调用 fn, 结果非 None 则写缓存, 返回结果(写入高峰期 TTL 自动放大)
    - Redis 不可用: 静默降级为直接调用 fn (不影响业务)
    """
    hit = cache_get_json(key)
    if hit is not None:
        return hit
    result = fn(*args, **kwargs)
    if result is not None:
        cache_set_json(key, result, ttl)
    return result


def cached_json(key_fn: Callable[..., str], ttl: int | Callable[..., int] = 60) -> Callable:
    """装饰器: 给 FastAPI 同步路由加 JSON 缓存

    用法:
        @router.get("/today/kpi")
        @cached_json(lambda date, **kw: f"today:kpi:{date}:{kw}")
        def today_kpi(date: str = None):
            ...

    ttl 参数:
      - int: 固定 TTL(默认 60s)
      - callable: 每次写入时调用 `ttl(**kwargs)` 动态计算 TTL(如日期感知:
        `lambda **kw: cache_ttl_for(kw.get('start'), kw.get('end'), 60)`)

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
                # 仅信任 dict 形态: 旧版 cache_set_json(default=str) 曾把 R pydantic
                # 响应对象序列化成 repr 字符串投毒缓存, 命中返 str 会触发
                # response_model=R 校验失败 500; 遇非 dict(历史投毒条目)跳过让其重算自愈。
                if hit is not None and isinstance(hit, dict):
                    return hit
            result = func(*args, **kwargs)
            if key and result is not None:
                eff_ttl = ttl(**kwargs) if callable(ttl) else ttl
                cache_set_json(key, result, eff_ttl)
            return result

        # 保留原函数元数据(FastAPI 依赖注入需要)
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        wrapper.__wrapped__ = func
        return wrapper

    return decorator

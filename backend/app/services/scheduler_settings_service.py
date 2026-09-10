"""调度配置服务 - 高频采集节拍(时段+间隔)读取/校验/写入 + 槽位计算

(#51, A-20260910-006 LOCKED v1.0)

职责:
  1. compute_slots(start, end, interval): **纯函数**, 生成槽位清单 ["HH:MM", ...]
  2. get_settings(db): 读配置(缺失自动补默认行, 兼容旧库)
  3. update_settings(db, ...): 校验 + 写入(非法值 → 400, 不静默取默认)
  4. get_cached_slots(): 带 60s 缓存的槽位读取(供调度器每 tick 调用, 避免每分钟查库)

关键语义(架构 §2.2, 均有单测锁定):
  - 时段左闭右开 [start_hour, end_hour); end=24 表示至当日 24:00, **不含 "24:00" 槽**
  - 尾部残余不足一个间隔 → 丢弃(不产生半槽)
  - 不产生重复槽位
"""

from __future__ import annotations

import logging
import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.scheduler_settings import (
    DEFAULT_END_HOUR,
    DEFAULT_INTERVAL_MINUTES,
    DEFAULT_START_HOUR,
    MAX_INTERVAL_MINUTES,
    MIN_INTERVAL_MINUTES,
    SINGLETON_ID,
    SchedulerSettings,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# 槽位计算(纯函数)
# ─────────────────────────────────────────────────────────────────────
def compute_slots(start_hour: int, end_hour: int, interval_minutes: int) -> list[str]:
    """生成高频槽位清单(纯函数, 供单测锁定边界语义)。

    时段按「分钟」展开后每 interval 切一刀, 起点含、终点不含:
        compute_slots(8, 24, 30)  → ["08:00", "08:30", ..., "23:30"]   (32 项)
        compute_slots(8, 24, 60)  → ["08:00", ..., "23:00"]            (16 项)
        compute_slots(8, 24, 50)  → 尾部残余(<50min)丢弃, 不产生 "23:30"

    ⚠️ 边界: end_hour=24 时最后一槽为 "23:xx", **绝不含 "24:00"** ——
       24:00 即次日 0:00, 应归次日调度, 若在此产生会与次日首槽重复。
    """
    if interval_minutes <= 0:
        return []
    if end_hour <= start_hour:
        return []

    start_min = start_hour * 60
    end_min = end_hour * 60

    slots: list[str] = []
    cur = start_min
    while cur < end_min:
        hh, mm = divmod(cur, 60)
        # 防御: 理论上 cur < 24*60, 不会出现 24:00; 显式跳过以防调用方传 end>24
        if hh >= 24:
            break
        slots.append(f"{hh:02d}:{mm:02d}")
        cur += interval_minutes
    return slots


def validate_settings(start_hour: int, end_hour: int, interval_minutes: int) -> None:
    """校验配置合法性(非法 → HTTPException 400, 不静默取默认)。

    边界与理由见架构 §3.1 校验表。
    """
    if interval_minutes < MIN_INTERVAL_MINUTES:
        raise HTTPException(
            400,
            f"采集间隔下限 {MIN_INTERVAL_MINUTES} 分钟"
            f"(每轮实测约 4.2 分钟, 低于此值上一轮未结束下一轮已到, 必然重叠)",
        )
    if interval_minutes > MAX_INTERVAL_MINUTES:
        raise HTTPException(400, f"采集间隔上限 {MAX_INTERVAL_MINUTES} 分钟")
    if not (0 <= start_hour < end_hour <= 24):
        raise HTTPException(400, "起止时间非法: 需满足 0 ≤ 起始 < 结束 ≤ 24")
    if not compute_slots(start_hour, end_hour, interval_minutes):
        raise HTTPException(400, "该时段与间隔组合产生 0 个采集点, 请调整参数")


# ─────────────────────────────────────────────────────────────────────
# 读写
# ─────────────────────────────────────────────────────────────────────
def get_settings(db: Session) -> SchedulerSettings:
    """读配置行; 缺失则创建默认行(兼容旧库/手工删行场景)。"""
    row = db.scalar(select(SchedulerSettings).where(SchedulerSettings.id == SINGLETON_ID))
    if row is None:
        row = SchedulerSettings(
            id=SINGLETON_ID,
            high_freq_start_hour=DEFAULT_START_HOUR,
            high_freq_end_hour=DEFAULT_END_HOUR,
            high_freq_interval_minutes=DEFAULT_INTERVAL_MINUTES,
            updated_by="system",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info(
            "[scheduler_settings] 初始化默认配置: %s:00~%s:00 每 %s 分钟",
            DEFAULT_START_HOUR, DEFAULT_END_HOUR, DEFAULT_INTERVAL_MINUTES,
        )
    return row


def to_dict(row: SchedulerSettings) -> dict:
    """序列化(含派生槽位信息, 供前端直接展示)。"""
    slots = compute_slots(
        row.high_freq_start_hour, row.high_freq_end_hour, row.high_freq_interval_minutes
    )
    return {
        "start_hour": row.high_freq_start_hour,
        "end_hour": row.high_freq_end_hour,
        "interval_minutes": row.high_freq_interval_minutes,
        "slots": slots,
        "slot_count": len(slots),
        "runs_per_day": len(slots),
        "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def update_settings(
    db: Session,
    *,
    start_hour: int,
    end_hour: int,
    interval_minutes: int,
    actor: str | None = None,
) -> dict:
    """校验并写入配置(非法 → 400)。返回序列化结果。"""
    validate_settings(start_hour, end_hour, interval_minutes)
    row = get_settings(db)
    row.high_freq_start_hour = start_hour
    row.high_freq_end_hour = end_hour
    row.high_freq_interval_minutes = interval_minutes
    row.updated_by = (actor or "")[:64] or None
    db.commit()
    db.refresh(row)
    logger.info(
        "[scheduler_settings] 配置更新(by=%s): %s:00~%s:00 每 %s 分钟",
        actor or "-", start_hour, end_hour, interval_minutes,
    )
    # 配置变更 → 让调度器重算当日槽位登记(架构 §六.4 风险缓解)
    _invalidate_cache()
    try:
        from app.services.bms_scheduler import bms_scheduler

        bms_scheduler.on_settings_changed()
    except Exception:  # noqa: BLE001  调度器未启动/导入失败不影响配置写入
        logger.debug("[scheduler_settings] 通知调度器重算槽位失败(忽略)", exc_info=True)
    return to_dict(row)


# ─────────────────────────────────────────────────────────────────────
# 缓存(供调度器每 tick 读取, 避免每分钟查库)
# ─────────────────────────────────────────────────────────────────────
_CACHE_TTL_S = 60.0
_cache: dict = {"at": 0.0, "value": None}


def _invalidate_cache() -> None:
    _cache["at"] = 0.0
    _cache["value"] = None


def get_cached_slots(db: Session) -> tuple[list[str], int, int, int]:
    """带 60s 缓存的槽位读取。返回 (slots, start_hour, end_hour, interval)。

    调度器每 60s tick 一次, 缓存 TTL 与之对齐: 正常情况下每分钟最多查库 1 次;
    配置变更时由 `_invalidate_cache()` 主动失效, 保证"下一槽位生效"。
    """
    now = time.monotonic()
    if _cache["value"] is not None and (now - _cache["at"]) < _CACHE_TTL_S:
        return _cache["value"]

    row = get_settings(db)
    value = (
        compute_slots(
            row.high_freq_start_hour, row.high_freq_end_hour, row.high_freq_interval_minutes
        ),
        row.high_freq_start_hour,
        row.high_freq_end_hour,
        row.high_freq_interval_minutes,
    )
    _cache["at"] = now
    _cache["value"] = value
    return value


def reset_cache_for_tests() -> None:
    """测试辅助: 清缓存(避免用例间串扰)。"""
    _invalidate_cache()

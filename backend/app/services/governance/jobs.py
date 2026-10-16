# -*- coding: utf-8 -*-
"""#332 T6 治理作业并发互斥封装: 采纳作业 + E 路企业商户维表重建

设计说明（Design Rationale）:
- 目的: 让「采纳作业 / 维表重建」在调度器与 admin 手动两个入口间**互斥**,
  且每次运行都在 system_event 留痕(开始/结束/计数/耗时)。
- 逻辑(为何这样组织):
  - 互斥用 **事务级** advisory lock(pg_try_advisory_xact_lock):
    随作业末尾唯一一次 commit/rollback 自动释放, 不会像会话级锁那样因
    SQLAlchemy 连接池换连接而「锁泄漏成僵尸锁」(既有教训: advisory lock
    僵尸连接挂死需 pg_terminate_backend);
  - 同一 key 上会话级/事务级锁互斥, 测试可用会话级锁模拟并发占用;
  - 锁 key 为代码常量(332 工单号编码), 全库唯一来源, 禁止散造。
- 取舍: ① 抢不到锁直接返回 skipped(不排队等锁 —— 调度下一周期自然会来,
  排队只会拖垮单 worker); ② 留痕只写「完成态」一条事件(成功/失败),
  不写开始占位行(同步作业秒~分钟级, 双行留痕会淹没真实变化, 同 #166 教训);
  ③ 作业失败向上抛, 由调用方(调度器已包 try)决定, 不在本层吞。
- 工单: #332
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.governance.dim_device_merchant_service import rebuild_dim_device_merchant
from app.services.governance.refund_adoption import run_refund_adoption
from app.services.system_event_service import record_system_event

logger = logging.getLogger(__name__)

# advisory lock keys(全库唯一来源; 编码: 332 工单 + 序号)
LOCK_KEY_REFUND_ADOPTION = 33200001
LOCK_KEY_DIM_DEVICE_MERCHANT = 33200002

_JOB = "job"
_SKIPPED = "skipped"


def _try_lock(db: Session, key: int) -> bool:
    return bool(db.execute(
        text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": key}
    ).scalar())


def _jsonable(obj):
    """system_event detail 序列化: Decimal/datetime 转字符串。"""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


def run_adoption_job(
    db: Session,
    routes: str | list[str] | tuple[str, ...] | None = None,
    *,
    trigger: str = "manual",
) -> dict:
    """带互斥锁与留痕地运行五路采纳作业。

    返回: 作业结果(run_refund_adoption 原样返回, 另附 trigger/elapsed_ms);
          抢不到锁时 {"skipped": True, "reason": "already_running"}。
    异常: 留 failed 事件后原样上抛。
    """
    if not _try_lock(db, LOCK_KEY_REFUND_ADOPTION):
        logger.warning("[governance] 采纳作业已在运行(锁占用), 本次跳过 trigger=%s", trigger)
        return {_SKIPPED: True, "reason": "already_running", _JOB: "refund_adoption"}

    t0 = time.monotonic()
    started = datetime.now(timezone.utc)
    try:
        result = run_refund_adoption(db, routes)  # 末尾唯一 commit, 随之释放锁
    except Exception as e:
        db.rollback()  # 释放事务级锁
        record_system_event(
            db, category="governance", action="refund_adoption_run",
            title="退款五路采纳作业失败",
            summary=f"trigger={trigger}: {e}"[:500],
            status="failed",
            detail=f"trigger={trigger}\nstarted_at={started.isoformat()}\nerror={e!r}",
        )
        raise
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    result["trigger"] = trigger
    result["elapsed_ms"] = elapsed_ms

    # 完成态留痕(TR-6.3): 五路行数/排除数/耗时齐全
    summary_parts = [
        f"{r}={s['total']}行(adopt {s['adopted']}/excl {s['excluded']}/pend {s['pending']})"
        for r, s in result["routes"].items()
    ]
    record_system_event(
        db, category="governance", action="refund_adoption_run",
        title=f"退款五路采纳作业完成({','.join(result['routes'])})",
        summary="; ".join(summary_parts)[:500],
        status="success",
        detail=json.dumps(
            {"trigger": trigger, "started_at": started.isoformat(),
             "finished_at": datetime.now(timezone.utc).isoformat(),
             "elapsed_ms": elapsed_ms, **result},
            ensure_ascii=False, default=_jsonable,
        ),
    )
    return result


def run_dim_device_merchant_job(db: Session, *, trigger: str = "manual") -> dict:
    """带互斥锁与留痕地重建 dim_device_merchant。返回/异常语义同 run_adoption_job。"""
    if not _try_lock(db, LOCK_KEY_DIM_DEVICE_MERCHANT):
        logger.warning("[governance] dim_device_merchant 重建已在运行(锁占用), 本次跳过 trigger=%s", trigger)
        return {_SKIPPED: True, "reason": "already_running", _JOB: "dim_device_merchant"}

    t0 = time.monotonic()
    started = datetime.now(timezone.utc)
    try:
        result = rebuild_dim_device_merchant(db)  # 末尾唯一 commit, 随之释放锁
    except Exception as e:
        db.rollback()
        record_system_event(
            db, category="governance", action="dim_device_merchant_rebuild",
            title="E路企业商户维表重建失败",
            summary=f"trigger={trigger}: {e}"[:500],
            status="failed",
            detail=f"trigger={trigger}\nstarted_at={started.isoformat()}\nerror={e!r}",
        )
        raise
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    result["trigger"] = trigger
    result["elapsed_ms"] = elapsed_ms

    record_system_event(
        db, category="governance", action="dim_device_merchant_rebuild",
        title="E路企业商户维表重建完成",
        summary=(
            f"{result['segments']}分段/{result['devices']}设备; "
            f"overlap设备{result['overlap_devices']}, 仲裁{result['arbiter_picks']}段, "
            f"假SN{result['fake_sns']}"
        )[:500],
        status="success",
        detail=json.dumps(
            {"trigger": trigger, "started_at": started.isoformat(),
             "finished_at": datetime.now(timezone.utc).isoformat(),
             "elapsed_ms": elapsed_ms, **result},
            ensure_ascii=False, default=_jsonable,
        ),
    )
    return result

"""#339 大表物理精简重建作业: cs_records / bms_orders

幂等, advisory lock 防并发, 全量 TRUNCATE + INSERT…SELECT (同事务原子换装,
读侧 MVCC 无感知中间态)。
设计范式同 dim_device_combo_service / refund_adoption。

定时: batch job rebuild_std_big, 北京时间每日 03:40 (batch_runner.BATCH_JOBS)。
  原设计 02:30 已被 rebuild_cash_flow_wide 占用, 按 02:00 purge → 02:30
  cash_flow_wide → 03:10 refund_wide → 03:40 std_big 错峰; 早于 05:00 BMS
  补跑 / 06:00 套餐同步窗口。
手动: 直接调用 rebuild_cs_records() / rebuild_bms_orders() / rebuild_both()
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import engine
from app.core.db_guard import assert_safe_to_destroy

logger = logging.getLogger(__name__)

# 精简列集 (与 migration d9e1f0c2b4a6 共用单一来源)
_CS_COLS = "id, problem_category, problem_type, device_sn, solution, operation_time, operator, description, execution_result, user_accepted, consult_time, created_at"
_BO_COLS = "order_no, order_type, device_sn, combo_name, combo_price, actual_pay_price, balance_pay_price, pay_date, pay_type, order_status, refund_status, create_user, create_date, business_name, merchant_name, active_date, device_type_name, created_at, updated_at"


def rebuild_cs_records() -> dict:
    """std_cs_record 全量重建 (over ods.cs_records 精简列集)。"""
    return _rebuild("std_cs_record", "ods.cs_records", _CS_COLS, 1101, "cs_records")


def rebuild_bms_orders() -> dict:
    """std_bms_order 全量重建 (over ods.bms_orders 精简列集)。"""
    return _rebuild("std_bms_order", "ods.bms_orders", _BO_COLS, 1102, "bms_orders")


def rebuild_both() -> dict:
    """两张大表都重建 (串行, advisory lock 防并发)。"""
    r1 = rebuild_cs_records()
    r2 = rebuild_bms_orders()
    return {"cs_records": r1, "bms_orders": r2}


def _rebuild(std_table: str, ods_table: str, cols: str, lock_key: int, label: str) -> dict:
    started = datetime.now(timezone.utc)
    logger.info("[%s] rebuild start → %s (from %s)", label, std_table, ods_table)

    with Session(engine, autocommit=False, autoflush=False) as db:
        try:
            # 破坏性操作纪律: TRUNCATE 前过 db_guard; 定时重建是设计内常态运维,
            # 显式 allow_real_db=True (与 batch_runner._purge_expired_logs 同范式)
            assert_safe_to_destroy(
                db,
                reason=f"std_big_rebuild[{label}]: TRUNCATE {std_table} 后全量回灌(设计内定时重建, #339)",
                allow_real_db=True,
            )
            db.execute(text(f"SELECT pg_advisory_lock({lock_key})"))
            # 1) TRUNCATE
            db.execute(text(f"TRUNCATE TABLE {std_table}"))
            db.flush()
            # 2) INSERT…SELECT 精简列集
            db.execute(
                text(f"INSERT INTO {std_table} ({cols}) SELECT {cols} FROM {ods_table}")
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("[%s] rebuild failed", label)
            raise
        finally:
            db.execute(text(f"SELECT pg_advisory_unlock({lock_key})"))

    elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    logger.info("[%s] rebuild done → %s (%d ms)", label, std_table, elapsed_ms)
    return {"elapsed_ms": elapsed_ms, "std_table": std_table}

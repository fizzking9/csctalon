"""日志/事件表保留期清理(2026-09-29)

背景: 系统上线两个月, ticket_action_logs 已 73 万行/219MB, operation_logs 10 万行,
system_events 6600+ 行且无任何清理机制, 无限增长会拖慢查询、撑大备份。

策略(分级保留, 仅删"流水", 不碰业务事实表):
  - system_events       90 天   事务事件(成功噪音占大头)
  - llm_call_logs       90 天   LLM 调用明细(成本统计按月出, 90 天足够追溯)
  - operation_logs      180 天  管理操作审计(安全追溯留半年)
  - ticket_action_logs  365 天  工单动作流水(量大, 留一年)
  - bms/scrm/complaint/manual_refund sync_logs 365 天  同步凭证(补跑/熔断参考)

执行: 调度器每天 03:15(北京时间)调一次 run_retention_purge(); 分批 DELETE
(ctid 子查询, 每批 5000 行), 避免长事务锁表; 单表失败不影响其它表。
可独立手动调用(管理脚本/测试), 纯幂等。
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 表名 → 保留天数。表名是代码内置白名单(不接受外部传入), 杜绝 SQL 注入。
RETENTION_DAYS: dict[str, int] = {
    "system_events": 90,
    "llm_call_logs": 90,
    "operation_logs": 180,
    "ticket_action_logs": 365,
    "bms_sync_logs": 365,
    "scrm_sync_logs": 365,
    "complaint_sync_logs": 365,
    "manual_refund_sync_logs": 365,
}

# 所有表统一用 created_at(已核对 8 张表均存在该列)
TS_COLUMN = "created_at"
BATCH_SIZE = 5000
MAX_BATCHES_PER_TABLE = 200  # 单次最多 100 万行/表, 超出下次继续, 避免凌晨任务过长


def purge_table(db: Session, table: str, days: int,
                batch_size: int = BATCH_SIZE,
                max_batches: int = MAX_BATCHES_PER_TABLE) -> int:
    """分批删除单张表的过期行, 返回删除总行数。

    用 ctid 子查询限定每批行集(PostgreSQL), 每批独立提交, 不持有长事务。
    """
    if table not in RETENTION_DAYS:
        raise ValueError(f"不在保留期白名单内的表: {table}")
    threshold = datetime.now(timezone.utc) - timedelta(days=days)
    # 表名/列名来自上方白名单常量; 参数走绑定(:ts/:batch), 无注入面
    sql = text(
        f"DELETE FROM {table} WHERE ctid IN ("
        f"  SELECT ctid FROM {table} WHERE {TS_COLUMN} < :ts LIMIT :batch)"
    )
    total = 0
    for _ in range(max_batches):
        result = db.execute(sql, {"ts": threshold, "batch": batch_size})
        db.commit()
        deleted = result.rowcount or 0
        total += deleted
        if deleted < batch_size:
            break
    return total


def run_retention_purge(db: Session, *, retention_days: dict[str, int] | None = None) -> dict:
    """执行一次全量保留期清理。返回 {表名: 删除行数} 与汇总。

    单表失败: 记录日志/事件后继续其它表, 不让一张表的问题中断整体清理。
    """
    rules = retention_days or RETENTION_DAYS
    deleted_map: dict[str, int] = {}
    errors: dict[str, str] = {}
    for table, days in rules.items():
        try:
            n = purge_table(db, table, days)
            deleted_map[table] = n
            if n:
                logger.info("[retention] %s 删除 %d 行(保留 %d 天)", table, n, days)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            errors[table] = str(e)
            logger.error("[retention] %s 清理失败(跳过): %s", table, e, exc_info=True)

    total_deleted = sum(deleted_map.values())
    # 仅在真的删了东西(或出错)时留事务事件, 避免每天一条空跑噪音(info 不亮角标)
    if total_deleted or errors:
        try:
            from app.services.system_event_service import record_system_event

            summary = ", ".join(f"{t} {n} 行" for t, n in deleted_map.items() if n) or "无过期数据"
            if errors:
                summary += f"; 失败: {', '.join(errors)}"
            record_system_event(
                db,
                category="schedule",
                action="log_retention_purge",
                title="日志保留期清理" if not errors else "日志保留期清理(部分失败)",
                summary=summary,
                status="success" if not errors else "partial",
                level="info" if not errors else "warning",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[retention] 事件写入失败(忽略): %s", e)

    return {"deleted": deleted_map, "total": total_deleted, "errors": errors}

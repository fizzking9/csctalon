"""workbuddy 手动退款数据推送同步服务

workbuddy 每 20 分钟检查文件(商户号手机退款处理登记.xlsx),文件变化时将
明细以 JSON 推送过来,本服务负责:

1. 数据清洗: 列名映射(兼容中文/英文字段)、姓名/金额字段防御性校验、
   时间质量校验(登记日期年份纠错/时间倒挂标记)
2. 业务去重: 按 (register_time, order_no, refund_amount) 复合键 upsert,
   防止 20 分钟重复推送产生脏数据(同一订单可分期多次退款,复合键能区分)
3. 设备主数据维护: 推送中出现的新 SN 自动在 devices 表建空壳
4. 同步日志: 每次推送写入 manual_refund_sync_logs,记录清洗/去重/报错统计
"""

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.manual_refund import ManualRefund
from app.models.manual_refund_sync_log import ManualRefundSyncLog
from app.services.data_parser import COLUMN_TEMPLATES
from app.services.upsert import (
    _ensure_afternoon_time,
    _freeze_ok_manual_refund_rows,
    _is_manual_refund_name_polluted,
    _manual_dedup_key,
    _manual_dedup_register_date,
    _manual_refund_quality_check,
    _sanitize_name_field,
    _sync_devices,
    _to_datetime,
    _to_float,
    _to_str,
    _truncate_str,
)

logger = logging.getLogger(__name__)


class ManualRefundSyncResult:
    """手动退款同步结果统计

    - dropped  : 硬失败(整行被丢弃, 如行解析异常/缺登记日期或订单号) → 影响同步状态(partial)
    - warnings : 软警告(行保留, 但某字段被清洗, 如姓名列填了手机号 → 置空) → 仅记录, 不影响状态
    """

    def __init__(self) -> None:
        self.total = 0
        self.inserted = 0
        self.updated = 0
        self.dropped = 0
        self.filtered = 0
        self.warnings = 0
        self.errors: list[dict[str, Any]] = []

    def add_warning(self, row_idx: int, msg: str, data: dict | None = None) -> None:
        """软警告: 行保留但字段被清洗"""
        self.warnings += 1
        self.errors.append({"row": row_idx, "level": "warning", "message": msg, "data": data})

    def add_error(self, row_idx: int, msg: str, data: dict | None = None) -> None:
        """兼容 upsert._sanitize_name_field 的软警告回调(该回调不会丢弃行)"""
        self.add_warning(row_idx, msg, data)

    def add_dropped(self, row_idx: int, msg: str, data: dict | None = None) -> None:
        """硬失败: 整行被丢弃"""
        self.dropped += 1
        self.errors.append({"row": row_idx, "level": "error", "message": msg, "data": data})


def _map_row(row: dict[str, Any]) -> dict[str, Any]:
    """将原始行(中文列名或英文字段名)映射为模型字段名

    优先用 COLUMN_TEMPLATES["manual_refund"] 的中文候选列名; 同时兼容英文字段名。
    """
    out: dict[str, Any] = {}
    template = COLUMN_TEMPLATES.get("manual_refund", {})
    row_keys = {str(k).strip(): v for k, v in row.items()}
    for target_field, candidates in template.items():
        # 1) 直接命中英文字段名
        if target_field in row_keys:
            out[target_field] = row_keys[target_field]
            continue
        # 2) 中文候选列名精确匹配
        found = False
        for cand in candidates:
            for rk, rv in row_keys.items():
                if rk == cand:
                    out[target_field] = rv
                    found = True
                    break
            if found:
                break
    return out


def _clean_row(row: dict[str, Any], idx: int, result: ManualRefundSyncResult) -> dict[str, Any] | None:
    """清洗单条手动退款行 → 模型字段值"""
    data = _map_row(row)
    try:
        register_time = _ensure_afternoon_time(_to_datetime(data.get("register_time")))
        order_no = _to_str(data.get("order_no"))
        if register_time is None or not order_no:
            result.add_dropped(idx, "缺少登记日期或订单号,无法去重定位(跳过)")
            return None
        remark = _to_str(data.get("remark")) or ""
        # 示例模板行跳过(与本地导入同规则)
        if remark == "示例":
            result.add_warning(idx, "备注为'示例'的模板数据行,已跳过")
            return None
        op_time = _ensure_afternoon_time(_to_datetime(data.get("op_time")))
        # 时间质量校验(年份纠错 auto_fixed / 待人工确认 pending, 覆盖 R2/R3/R4; R4 也修正 op_time 未来年份)
        register_time_before = register_time  # 纠错前原始值(存 register_time_raw)
        register_time, op_time, quality_status, quality_note = _manual_refund_quality_check(
            register_time, op_time, idx, result)
        # 人员污染检测(#36): 登记人/退款操作人被数字/时间戳/备注污染 → sanitize 会置空,
        # 此时补打 [人员污染] pending, 让 L3 质量页可见可处理(而非仅日志 warning 不可见)
        name_polluted = _is_manual_refund_name_polluted(data.get("registrant")) or _is_manual_refund_name_polluted(
            data.get("refund_operator"))
        if name_polluted and quality_status is None:
            quality_status, quality_note = "pending", "[人员污染] 登记人/退款操作人疑似列错位被置空, 待人工核对"
        raw_refunded = _to_str(data.get("is_refunded"))
        device_sn = _to_str(data.get("device_sn"))
        return {
            "register_time": register_time,
            # ⚠️ 2026-09-05 (#20): 纠错(auto_fixed)发生时保留原始值到 register_time_raw
            "register_time_raw": register_time_before if quality_status == "auto_fixed" else None,
            "device_sn": device_sn[:64] if device_sn else None,
            "order_no": order_no,
            "merchant_id": _truncate_str(data.get("merchant_id"), 64),
            "order_amount": _to_float(data.get("order_amount")),
            "registrant": _sanitize_name_field(data.get("registrant"), idx, result, "手动退款-登记人"),
            "is_refunded": "是" if raw_refunded == "是" else "否",
            "refund_amount": _to_float(data.get("refund_amount")),
            "refund_operator": _sanitize_name_field(data.get("refund_operator"), idx, result, "手动退款-退款操作人"),
            "op_time": op_time,
            "remark": remark or None,
            "quality_status": quality_status,
            "quality_note": quality_note,
        }
    except Exception as e:
        result.add_dropped(idx, f"手动退款行解析失败: {e}")
        return None


def _dedup_in_batch(rows: list[dict]) -> list[dict]:
    """同批次内去重: 按 (登记日期, 订单号, 设备号, 登记人) 键(文件里可能存在重复行,
    PG ON CONFLICT DO UPDATE 不允许同一语句内二次命中,保留最后一条)"""
    seen: dict[tuple, dict] = {}
    for r in rows:
        seen[_manual_dedup_key(r)] = r
    return list(seen.values())


def _batch_upsert(db: Session, rows: list[dict], result: ManualRefundSyncResult) -> None:
    """按 (北京登记日期, order_no, COALESCE(device_sn,''), COALESCE(registrant,'')) 去重 upsert

    与本地导入(upsert.upsert_manual_refunds)同口径: 设备号+订单号+登记日期+登记人 确定一笔退款单,
    退款金额/状态修改走 UPDATE。分批写入避免单条 SQL 绑定参数超 PG 上限。
    """
    if not rows:
        return
    unique_rows = _dedup_in_batch(rows)
    dup_in_batch = len(rows) - len(unique_rows)
    if dup_in_batch:
        logger.info(f"[manual_refund_sync] 批次内重复去重键 {dup_in_batch} 行(保留最后一条)")

    # ok 保护(#36): 剔除"库内已人工确认(ok)且本次问题类别相同"的行, 冻结不更新不重报
    unique_rows = _freeze_ok_manual_refund_rows(db, unique_rows)
    if not unique_rows:
        logger.info("[manual_refund_sync] 全部行被 ok 保护冻结(无更新)")
        return

    total_ins = 0
    total_upd = 0
    BATCH = 2000
    for i in range(0, len(unique_rows), BATCH):
        batch = unique_rows[i:i + BATCH]
        stmt = pg_insert(ManualRefund).values(batch)
        # 去重键字段不参与 set(由唯一索引定位); 其余字段作为可更新内容
        _key_cols = ("register_time", "order_no", "device_sn", "registrant")
        update_cols = {c: stmt.excluded[c] for c in batch[0].keys() if c not in _key_cols}
        # 内容比较字段: 去重键与系统派生时间戳不参与"是否变化"判定, 避免每轮整表重写
        _compare_cols = [
            c for c in batch[0].keys()
            if c not in _key_cols and c not in ("quality_note", "register_time_raw")
        ]
        _where = None
        if _compare_cols:
            _where = or_(*[
                getattr(ManualRefund, c).is_distinct_from(stmt.excluded[c]) for c in _compare_cols
            ])
        stmt = stmt.on_conflict_do_update(
            # 表达式部分唯一索引 (北京登记日期, order_no, COALESCE(device_sn,''), COALESCE(registrant,''))
            index_elements=[
                text("((register_time AT TIME ZONE 'Asia/Shanghai')::date)"),
                text("order_no"),
                text("COALESCE(device_sn, '')"),
                text("COALESCE(registrant, '')"),
            ],
            index_where=text("order_no IS NOT NULL"),
            set_=update_cols,
            where=_where,  # #36: 未变行不更新(避免每轮整表重写历史行)
        )
        # RETURNING (xmax=0)=insert; 排除被 where 挡下的未变冲突行
        ret = db.execute(
            stmt.returning(text("(xmax = 0) AS _is_ins"))
        ).all()
        total_ins += sum(1 for r in ret if r[0])
        total_upd += len(ret) - sum(1 for r in ret if r[0])
        db.flush()
    result.inserted += total_ins
    result.updated += total_upd


def _reconcile_full(db: Session, keys_in_payload: set[tuple]) -> int:
    """全量对账: 删除库中不在本次推送内的行(按新去重键)。返回删除行数。

    要求 workbuddy 每次全量推送文件全部行(payload 带 full_sync=true)。
    只处理 order_no 非空的行; order_no 为空的历史脏行不受影响。
    """
    rows = db.execute(
        select(ManualRefund.id, ManualRefund.register_time, ManualRefund.order_no,
               ManualRefund.device_sn, ManualRefund.registrant)
        .where(ManualRefund.order_no.isnot(None))
    ).all()
    stale_ids: list[int] = []
    for rid, rt, on, sn, reg in rows:
        k = (str(_manual_dedup_register_date(rt)), on, (sn or "").strip(), (reg or "").strip())
        if k not in keys_in_payload:
            stale_ids.append(rid)
    if stale_ids:
        db.execute(delete(ManualRefund).where(ManualRefund.id.in_(stale_ids)))
    return len(stale_ids)


def sync_manual_refunds_from_payload(
    db: Session,
    payload: dict[str, Any],
    source_file: str | None = None,
    sync_source: str = "workbuddy",
) -> dict[str, Any]:
    """workbuddy JSON 推送主入口

    payload 结构:
    {
      "source_file": "商户号手机退款处理登记.xlsx",   # 可选
      "manual_refunds": [ {...中文列名行...} ],      # 必填
      "full_sync": true,                            # 可选; 每次全量推送文件全部行时置 true,
                                                     # 系统会删除库中不在本次推送内的残留行(全量对账)
    }
    行内字段名兼容 xlsx 中文列名与英文字段名。
    返回统计(与 manual_refund_sync_logs 记录一致)。
    """
    log = ManualRefundSyncLog(source_file=source_file or "workbuddy-push", sync_source=sync_source, status="success")
    db.add(log)
    result = ManualRefundSyncResult()
    try:
        rows_raw = payload.get("manual_refunds") or []
        full_sync = bool(payload.get("full_sync"))
        if not isinstance(rows_raw, list):
            raise ValueError("manual_refunds 必须是数组")

        rows: list[dict] = []
        for idx, row in enumerate(rows_raw):
            if not isinstance(row, dict):
                result.add_dropped(idx, "手动退款行不是对象")
                continue
            rec = _clean_row(row, idx, result)
            if rec is None:
                continue
            rows.append(rec)
        result.total = len(rows_raw)

        # 维护设备主表(缺失 SN 建空壳,避免外键失败)
        _sync_devices(rows, "device_sn", db)
        _batch_upsert(db, rows, result)

        # 全量对账: 删除库中不在本次推送内的行(防止文件版本回退/删除行导致残留累积)
        deleted = 0
        if full_sync and rows:
            keys_in_payload = {_manual_dedup_key(r) for r in rows}
            deleted = _reconcile_full(db, keys_in_payload)
            if deleted:
                logger.info(f"[manual_refund_sync] 全量对账删除残留行 {deleted} 行")

        # 统计写日志(dropped=硬失败行, 影响状态; warnings=软警告仅记录)
        log.total = result.total
        log.inserted = result.inserted
        log.updated = result.updated
        log.deleted = deleted
        log.failed = result.dropped
        log.warnings = result.warnings
        all_errors = result.errors[:50]
        if all_errors:
            log.error = str(all_errors)
        log.status = "partial" if result.dropped else "success"
        log.finished_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(log)

        # 手动退款入库后失效看板缓存(退款看板/运营看板/今日看板), 避免旧聚合
        # (被动推送独立于 sync_task_manager, 需在此显式失效)
        try:
            from app.core.redis_client import cache_invalidate_recent_days
            n = 0
            for pat in ("refund:*", "today:*"):
                n += cache_invalidate_recent_days(pat)
            if n:
                logger.info(f"[manual_refund_sync] 失效 {n} 条看板缓存(近N天窗口,保留历史)")
        except Exception as ce:
            logger.debug(f"[manual_refund_sync] 缓存失效失败(不影响同步): {ce}")

        logger.info(
            f"[manual_refund_sync] {source_file or 'workbuddy-push'} 完成: "
            f"{result.total}(+{result.inserted}/~{result.updated}/-{deleted}/!{result.dropped}/!w{result.warnings})"
        )
        return {
            "log_id": log.id,
            "status": log.status,
            "total": result.total,
            "inserted": result.inserted,
            "updated": result.updated,
            "deleted": deleted,
            "failed": result.dropped,
            "filtered": result.filtered,
            "warnings": result.warnings,
            "errors": result.errors[:50],
        }
    except Exception as e:
        db.rollback()
        log.status = "failed"
        log.error = str(e)[:2000]
        log.finished_at = datetime.now(timezone.utc)
        try:
            db.commit()
        except Exception:
            db.rollback()
        logger.exception(f"[manual_refund_sync] 推送失败: {e}")
        raise

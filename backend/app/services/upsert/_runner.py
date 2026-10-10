"""入口编排: run_upsert / UPSERT_DISPATCH / 拒绝行与异常值落库(suspects)。"""

import logging

import pandas as pd
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 跨子模块引用(拆分自 upsert.py, 原同文件内直接调用)
from app.services.upsert._complaints import upsert_card_complaints, upsert_external_complaints
from app.services.upsert._core import (
    _SUSPECT_LIMIT_PER_BATCH,
    _TZ_CN,
    ImportResult,
    _sanitize_date,
    _to_datetime,
)
from app.services.upsert._masters import (
    upsert_bms_orders,
    upsert_device_combos,
    upsert_device_flow_usage,
    upsert_device_profiles,
    upsert_device_tag_bindings,
    upsert_device_tags,
    upsert_merchants,
    upsert_refund_record_reports,
    upsert_return_trackings,
)
from app.services.upsert._refunds import (
    upsert_manual_refunds,
    upsert_merchant_refunds,
    upsert_refunds,
    upsert_shanmao_refunds,
    upsert_wallet,
)
from app.services.upsert._tickets import upsert_cs_records, upsert_tickets


def _persist_rejected_from_errors(db: Session, data_type: str, result: ImportResult) -> int:
    """A-20260908-003 (#40): 把 run_upsert 硬失败(丢弃未入库)的行写拒收表。

    只处理 errors 中带原始行 data 的(无 data 的仅计数, 无法按行展示)。
    savepoint 隔离: 写拒收失败不影响已完成的 upsert 与主事务。

    返回(#42): 实际写入拒收表的条数 —— 与 failed 不同义:
      failed  = 硬失败行数(含无 data 无法按行展示的)
      rejected = 其中真正落库、可按行可见可判定的条数
    """
    import logging as _log

    _lg = _log.getLogger(__name__)
    try:
        from app.models.ingest_rejected_row import IngestRejectedRow

        to_insert = [
            e for e in result.errors
            if e.get("data") and isinstance(e.get("data"), dict)
        ]
        if not to_insert:
            return 0
        inserted = 0
        with db.begin_nested():
            for e in to_insert[:200]:  # 上限 200 行/批, 防海量噪音
                db.add(IngestRejectedRow(
                    data_type=data_type,
                    source="upload",  # 上传/导入共用 run_upsert; BMS 走此也标 upload 可后续细分
                    reject_category="validate_fail",
                    reject_reason=str(e.get("message"))[:500],
                    raw_row=e.get("data"),
                    source_row_no=e.get("row"),
                    status="open",
                ))
                inserted += 1
        return inserted
    except Exception as _rej_e:  # noqa: BLE001
        _lg.warning(f"[ingest_rejected] 上传错误行落拒收表失败(忽略): {_rej_e}")
        return 0

UPSERT_DISPATCH = {    "refund": upsert_refunds,
    "wallet": upsert_wallet,
    "ticket": upsert_tickets,
    "device_combo": upsert_device_combos,
    "merchant_refund": upsert_merchant_refunds,
    "merchant": upsert_merchants,
    "cs_record": upsert_cs_records,
    "external_complaint": upsert_external_complaints,
    "card_complaint": upsert_card_complaints,
    "shanmao_refund": upsert_shanmao_refunds,
    "manual_refund": upsert_manual_refunds,
    "device_tag": upsert_device_tags,
    "device_tag_binding": upsert_device_tag_bindings,
    "device_flow_usage": upsert_device_flow_usage,
    "device_profile": upsert_device_profiles,
    "refund_record_report": upsert_refund_record_reports,
    "bms_order": upsert_bms_orders,
    "return_tracking": upsert_return_trackings,
}

def _persist_text_suspects(db: Session, data_type: str, df, mapping: dict[str, str],
                           *, limit: int = _SUSPECT_LIMIT_PER_BATCH) -> int:
    """工单 #168：导入期文本异常留痕（截断 / 换行）——**只看不改**。

    为什么在 `run_upsert` 这一层做：它是上传/BMS 共用的**唯一入口**，且此处 df 里是
    **截断前的原始值**（比扫描期只能看截断后的值更准）。判据与扫描期同源，见
    `text_anomaly_rules`（同一常量、同一白名单、同一类型枚举）。

    三条纪律：① 不改入库数据 ② 失败不阻断采集 ③ 每批上限（默认 200，同 #40 的 validate_fail）
    """
    import logging as _log

    _lg = _log.getLogger(__name__)
    try:
        from app.services.ingest_rejected_service import add_suspect_row
        from app.services.text_anomaly_rules import CATEGORY_BY_KIND, kinds_of

        col_limits = _column_max_lengths(db, data_type)
        if not col_limits:
            return 0
        # mapping 是 {目标字段: 源列名}（见 _apply_mapping 的遍历方式）
        written = 0
        budget = limit
        for idx, rec in enumerate(df.to_dict("records")):
            if budget <= 0:
                break
            for target_field, source_col in mapping.items():
                if budget <= 0:
                    break
                kinds = kinds_of(rec.get(source_col), col_limits.get(target_field),
                                 column=target_field)
                for kind in kinds:
                    if budget <= 0:
                        break
                    row = add_suspect_row(
                        db, data_type=data_type, source="upload",
                        reject_category=CATEGORY_BY_KIND[kind],
                        reject_reason=(f"导入期检测: {target_field} "
                                       f"{'含换行符' if kind == 'newline' else '长度贴近列宽上限(疑被截断)'}"
                                       f"（原始值长度 {len(str(rec.get(source_col) or ''))}）"),
                        raw_row=rec, source_row_no=idx + 2,  # +2: 表头占 1 行, 行号从 1 起
                    )
                    if row is not None:
                        written += 1
                        budget -= 1
        return written
    except Exception as e:  # noqa: BLE001  留痕失败不影响采集主流程
        _lg.warning(f"[ingest_rejected] 文本异常留痕失败(忽略, 不影响入库): {e}")
        return 0

def _column_max_lengths(db: Session, data_type: str) -> dict[str, int | None]:
    """取该 data_type 对应表的文本列宽（列宽权威 = DB 的 information_schema）。

    表名从 `data_sources` 反查（与扫描期 D4 用同一张表 → 判据同源的一部分）；
    未登记/查不到 → 返回空 dict，调用方静默跳过（不猜列宽）。
    """
    try:
        from sqlalchemy import text as _text

        table = db.execute(_text(
            "SELECT table_name FROM data_sources WHERE code = :c LIMIT 1"
        ), {"c": data_type}).scalar()
        if not table:
            return {}
        rows = db.execute(_text(
            "SELECT column_name, character_maximum_length FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t "
            "  AND data_type IN ('text', 'character varying')"
        ), {"t": table}).mappings().all()
        return {r["column_name"]: r["character_maximum_length"] for r in rows}
    except Exception as e:  # noqa: BLE001  取列宽失败 → 不检测（不猜）
        logger.warning(f"[ingest_rejected] 取列宽失败, 跳过文本异常检测: {e}")
        return {}

def _datetime_columns(db: Session, data_type: str) -> set[str]:
    """该 data_type 对应表的日期/时间列（列类型权威 = DB 的 information_schema）。

    与 `_column_max_lengths` 同一手法（表名从 `data_sources` 反查，未登记则跳过不猜）。
    """
    try:
        from sqlalchemy import text as _text

        table = db.execute(_text(
            "SELECT table_name FROM data_sources WHERE code = :c LIMIT 1"
        ), {"c": data_type}).scalar()
        if not table:
            return set()
        rows = db.execute(_text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t "
            "  AND data_type IN ('timestamp with time zone','timestamp without time zone','date')"
        ), {"t": table}).scalars().all()
        return set(rows)
    except Exception as e:  # noqa: BLE001 取列类型失败 → 不检测（不猜）
        logger.warning(f"[ingest_rejected] 取日期列失败, 跳过日期异常检测: {e}")
        return set()

def _persist_date_suspects(db: Session, data_type: str, df, mapping: dict[str, str],
                           *, limit: int = _SUSPECT_LIMIT_PER_BATCH) -> int:
    """工单 #169：导入期日期异常留痕（年份越界 / 未来日期）——**只看不改**。

    与 D5 探测器**判据同源**（同一模块 `date_anomaly_rules`：同一 MIN_YEAR、同一容差、
    同一计划类列白名单）；此处拿的是 df 里的**原始值**（比扫描期只能看入库值更准）。

    三条纪律（同 #168）：① 不改入库数据 ② 失败不阻断采集 ③ 每批上限。

    ⚠️ 与**入库结果对齐**：`_sanitize_date` 能自动纠错的年份（如 2225 → 2025、1900 → 2000）
    不留痕 —— 那些列入库后已是正常值，留痕只会变成噪音。纠正不了的（含未走 `_sanitize_date`
    的列）才留痕；后者即使这里漏报，扫描期 D5 也会在库上检出（两道网）。
    """
    try:
        from app.services import date_anomaly_rules as _dr
        from app.services.ingest_rejected_service import add_suspect_row

        dt_cols = _datetime_columns(db, data_type)
        if not dt_cols:
            return 0
        written = 0
        budget = limit
        for idx, rec in enumerate(df.to_dict("records")):
            if budget <= 0:
                break
            for target_field, source_col in mapping.items():
                if budget <= 0 or target_field not in dt_cols:
                    continue
                raw = rec.get(source_col)
                dt = _to_datetime(raw)
                if dt is None:
                    continue  # 解析不了的值由既有校验/拒收流程管，不在此重复留痕
                fixed = _sanitize_date(dt)
                if fixed is not None and _dr.is_impossible_year(dt) \
                        and not _dr.is_impossible_year(fixed):
                    continue  # 入库前已被 _sanitize_date 纠成正常年份 → 不入留痕
                kinds = _dr.impossible_kinds(dt, column=target_field)
                if not kinds:
                    continue
                reason = "、".join(
                    f"{_dr.KIND_TEXT[k]}（{dt.astimezone(_TZ_CN).strftime('%Y-%m-%d')}，"
                    f"允许 {_dr.MIN_YEAR}~{_dr.max_year()}）"
                    if k == _dr.KIND_YEAR_RANGE else
                    f"{_dr.KIND_TEXT[k]}（{dt.astimezone(_TZ_CN).strftime('%Y-%m-%d')}）"
                    for k in kinds
                )
                row = add_suspect_row(
                    db, data_type=data_type, source="upload",
                    reject_category=_dr.CATEGORY_DATE_IMPOSSIBLE,
                    reject_reason=f"导入期检测: {target_field} {reason}（原始值 {raw!r}）",
                    raw_row=rec, source_row_no=idx + 2,  # +2: 表头占 1 行, 行号从 1 起
                )
                if row is not None:
                    written += 1
                    budget -= 1
        return written
    except Exception as e:  # noqa: BLE001  留痕失败不影响采集主流程
        logger.warning(f"[ingest_rejected] 日期异常留痕失败(忽略, 不影响入库): {e}")
        return 0

def run_upsert(
    df: pd.DataFrame,
    data_type: str,
    mapping: dict[str, str],
    db: Session,
) -> ImportResult:
    """执行 upsert 主入口"""
    result = ImportResult()
    # 统一清洗空值:
    # 1. NaN/NaT → None(iterrows 会把含空值的列推断为 float,产生 NaN/NaT)
    # 2. 文本 "NaN"/"nan"/"null"/"none"(含前后空白) → None
    #    (导出数据常用这些占位符表示空,直接插入会触发外键/类型错误)
    df = df.where(pd.notna(df), None)
    df = df.map(
        lambda v: None
        if (isinstance(v, str) and v.strip().lower() in ("nan", "null", "none", ""))
        else v
    )
    fn = UPSERT_DISPATCH.get(data_type)
    if not fn:
        raise ValueError(f"不支持的数据类型: {data_type}")
    # ── 工单 #168: 导入期文本异常留痕(截断/换行) —— 在入库**之前**跑, 拿的是截断前原始值;
    #    只写留痕、不改 df、不改入库数据(失败仅告警, 见 _persist_text_suspects)
    suspects = _persist_text_suspects(db, data_type, df, mapping)
    # ── 工单 #169: 导入期日期异常留痕(年份越界/未来日期) —— 同一位置、同一纪律(判据同源见
    #    date_anomaly_rules; 扫描期 D5 用同一组常量)
    date_suspects = _persist_date_suspects(db, data_type, df, mapping)
    fn(df, mapping, db, result)
    result.text_suspects = suspects
    result.date_suspects = date_suspects
    # ── A-20260908-003 (#40): 未采纳行落拒收表(上传/BMS 共用 run_upsert 入口) ──
    # result.errors = 硬失败(行被丢弃未入库), 有 raw data 的写 ingest_rejected_rows
    # 供人工判定(open/keep/discard)。savepoint 隔离, 失败不影响主流程。
    if result.errors:
        result.rejected = _persist_rejected_from_errors(db, data_type, result)
    db.commit()
    return result

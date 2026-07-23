"""增量 upsert 服务 - 按主键去重,已存在则更新,新增则插入

支持 4 张业务表 + 商户主数据
统计逻辑:upsert 前先查已存在的主键数量,以精确区分 insert/update
"""

from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.merchant import Merchant
from app.models.merchant_refund import MerchantRefund
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.models.wallet import WalletWithdrawal


class ImportResult:
    """导入结果统计"""

    def __init__(self) -> None:
        self.total = 0
        self.inserted = 0
        self.updated = 0
        self.failed = 0
        self.errors: list[dict[str, Any]] = []

    def add_error(self, row_idx: int, msg: str, data: dict | None = None) -> None:
        self.failed += 1
        self.errors.append({"row": row_idx, "message": msg, "data": data})


# ---------- 类型转换工具 ----------


def _to_str(val: Any) -> str | None:
    """强制转字符串,过滤 NaN/None(pandas iterrows 会把混合列推断为 float)"""
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    s = str(val).strip()
    if s == "" or s.lower() == "nan":
        return None
    return s


def _to_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        if pd.isna(val):  # 捕获 numpy nan / pandas NaT
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, str) and val.strip() == "":
        return None
    try:
        f = float(val)
        if pd.isna(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _to_int(val: Any) -> int | None:
    f = _to_float(val)
    return int(f) if f is not None else None


def _to_datetime(val: Any) -> datetime | None:
    if val is None:
        return None
    try:
        if pd.isna(val):  # 捕获 pandas NaT / numpy nan
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, datetime):
        return val
    if isinstance(val, str) and val.strip() == "":
        return None
    try:
        result = pd.to_datetime(val)
        if pd.isna(result):
            return None
        return result.to_pydatetime()
    except Exception:
        return None


def _clean_value(val: Any) -> Any:
    """统一清洗单元格值(pandas object 列里 None 会被内部转成 NaN,
    且导出数据常含文本 'NaN'/'null' 占位符,需统一归一为 None)"""
    if val is None:
        return None
    try:
        if pd.isna(val):  # 捕获 numpy nan / pandas NaT
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, str):
        s = val.strip()
        if s == "" or s.lower() in ("nan", "null", "none"):
            return None
        return s
    return val


def apply_mapping(row: dict, mapping: dict[str, str]) -> dict[str, Any]:
    """根据列映射从行数据提取目标字段值(并统一清洗空值)"""
    out: dict[str, Any] = {}
    for target_field, source_col in mapping.items():
        if source_col in row:
            out[target_field] = _clean_value(row[source_col])
    return out


# ---------- 通用 upsert 执行器 ----------


def _execute_upsert(
    db: Session,
    model,
    pk_field: str,
    rows: list[dict],
    result: ImportResult,
) -> None:
    """通用 upsert 执行:先统计已存在,再执行 on_conflict,精确区分 insert/update"""
    if not rows:
        return
    # 按主键去重(同批次内重复主键会导致 ON CONFLICT affect row a second time;
    # 保留最后一条,符合"后到覆盖"的增量语义)
    seen: dict[str, dict] = {}
    for r in rows:
        k = r.get(pk_field)
        if k is not None:
            seen[str(k)] = r
    rows = list(seen.values())
    if not rows:
        return
    # 主键统一转字符串,避免 IN 查询时 varchar = double precision 类型冲突
    keys = [str(r[pk_field]) for r in rows]
    existing_count = 0
    if keys:
        existing_count = db.scalar(
            select(func.count()).select_from(model).where(getattr(model, pk_field).in_(keys))
        ) or 0
    stmt = pg_insert(model).values(rows)
    update_cols = {c: stmt.excluded[c] for c in rows[0] if c != pk_field}
    stmt = stmt.on_conflict_do_update(index_elements=[pk_field], set_=update_cols)
    db.execute(stmt)
    db.flush()
    result.inserted += max(len(rows) - existing_count, 0)
    result.updated += min(existing_count, len(rows))


def _sync_devices(rows: list[dict], sn_field: str, db: Session) -> None:
    """从导入记录中同步设备主数据(SN 不存在则创建)

    SN 统一转字符串并过滤 None/NaN,避免 IN 查询类型冲突
    (df.iterrows 会把含 NaN 的列推断为 float,导致 varchar = double precision 错误)
    """
    sns = {_to_str(r.get(sn_field)) for r in rows}
    sns.discard(None)
    if not sns:
        return
    existing = set(db.scalars(select(Device.sn).where(Device.sn.in_(sns))))
    to_create = [Device(sn=sn) for sn in sns if sn not in existing]
    if to_create:
        db.bulk_save_objects(to_create)
        db.flush()


# ---------- 各数据类型 upsert ----------


def upsert_refunds(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            refund_no = (data.get("refund_no") or "").strip() if data.get("refund_no") else None
            if not refund_no:
                result.add_error(idx, "退费单号为空")
                continue
            rows.append({
                "refund_no": refund_no,
                "platform_order_no": data.get("platform_order_no"),
                "third_order_no": data.get("third_order_no"),
                "device_sn": data.get("device_sn"),
                "refund_reason": data.get("refund_reason"),
                "package_name": data.get("package_name"),
                "merchant_name": data.get("merchant_name"),
                "refund_amount": _to_float(data.get("refund_amount")),
                "actual_amount": _to_float(data.get("actual_amount")),
                "remark": data.get("remark"),
                "status": data.get("status"),
                "refund_method": data.get("refund_method"),
                "audit_remark": data.get("audit_remark"),
                "auditor": data.get("auditor"),
                "operator": data.get("operator"),
                "created_at_raw": _to_datetime(data.get("created_at_raw")),
                "updated_at_raw": _to_datetime(data.get("updated_at_raw")),
                "package_price": _to_float(data.get("package_price")),
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    _sync_devices(rows, "device_sn", db)
    _execute_upsert(db, Refund, "refund_no", rows, result)


def upsert_wallet(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """钱包提现 - 无天然唯一键,简单 insert(on_conflict_do_nothing 兜底)"""
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            rows.append({
                "account_id": data.get("account_id"),
                "wallet_balance": _to_float(data.get("wallet_balance")),
                "device_sn": data.get("device_sn"),
                "op_type": data.get("op_type"),
                "op_amount": _to_float(data.get("op_amount")),
                "remark": data.get("remark"),
                "op_time": _to_datetime(data.get("op_time")),
                "operator": data.get("operator"),
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    _sync_devices(rows, "device_sn", db)
    db.execute(pg_insert(WalletWithdrawal).values(rows).on_conflict_do_nothing())
    db.flush()
    result.inserted += len(rows)


def upsert_tickets(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            ticket_no = (data.get("ticket_no") or "").strip() if data.get("ticket_no") else None
            if not ticket_no:
                result.add_error(idx, "工单号为空")
                continue
            rows.append({
                "ticket_no": ticket_no,
                "device_sn": data.get("device_sn"),
                "device_type": data.get("device_type"),
                "device_type_remark": data.get("device_type_remark"),
                "phone": data.get("phone"),
                "service_category": data.get("service_category"),
                "service_item": data.get("service_item"),
                "priority": _to_int(data.get("priority")),
                "status": data.get("status"),
                "remark": data.get("remark"),
                "register_time": _to_datetime(data.get("register_time")),
                "active_time": _to_datetime(data.get("active_time")),
                "dispatch_time": _to_datetime(data.get("dispatch_time")),
                "finish_time": _to_datetime(data.get("finish_time")),
                "handle_duration": _to_int(data.get("handle_duration")),
                "registrant": data.get("registrant"),
                "channel": data.get("channel"),
                "handle_node": data.get("handle_node"),
                "handler": data.get("handler"),
                "handle_opinion": data.get("handle_opinion"),
                "is_appeal": data.get("is_appeal"),
                "order_no": data.get("order_no"),
                "refund_amount": _to_float(data.get("refund_amount")),
                "estimated_amount": _to_float(data.get("estimated_amount")),
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    _sync_devices(rows, "device_sn", db)
    _execute_upsert(db, ServiceTicket, "ticket_no", rows, result)


def upsert_merchant_refunds(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            wx_refund_no = (data.get("wx_refund_no") or "").strip() if data.get("wx_refund_no") else None
            if not wx_refund_no:
                result.add_error(idx, "微信退款单号为空")
                continue
            rows.append({
                "wx_refund_no": wx_refund_no,
                "trade_time": _to_datetime(data.get("trade_time")),
                "app_id": data.get("app_id"),
                "merchant_id": data.get("merchant_id"),
                "sub_merchant_id": data.get("sub_merchant_id"),
                "device_no": data.get("device_no"),
                "wx_order_no": data.get("wx_order_no"),
                "merchant_order_no": data.get("merchant_order_no"),
                "user_id": data.get("user_id"),
                "trade_type": data.get("trade_type"),
                "trade_status": data.get("trade_status"),
                "pay_bank": data.get("pay_bank"),
                "currency": data.get("currency"),
                "settle_amount": _to_float(data.get("settle_amount")),
                "voucher_amount": _to_float(data.get("voucher_amount")),
                "refund_amount": _to_float(data.get("refund_amount")),
                "recharge_voucher_refund": _to_float(data.get("recharge_voucher_refund")),
                "refund_type": data.get("refund_type"),
                "refund_status": data.get("refund_status"),
                "product_name": data.get("product_name"),
                "fee": _to_float(data.get("fee")),
                "rate": _to_float(data.get("rate")),
                "order_amount": _to_float(data.get("order_amount")),
                "apply_refund_amount": _to_float(data.get("apply_refund_amount")),
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    _sync_devices(rows, "device_no", db)
    _execute_upsert(db, MerchantRefund, "wx_refund_no", rows, result)


def upsert_merchants(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            sub_name = (data.get("sub_name") or "").strip() if data.get("sub_name") else None
            if not sub_name:
                continue
            rows.append({
                "sub_name": sub_name,
                "main_name": data.get("main_name"),
                "merchant_id": data.get("merchant_id"),
                "legal_person": data.get("legal_person"),
                "super_admin": data.get("super_admin"),
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    _execute_upsert(db, Merchant, "sub_name", rows, result)


UPSERT_DISPATCH = {
    "refund": upsert_refunds,
    "wallet": upsert_wallet,
    "ticket": upsert_tickets,
    "merchant_refund": upsert_merchant_refunds,
    "merchant": upsert_merchants,
}


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
    fn(df, mapping, db, result)
    db.commit()
    return result

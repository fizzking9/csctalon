"""增量 upsert 服务 - 按主键去重,已存在则更新,新增则插入

支持 4 张业务表 + 商户主数据
统计逻辑:upsert 前先查已存在的主键数量,以精确区分 insert/update
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

# 运营平台(BMS / ai-acc)导出的时间为北京时间(naive,无时区)。
# 存入 timestamptz 列时,DB 时区为 Etc/UTC,naive 值会被当 UTC 存,
# 导致按北京显示时偏后 8 小时。这里把 naive 视为北京时间,转 UTC 后再存。
_TZ_CN = timezone(timedelta(hours=8))

from app.models.card_complaint import CardComplaint
from app.models.cs_record import CsRecord
from app.models.device import Device
from app.models.device_combo import DeviceCombo
from app.models.external_complaint import ExternalComplaint
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
        # naive 视为北京时间 → UTC(aware);aware 原样返回
        if val.tzinfo is None:
            return val.replace(tzinfo=_TZ_CN).astimezone(timezone.utc)
        return val
    if isinstance(val, str) and val.strip() == "":
        return None
    try:
        result = pd.to_datetime(val)
        if pd.isna(result):
            return None
        # 运营平台导出为 naive 北京时间,转 UTC 后存入 timestamptz,
        # 否则 DB(UTC)会按 UTC 解释导致偏后 8 小时
        if result.tzinfo is None:
            result = result.tz_localize(_TZ_CN).tz_convert(timezone.utc)
        return result.to_pydatetime()
    except Exception:
        return None


def _sanitize_date(val: datetime | None) -> datetime | None:
    """对明显录入笔误的年份做纠错(如 2225 → 2025)

    运营平台导出数据偶有年份录入错误(多打一位/输错前两位),
    规则:年份 > 当前年+1 视为异常,尝试 2xxx→20xx 映射(2000 + year%100);
    映射后仍异常或无法确定则返回 None。
    """
    if val is None:
        return None
    year = val.year
    current_year = datetime.now().year
    if year <= current_year + 1:
        return val  # 正常年份,不处理
    # 尝试 2xxx → 20xx 纠错(如 2225 → 2025, 2204 → 2004)
    corrected_year = 2000 + (year % 100)
    if corrected_year <= current_year + 1 and corrected_year >= 2000:
        try:
            return val.replace(year=corrected_year)
        except ValueError:
            # 2月29日在非闰年不存在,退到2月28日
            return val.replace(year=corrected_year, day=28)
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


# ---------- 脏数据防御:人名字段 & 单号格式校验 ----------

_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
# 典型单号: RF / SP / WS / WX / BR / INIT:: / YKM 等开头,必须是 ASCII 可见字符(不含中文)且长度≤64
_REFUND_NO_BAD_RE = re.compile(r"[\u4e00-\u9fff]|[\s，。；、（）【】《》\"'`]")


def _is_numeric_like(val: Any) -> bool:
    """判断一个值是否"长得像数字/金额"。
    用于 operator / auditor / handler 等"应该是人名字符串"的字段,
    如果 Excel 列映射错把金额列写进来,在这里能识别并拦截。
    """
    if val is None:
        return False
    if isinstance(val, (int, float)):
        try:
            if pd.isna(val):  # NaN/NaT 不算
                return False
        except (TypeError, ValueError):
            pass
        return True
    if isinstance(val, str):
        s = val.strip().replace(",", "")  # 千分位逗号
        return bool(_NUMERIC_RE.match(s))
    return False


def _looks_like_bad_refund_no(val: str | None) -> bool:
    """退费单号明显不合法:含中文、中文标点、空白,或长度过于离谱(>64)。
    合法单号示例: RF2025071200031 / INIT::xxx::套餐::2025-01-01 等。
    """
    if not val:
        return False
    if len(val) > 64:
        return True
    return bool(_REFUND_NO_BAD_RE.search(val))


def _sanitize_name_field(
    val: Any,
    row_idx: int,
    result: ImportResult,
    field_label: str,
) -> str | None:
    """清洗应该为人名(或企微-人名 / 英文账号名)的字符串字段。
    - 含数字/金额格式 → 判定为"Excel 列映射错位",记录为错误行提示,但继续导入(该字段置 None)
      这样不会丢整行数据,同时在 errors 里提醒用户检查列映射。
    - 空值 → None
    """
    s = _to_str(val)
    if s is None:
        return None
    if _is_numeric_like(s):
        result.add_error(
            row_idx,
            f"{field_label}='{s}' 格式异常(疑似金额/数字,可能是 Excel 列映射错位),已置空请核对列映射",
            {"field": field_label, "raw_value": s},
        )
        return None
    return s


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
            if _looks_like_bad_refund_no(refund_no):
                result.add_error(
                    idx,
                    f"退费单号 '{refund_no}' 格式异常(含中文/标点/空白或过长,疑似误填为备注文本),请核对",
                    {"refund_no": refund_no},
                )
                continue
            # operator / auditor 必须像人名(不能是数字,否则是金额列映射错位)
            operator = _sanitize_name_field(data.get("operator"), idx, result, "退费单-操作人")
            auditor = _sanitize_name_field(data.get("auditor"), idx, result, "退费单-审核人")
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
                "auditor": auditor,
                "operator": operator,
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
                "operator": _sanitize_name_field(data.get("operator"), idx, result, "钱包提现-操作人"),
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
            ticket_no = str(data.get("ticket_no")).strip() if data.get("ticket_no") else None
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
                "registrant": _sanitize_name_field(data.get("registrant"), idx, result, "工单-登记人"),
                "channel": data.get("channel"),
                "handle_node": data.get("handle_node"),
                "handler": _sanitize_name_field(data.get("handler"), idx, result, "工单-处理人"),
                "handle_opinion": data.get("handle_opinion"),
                "is_appeal": data.get("is_appeal"),
                "order_no": data.get("order_no"),
                "submit_params": data.get("submit_params"),
                "refund_amount": _to_float(data.get("refund_amount")),
                "estimated_amount": _to_float(data.get("estimated_amount")),
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    _sync_devices(rows, "device_sn", db)
    _execute_upsert(db, ServiceTicket, "ticket_no", rows, result)


def upsert_merchant_refunds(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """商户号现金流 - 按 (trade_time, merchant_order_no) 复合键去重 upsert

    微信退款单号在 Excel 导出时被 float 截断丢失精度,支付行则为 0,不可作主键。
    改用 (交易时间, 商户订单号) 复合去重键(已验证 86352 行零重复),重复导入按此键
    更新而非累计,后续拿到准确数据也能按此键覆盖补全。
    缺少交易时间或商户订单号的行无法定位,跳过。单批超 PG 65535 绑定参数上限,按 2000 行/批。
    """
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            trade_time = _to_datetime(data.get("trade_time"))
            mono = data.get("merchant_order_no")
            mono = None if (isinstance(mono, str) and mono.strip() in ("", "0", "0.0")) else mono
            # 缺少去重键(交易时间或商户订单号)的行无法定位,跳过
            if trade_time is None or mono is None:
                result.add_error(idx, "缺少交易时间或商户订单号,无法去重定位(跳过)")
                continue
            # merchant_refund_no 为 "0"/"0.0" 时归一为 None(支付行无商户退款单号,非真实单号)
            mrn = data.get("merchant_refund_no")
            if isinstance(mrn, str) and mrn.strip() in ("0", "0.0"):
                mrn = None
            rows.append({
                "wx_refund_no": data.get("wx_refund_no"),
                "merchant_refund_no": mrn,
                "trade_time": trade_time,
                "app_id": data.get("app_id"),
                "merchant_id": data.get("merchant_id"),
                "sub_merchant_id": data.get("sub_merchant_id"),
                "device_no": data.get("device_no"),
                "wx_order_no": data.get("wx_order_no"),
                "merchant_order_no": mono,
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
    if not rows:
        return
    _sync_devices(rows, "device_no", db)
    # 分批 on_conflict_do_update:按 (trade_time, merchant_order_no) 部分唯一索引去重更新。
    # index_where 须与索引定义一致以供 PG 推断该部分索引。
    before = db.scalar(select(func.count()).select_from(MerchantRefund)) or 0
    BATCH = 2000
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        stmt = pg_insert(MerchantRefund).values(batch)
        update_cols = {c: stmt.excluded[c] for c in batch[0].keys()
                       if c not in ("trade_time", "merchant_order_no")}
        stmt = stmt.on_conflict_do_update(
            index_elements=["trade_time", "merchant_order_no"],
            index_where=text("trade_time IS NOT NULL AND merchant_order_no IS NOT NULL"),
            set_=update_cols,
        )
        db.execute(stmt)
        db.flush()
    after = db.scalar(select(func.count()).select_from(MerchantRefund)) or 0
    inserted = max(after - before, 0)
    result.inserted += inserted
    result.updated += max(len(rows) - inserted, 0)


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


def upsert_cs_records(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """智能助手处理记录 - 无天然唯一键,简单 insert(on_conflict_do_nothing 兜底)

    同设备同时间可多次接待,不去重;SN 缺失允许(部分接待无设备号)。
    """
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            rows.append({
                "problem_category": data.get("problem_category"),
                "problem_type": data.get("problem_type"),
                "device_sn": data.get("device_sn"),
                "solution": data.get("solution"),
                "operation_time": _to_datetime(data.get("operation_time")),
                "operator": _sanitize_name_field(data.get("operator"), idx, result, "客服记录-操作人"),
                "send_content": data.get("send_content"),
                "description": data.get("description"),
                "execution_result": data.get("execution_result"),
                "user_accepted": data.get("user_accepted"),
                "consult_time": _to_datetime(data.get("consult_time")),
                "problem_create_time": _to_datetime(data.get("problem_create_time")),
                "last_consult_time": _to_datetime(data.get("last_consult_time")),
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    _sync_devices(rows, "device_sn", db)
    db.execute(pg_insert(CsRecord).values(rows).on_conflict_do_nothing())
    db.flush()
    result.inserted += len(rows)


def upsert_external_complaints(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """外部投诉(监督局/315) - 投诉编号去重更新,无编号的行简单插入

    投诉编号有 \n 前缀,导入时剥离。无编号的行(~3%)按 on_conflict_do_nothing 插入,
    重复导入会累计(与 cs_record 一致),有编号的 96% 走 upsert 精确去重更新。
    """
    with_pk: list[dict] = []
    without_pk: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            # 投诉编号剥离 \n/空白
            raw_no = data.get("complaint_no")
            complaint_no = raw_no.replace("\n", "").strip() if isinstance(raw_no, str) else None
            rec = {
                "complaint_no": complaint_no or None,
                "complaint_date": _sanitize_date(_to_datetime(data.get("complaint_date"))),
                "platform": data.get("platform"),
                "complaint_account": data.get("complaint_account"),
                "company_entity": data.get("company_entity"),
                "complaint_shop": data.get("complaint_shop"),
                "purchase_order_no": data.get("purchase_order_no"),
                "complainant": _sanitize_name_field(data.get("complainant"), idx, result, "外部投诉-投诉人"),
                "complainant_phone": data.get("complainant_phone"),
                "device_sn": data.get("device_sn"),
                "wechat_id": data.get("wechat_id"),
                "problem": data.get("problem"),
                "keyword": data.get("keyword"),
                "complaint_category": data.get("complaint_category"),
                "solution": data.get("solution"),
                "result": data.get("result"),
                "follower": _sanitize_name_field(data.get("follower"), idx, result, "外部投诉-跟进人"),
                "handle_time": _sanitize_date(_to_datetime(data.get("handle_time"))),
                "handle_detail": data.get("handle_detail"),
                "internal_analysis": data.get("internal_analysis"),
                "external_analysis": data.get("external_analysis"),
                "reception_cs": _sanitize_name_field(data.get("reception_cs"), idx, result, "外部投诉-接待客服"),
                "package_order_no": data.get("package_order_no"),
                "package_name": data.get("package_name"),
                "package_price": _to_float(data.get("package_price")),
                "actual_pay_amount": _to_float(data.get("actual_pay_amount")),
                "negotiate_refund_amount": _to_float(data.get("negotiate_refund_amount")),
            }
            (with_pk if complaint_no else without_pk).append(rec)
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    all_rows = with_pk + without_pk
    if not all_rows:
        return
    _sync_devices(all_rows, "device_sn", db)
    # 有编号:upsert 精确去重更新
    if with_pk:
        _execute_upsert(db, ExternalComplaint, "complaint_no", with_pk, result)
    # 无编号:简单插入(null 不触发 unique 冲突,重复导入会累计)
    if without_pk:
        db.execute(pg_insert(ExternalComplaint).values(without_pk).on_conflict_do_nothing())
        db.flush()
        result.inserted += len(without_pk)


def upsert_card_complaints(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """卡商群投诉 - 无天然唯一键,简单 insert(on_conflict_do_nothing 兜底),与 cs_record 一致

    device_sn 单元格可能含多个 SN(换行分隔),取首个以保证 varchar(64) 与外键约束。
    """
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            # device_sn 取首个(单元格可能含多个 SN,换行分隔)
            raw_sn = data.get("device_sn")
            device_sn = raw_sn.split("\n")[0].strip() if isinstance(raw_sn, str) and raw_sn.strip() else None
            rows.append({
                "complaint_date": _sanitize_date(_to_datetime(data.get("complaint_date"))),
                "complaint_group": data.get("complaint_group"),
                "complainant": _sanitize_name_field(data.get("complainant"), idx, result, "卡商群投诉-投诉人"),
                "complainant_phone": data.get("complainant_phone"),
                "device_sn": device_sn,
                "problem": data.get("problem"),
                "problem_category": data.get("problem_category"),
                "result": data.get("result"),
                "solution": data.get("solution"),
                "handler": _sanitize_name_field(data.get("handler"), idx, result, "卡商群投诉-处理人"),
                "handle_result": data.get("handle_result"),
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    _sync_devices(rows, "device_sn", db)
    db.execute(pg_insert(CardComplaint).values(rows).on_conflict_do_nothing())
    db.flush()
    result.inserted += len(rows)


def upsert_device_combos(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """设备套餐 - 主键 recharge_order_no,有则更新无则插入

    初始套餐(免费赠送/体验套餐)在 BMS 导出时无充值订单号,此前被主键非空校验跳过,
    导致这部分记录缺失。现为其生成稳定 fallback 主键 `INIT::{device_sn}::{package_name}::{start_date}`:
      - `INIT::` 前缀与真实订单号(BR/WX/SP 开头)隔离,绝不冲突
      - (device_sn, package_name, start_date) 组合唯一,保证多次增量同步幂等(命中 on_conflict_do_update)
      - device_sn 缺失时无法构造稳定唯一键,才报错跳过(安全网,正常初始套餐数据不触发)
    """
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            order_no = (data.get("recharge_order_no") or "").strip() if data.get("recharge_order_no") else None
            device_sn = data.get("device_sn")
            package_name = data.get("package_name")
            start_date = _to_datetime(data.get("start_date"))

            if not order_no:
                # 无充值订单号(典型:初始套餐),生成 fallback 主键入库
                if not device_sn:
                    result.add_error(idx, "充值订单号为空且设备SN为空,无法生成主键")
                    continue
                start_str = start_date.strftime("%Y-%m-%d") if start_date else "unknown"
                order_no = f"INIT::{device_sn}::{package_name or 'unknown'}::{start_str}"

            rows.append({
                "recharge_order_no": order_no,
                "device_sn": device_sn,
                "package_name": package_name,
                "package_type": data.get("package_type"),
                "traffic_type": data.get("traffic_type"),
                "merchant_name": data.get("merchant_name"),
                "actual_amount": _to_float(data.get("actual_amount")),
                "start_date": start_date,
                "end_date": _to_datetime(data.get("end_date")),
                "updated_at_raw": _to_datetime(data.get("updated_at_raw")),
                "created_at_raw": _to_datetime(data.get("created_at_raw")),
                "valid_days": _to_int(data.get("valid_days")),
                "creator": data.get("creator"),
                "available_traffic": _to_int(data.get("available_traffic")),
                "used_traffic": _to_int(data.get("used_traffic")),
                "remaining_traffic": _to_int(data.get("remaining_traffic")),
                "status": data.get("status"),
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    _sync_devices(rows, "device_sn", db)
    _execute_upsert(db, DeviceCombo, "recharge_order_no", rows, result)


UPSERT_DISPATCH = {
    "refund": upsert_refunds,
    "wallet": upsert_wallet,
    "ticket": upsert_tickets,
    "device_combo": upsert_device_combos,
    "merchant_refund": upsert_merchant_refunds,
    "merchant": upsert_merchants,
    "cs_record": upsert_cs_records,
    "external_complaint": upsert_external_complaints,
    "card_complaint": upsert_card_complaints,
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

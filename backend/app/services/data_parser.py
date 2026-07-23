"""数据解析服务 - CSV/XLSX 读取、编码识别、列名映射

复用现有分析逻辑,从'退费汇总.xlsx'的列结构提取列模板
"""

import io
import re
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.config import settings

# 各数据类型的列模板(目标字段名 -> 样本源列名候选)
# 源列名可能含尾部 \t,解析时会清洗
COLUMN_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "refund": {
        "refund_no": ["退费单号"],
        "platform_order_no": ["平台订单号"],
        "third_order_no": ["三方订单号"],
        "device_sn": ["设备SN"],
        "refund_reason": ["退费原因"],
        "package_name": ["套餐名称"],
        "merchant_name": ["商户名称"],
        "refund_amount": ["退费金额"],
        "actual_amount": ["实退金额"],
        "remark": ["备注"],
        "status": ["状态"],
        "refund_method": ["退款方式"],
        "audit_remark": ["审核备注"],
        "auditor": ["审核人"],
        "operator": ["操作人"],
        "created_at_raw": ["创建时间"],
        "updated_at_raw": ["更新时间"],
        "package_price": ["套餐价格"],
    },
    "wallet": {
        "account_id": ["账户ID"],
        "wallet_balance": ["钱包余额"],
        "device_sn": ["SN"],
        "op_type": ["操作类型"],
        "op_amount": ["操作金额"],
        "remark": ["备注"],
        "op_time": ["操作时间"],
        "operator": ["操作人员"],
    },
    "ticket": {
        "ticket_no": ["工单号"],
        "device_sn": ["SN"],
        "device_type": ["设备类型"],
        "device_type_remark": ["设备类型备注"],
        "phone": ["手机号"],
        "service_category": ["服务类别"],
        "service_item": ["服务项"],
        "priority": ["优先级"],
        "status": ["状态"],
        "remark": ["备注"],
        "register_time": ["登记时间"],
        "active_time": ["激活时间"],
        "dispatch_time": ["分发时间"],
        "finish_time": ["完成时间"],
        "handle_duration": ["处理时长"],
        "registrant": ["登记人"],
        "channel": ["录入渠道"],
        "handle_node": ["处理节点"],
        "handler": ["处理人"],
        "handle_opinion": ["处理意见"],
        "is_appeal": ["是否申诉"],
        "order_no": ["订单编号"],
        "refund_amount": ["退款金额"],
        "estimated_amount": ["预估可退金额"],
    },
    "merchant_refund": {
        "trade_time": ["交易时间"],
        "app_id": ["公众账号ID"],
        "merchant_id": ["商户号"],
        "sub_merchant_id": ["特约商户号"],
        "device_no": ["设备号"],
        "wx_order_no": ["微信订单号"],
        "merchant_order_no": ["商户订单号"],
        "user_id": ["用户标识"],
        "trade_type": ["交易类型"],
        "trade_status": ["交易状态"],
        "pay_bank": ["付款银行"],
        "currency": ["货币种类"],
        "settle_amount": ["应结订单金额"],
        "voucher_amount": ["代金券金额"],
        "wx_refund_no": ["微信退款单号"],
        "refund_amount": ["退款金额"],
        "recharge_voucher_refund": ["充值券退款金额"],
        "refund_type": ["退款类型"],
        "refund_status": ["退款状态"],
        "product_name": ["商品名称"],
        "fee": ["手续费"],
        "rate": ["费率"],
        "order_amount": ["订单金额"],
        "apply_refund_amount": ["申请退款金额"],
    },
    "merchant": {
        "sub_name": ["子商户名称"],
        "main_name": ["主商户名称"],
        "merchant_id": ["商户号id"],
        "legal_person": ["法人"],
        "super_admin": ["超级管理员"],
    },
}


def clean_cell(val: Any) -> Any:
    """清洗单元格:去尾部 \\t、空白;空字符串转 None"""
    if val is None:
        return None
    if isinstance(val, str):
        v = val.replace("\t", "").strip()
        return v if v != "" else None
    # NaN 检测
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    return val


def read_csv_auto_encoding(path: Path | str, encoding: str | None = None) -> tuple[pd.DataFrame, str]:
    """自动识别编码读取 CSV,返回 (df, encoding_used)

    使用 python 引擎 + on_bad_lines='skip' 容错处理字段数不一致的行
    (真实数据中部分行含逗号/引号会导致字段数错位)
    """
    encodings_to_try = [encoding] if encoding else ["utf-8", "gbk", "gb18030", "utf-8-sig"]
    last_err: Exception | None = None
    for enc in encodings_to_try:
        if not enc:
            continue
        try:
            df = pd.read_csv(
                path,
                encoding=enc,
                dtype=str,
                engine="python",
                on_bad_lines="warn",
            )
            return df, enc
        except (UnicodeDecodeError, UnicodeError) as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue
    raise ValueError(f"无法识别CSV编码: {last_err}")


def read_table(path: Path | str, sheet_name: str | None = None, encoding: str | None = None) -> tuple[pd.DataFrame, str | None, list[str] | None]:
    """读取表格文件,返回 (df, used_encoding, sheet_names)

    支持 .csv / .xlsx / .xls
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df, enc = read_csv_auto_encoding(path, encoding)
        # 清洗列名(去 \t)
        df.columns = [str(c).replace("\t", "").strip() for c in df.columns]
        return df, enc, None
    elif suffix in (".xlsx", ".xls"):
        xl = pd.ExcelFile(path)
        sheets = xl.sheet_names
        target = sheet_name if sheet_name in sheets else (sheets[0] if sheets else None)
        df = pd.read_excel(path, sheet_name=target, dtype=str)
        df.columns = [str(c).replace("\t", "").strip() for c in df.columns]
        return df, None, sheets
    else:
        raise ValueError(f"不支持的文件类型: {suffix}")


def suggest_mapping(data_type: str, columns: list[str]) -> dict[str, str]:
    """根据列模板自动建议列映射(目标字段 -> 实际列名)"""
    template = COLUMN_TEMPLATES.get(data_type, {})
    mapping: dict[str, str] = {}
    normalized_cols = {c.replace("\t", "").strip(): c for c in columns}
    for target_field, candidates in template.items():
        for cand in candidates:
            cand_clean = cand.replace("\t", "").strip()
            if cand_clean in normalized_cols:
                mapping[target_field] = normalized_cols[cand_clean]
                break
    return mapping


def extract_refund_no_from_remark(remark: str | None) -> str | None:
    """从钱包提现备注中正则提取退费单号 RF..."""
    if not remark:
        return None
    m = re.search(r"RF\d{10,}", remark)
    return m.group(0) if m else None


def parse_file(path: Path | str, data_type: str, sheet_name: str | None = None, encoding: str | None = None) -> dict[str, Any]:
    """解析文件,返回预览数据

    Returns:
        dict: columns, sample_rows, total_rows, detected_encoding, suggested_mapping, sheet_names
    """
    df, used_enc, sheet_names = read_table(path, sheet_name=sheet_name, encoding=encoding)
    # 清洗所有单元格(pandas 2.1+ 用 DataFrame.map 替代 applymap)
    df = df.map(clean_cell)  # noqa: PD003
    total_rows = len(df)
    sample = df.head(10).to_dict(orient="records")
    # 转 JSON 安全类型
    sample = [_json_safe(r) for r in sample]
    suggested = suggest_mapping(data_type, list(df.columns))
    return {
        "columns": list(df.columns),
        "sample_rows": sample,
        "total_rows": total_rows,
        "detected_encoding": used_enc,
        "suggested_mapping": suggested,
        "sheet_names": sheet_names,
    }


def _json_safe(row: dict) -> dict:
    """将行数据转为 JSON 安全类型"""
    out = {}
    for k, v in row.items():
        if v is None or (isinstance(v, float) and pd.isna(v)):
            out[k] = None
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def load_dataframe(path: Path | str, sheet_name: str | None = None, encoding: str | None = None) -> pd.DataFrame:
    """加载完整 DataFrame(供 upsert 使用),单元格已清洗"""
    df, _, _ = read_table(path, sheet_name=sheet_name, encoding=encoding)
    df = df.map(clean_cell)  # noqa: PD003
    return df


def save_upload_file(content: bytes, filename: str) -> Path:
    """保存上传文件到上传目录,返回路径"""
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    # 防止路径穿越
    safe_name = Path(filename).name
    dest = upload_dir / safe_name
    with open(dest, "wb") as f:
        f.write(content)
    return dest

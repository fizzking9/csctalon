"""数据源元数据注册 - 反射 + Override 混合

字段集从 SQLAlchemy 模型反射获得（永远与模型对齐），
语义角色/聚合/枚举/可筛选 由 FIELD_OVERRIDES 人工维护。
新表加入：建表迁移 + 在 DATA_SOURCE_SEEDS 加一行 + 调 refresh。
"""

from sqlalchemy import Boolean, DateTime, Float, Integer, Numeric, delete, inspect, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.models.cs_record import CsRecord
from app.models.data_source import DataSource, DataSourceField
from app.models.merchant_refund import MerchantRefund
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.models.wallet import WalletWithdrawal

# 表级种子：code → 模型 + 元数据
DATA_SOURCE_SEEDS: list[dict] = [
    {
        "code": "refund",
        "model": Refund,
        "name_cn": "退费单",
        "time_field": "created_at_raw",
        "sn_field": "device_sn",
        "pk_field": "refund_no",
        "pk_strategy": "primary_key",
    },
    {
        "code": "wallet",
        "model": WalletWithdrawal,
        "name_cn": "钱包提现操作",
        "time_field": "op_time",
        "sn_field": "device_sn",
        "pk_field": None,
        "pk_strategy": "none",
    },
    {
        "code": "ticket",
        "model": ServiceTicket,
        "name_cn": "服务退款工单",
        "time_field": "register_time",
        "sn_field": "device_sn",
        "pk_field": "ticket_no",
        "pk_strategy": "primary_key",
    },
    {
        "code": "merchant_refund",
        "model": MerchantRefund,
        "name_cn": "商户号退款",
        "time_field": "trade_time",
        "sn_field": "device_no",
        "pk_field": "wx_refund_no",
        "pk_strategy": "primary_key",
    },
    {
        "code": "cs_record",
        "model": CsRecord,
        "name_cn": "智能助手处理记录",
        "time_field": "operation_time",
        "sn_field": "device_sn",
        "pk_field": None,
        "pk_strategy": "none",
    },
]

# 字段级 override：(source_code, field_name) → {role, agg, enum, is_filterable, is_pk}
# 未列出的字段默认 role=dimension, is_filterable=True, is_pk=col.primary_key
FIELD_OVERRIDES: dict[tuple[str, str], dict] = {
    # refund
    ("refund", "refund_no"): {"role": "dimension", "is_pk": True, "is_filterable": False},
    ("refund", "refund_amount"): {"role": "amount", "agg": "sum"},
    ("refund", "actual_amount"): {"role": "amount", "agg": "sum"},
    ("refund", "package_price"): {"role": "amount", "agg": "sum"},
    ("refund", "operator"): {"role": "person"},
    ("refund", "auditor"): {"role": "person"},
    ("refund", "device_sn"): {"role": "sn"},
    ("refund", "merchant_name"): {"role": "category"},
    ("refund", "status"): {"role": "category", "enum": ["申请中", "已退款", "无需退款"]},
    ("refund", "refund_method"): {"role": "category", "enum": ["线上", "线下", "钱包"]},
    ("refund", "refund_reason"): {"role": "category"},
    ("refund", "package_name"): {"role": "category"},
    ("refund", "created_at_raw"): {"role": "time"},
    ("refund", "updated_at_raw"): {"role": "time"},
    # wallet
    ("wallet", "op_amount"): {"role": "amount", "agg": "sum"},
    ("wallet", "wallet_balance"): {"role": "amount", "agg": "sum"},
    ("wallet", "operator"): {"role": "person"},
    ("wallet", "device_sn"): {"role": "sn"},
    ("wallet", "op_type"): {"role": "category", "enum": ["用户提现", "订单退费转入"]},
    ("wallet", "op_time"): {"role": "time"},
    ("wallet", "account_id"): {"role": "category"},
    # ticket
    ("ticket", "ticket_no"): {"role": "dimension", "is_pk": True, "is_filterable": False},
    ("ticket", "refund_amount"): {"role": "amount", "agg": "sum"},
    ("ticket", "estimated_amount"): {"role": "amount", "agg": "sum"},
    ("ticket", "handle_duration"): {"role": "measure", "agg": "avg"},
    ("ticket", "handler"): {"role": "person"},
    ("ticket", "registrant"): {"role": "person"},
    ("ticket", "device_sn"): {"role": "sn"},
    ("ticket", "service_category"): {"role": "category"},
    ("ticket", "service_item"): {"role": "category"},
    ("ticket", "status"): {"role": "category", "enum": ["待处理", "已完成"]},
    ("ticket", "register_time"): {"role": "time"},
    ("ticket", "finish_time"): {"role": "time"},
    ("ticket", "active_time"): {"role": "time"},
    ("ticket", "dispatch_time"): {"role": "time"},
    ("ticket", "priority"): {"role": "measure", "agg": "avg"},
    # merchant_refund
    ("merchant_refund", "wx_refund_no"): {"role": "dimension", "is_pk": True, "is_filterable": False},
    ("merchant_refund", "refund_amount"): {"role": "amount", "agg": "sum"},
    ("merchant_refund", "settle_amount"): {"role": "amount", "agg": "sum"},
    ("merchant_refund", "voucher_amount"): {"role": "amount", "agg": "sum"},
    ("merchant_refund", "fee"): {"role": "amount", "agg": "sum"},
    ("merchant_refund", "order_amount"): {"role": "amount", "agg": "sum"},
    ("merchant_refund", "apply_refund_amount"): {"role": "amount", "agg": "sum"},
    ("merchant_refund", "recharge_voucher_refund"): {"role": "amount", "agg": "sum"},
    ("merchant_refund", "device_no"): {"role": "sn"},
    ("merchant_refund", "trade_type"): {"role": "category"},
    ("merchant_refund", "trade_status"): {"role": "category"},
    ("merchant_refund", "refund_type"): {"role": "category"},
    ("merchant_refund", "refund_status"): {"role": "category", "enum": ["SUCCESS"]},
    ("merchant_refund", "merchant_id"): {"role": "category"},
    ("merchant_refund", "trade_time"): {"role": "time"},
    # cs_record
    ("cs_record", "device_sn"): {"role": "sn"},
    ("cs_record", "operator"): {"role": "person"},
    ("cs_record", "operation_time"): {"role": "time"},
    ("cs_record", "consult_time"): {"role": "time"},
    ("cs_record", "problem_create_time"): {"role": "time"},
    ("cs_record", "last_consult_time"): {"role": "time"},
    ("cs_record", "problem_category"): {"role": "category"},
    ("cs_record", "problem_type"): {"role": "category"},
    ("cs_record", "solution"): {"role": "category"},
    ("cs_record", "execution_result"): {"role": "category"},
    ("cs_record", "user_accepted"): {"role": "category", "enum": ["已接受", "未接受", "不涉及"]},
    ("cs_record", "send_content"): {"role": "dimension", "is_filterable": False},
    ("cs_record", "description"): {"role": "dimension", "is_filterable": False},
}

# 反射时排除的系统字段（TimestampMixin 的系统时间 + raw_extra 冗余）
_EXCLUDED_FIELDS = {"id", "created_at", "updated_at", "raw_extra"}


def _infer_data_type(col) -> str:
    """从 SQLAlchemy 列类型推断 data_type 字符串"""
    if isinstance(col.type, DateTime):
        return "timestamp"
    if isinstance(col.type, (Numeric, Float, Integer)):
        return "numeric"
    if isinstance(col.type, Boolean):
        return "bool"
    if isinstance(col.type, (JSONB,)):
        return "jsonb"
    return "string"


def ensure_data_sources_registered(db: Session) -> dict:
    """幂等注册：表已存在则更新元数据，字段集按模型反射重建"""
    stats = {"sources": 0, "fields": 0}
    for seed in DATA_SOURCE_SEEDS:
        # 反射字段集
        mapper = inspect(seed["model"])
        reflected: list[dict] = []
        for col in mapper.columns:
            if col.name in _EXCLUDED_FIELDS:
                continue
            override = FIELD_OVERRIDES.get((seed["code"], col.name), {})
            reflected.append({
                "field_name": col.name,
                "field_name_cn": col.comment or None,
                "data_type": _infer_data_type(col),
                "semantic_role": override.get("role", "dimension"),
                "agg_func": override.get("agg"),
                "is_filterable": override.get("is_filterable", True),
                "is_pk": override.get("is_pk", col.primary_key),
                "enum_values": override.get("enum"),
                "sort_order": len(reflected),
            })

        # upsert DataSource
        ds = db.scalar(select(DataSource).where(DataSource.code == seed["code"]))
        if ds:
            ds.table_name = seed["model"].__tablename__
            ds.name_cn = seed["name_cn"]
            ds.time_field = seed["time_field"]
            ds.sn_field = seed["sn_field"]
            ds.pk_field = seed["pk_field"]
            ds.pk_strategy = seed["pk_strategy"]
        else:
            ds = DataSource(
                code=seed["code"],
                table_name=seed["model"].__tablename__,
                name_cn=seed["name_cn"],
                time_field=seed["time_field"],
                sn_field=seed["sn_field"],
                pk_field=seed["pk_field"],
                pk_strategy=seed["pk_strategy"],
            )
            db.add(ds)
            db.flush()
        stats["sources"] += 1

        # 字段集重建（删旧建新，简单可靠）
        db.execute(delete(DataSourceField).where(DataSourceField.source_id == ds.id))
        for f in reflected:
            db.add(DataSourceField(source_id=ds.id, **f))
            stats["fields"] += 1
    db.commit()
    return stats

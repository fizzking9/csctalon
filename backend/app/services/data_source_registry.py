"""数据源元数据注册 - 反射 + Override 混合

字段集从 SQLAlchemy 模型反射获得（永远与模型对齐），
语义角色/聚合/枚举/可筛选 由 FIELD_OVERRIDES 人工维护。
新表加入：建表迁移 + 在 DATA_SOURCE_SEEDS 加一行 + 调 refresh。
"""

from sqlalchemy import Boolean, DateTime, Float, Integer, Numeric, delete, inspect, select
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.orm import Session

from app.models.card_complaint import CardComplaint
from app.models.cs_record import CsRecord
from app.models.data_source import DataSource, DataSourceField
from app.models.device_combo import DeviceCombo
from app.models.external_complaint import ExternalComplaint
from app.models.merchant_refund import MerchantRefund
from app.models.refund import Refund
from app.models.refund_wide import RefundWide
from app.models.ticket import ServiceTicket
from app.models.wallet import WalletWithdrawal

# 表级种子：code → 模型 + 元数据
# 核心数据表约束:
# 1. 服务请求(服务工单):包含退款和投诉等服务工单,站在设备视角
# 2. 退费单:运营视角的费用操作
# 3. 提现操作记录:会员钱包的操作记录
# 4. 外部投诉:315和质量监督局的投诉
# category 业务分组(数据查看左侧分组用):现金流/工单与客服/投诉登记/基础数据
DATA_SOURCE_SEEDS: list[dict] = [
    {
        "code": "refund_wide",
        "model": RefundWide,
        "name_cn": "退费宽表(物化)",
        "time_field": "created_at_raw",
        "sn_field": "device_sn",
        "pk_field": "refund_no",
        "pk_strategy": "primary_key",
        "category": "现金流",
        # internal=True 标记本表由系统物化构建,不来自 BMS/手动上传,UI 上禁止导入/同步
        "internal": True,
    },
    {
        "code": "ticket",
        "model": ServiceTicket,
        "name_cn": "服务请求管理",
        "time_field": "register_time",
        "sn_field": "device_sn",
        "pk_field": "ticket_no",
        "pk_strategy": "primary_key",
        "sync_source": "bms",
        "category": "工单与客服",
    },
    {
        "code": "refund",
        "model": Refund,
        "name_cn": "退费单管理",
        "time_field": "created_at_raw",
        "sn_field": "device_sn",
        "pk_field": "refund_no",
        "pk_strategy": "primary_key",
        "sync_source": "bms",
        "category": "现金流",
    },
    {
        "code": "wallet",
        "model": WalletWithdrawal,
        "name_cn": "提现操作记录",
        "time_field": "op_time",
        "sn_field": "device_sn",
        "pk_field": None,
        "pk_strategy": "none",
        "sync_source": "bms",
        "category": "现金流",
    },
    {
        "code": "merchant_refund",
        "model": MerchantRefund,
        "name_cn": "商户号退款",
        "time_field": "trade_time",
        "sn_field": "device_no",
        "pk_field": "wx_refund_no",
        "pk_strategy": "primary_key",
        "category": "现金流",
    },
    # 占位数据源(规划中,暂未建表实现):仅数据查看侧边栏占位,不可导入/同步/查看明细
    {
        "code": "shenmao_refund",
        "name_cn": "闪猫退款",
        "category": "现金流",
        "placeholder": True,
    },
    {
        "code": "other_refund",
        "name_cn": "其他退款",
        "category": "现金流",
        "placeholder": True,
    },
    {
        "code": "cs_record",
        "model": CsRecord,
        "name_cn": "智能助手处理记录",
        "time_field": "operation_time",
        "sn_field": "device_sn",
        "pk_field": None,
        "pk_strategy": "none",
        "category": "工单与客服",
        "sync_source": "bms",
    },
    {
        "code": "external_complaint",
        "model": ExternalComplaint,
        "name_cn": "外部投诉(315/监督局)",
        "time_field": "complaint_date",
        "sn_field": "device_sn",
        "pk_field": "complaint_no",
        "pk_strategy": "upsert",
        "category": "投诉登记",
    },
    {
        "code": "card_complaint",
        "model": CardComplaint,
        "name_cn": "卡商群投诉",
        "time_field": "complaint_date",
        "sn_field": "device_sn",
        "pk_field": None,
        "pk_strategy": "none",
        "category": "投诉登记",
    },
    {
        "code": "device_combo",
        "model": DeviceCombo,
        "name_cn": "设备套餐",
        "time_field": "created_at_raw",
        "sn_field": "device_sn",
        "pk_field": "recharge_order_no",
        "pk_strategy": "primary_key",
        "sync_source": "bms",
        "category": "基础数据",
    },
]

# 字段级 override：(source_code, field_name) → {role, agg, enum, is_filterable, is_pk}
# 未列出的字段默认 role=dimension, is_filterable=True, is_pk=col.primary_key
FIELD_OVERRIDES: dict[tuple[str, str], dict] = {
    # refund_wide (物化宽表,跨表关联预计算结果)
    ("refund_wide", "refund_no"): {"role": "dimension", "is_pk": True, "is_filterable": False},
    ("refund_wide", "refund_method"): {"role": "category", "enum": ["线上", "线下", "钱包"]},
    ("refund_wide", "status"): {"role": "category", "enum": ["申请中", "已退款", "无需退款"]},
    ("refund_wide", "refund_reason"): {"role": "category"},
    ("refund_wide", "operator"): {"role": "person"},
    ("refund_wide", "auditor"): {"role": "person"},
    ("refund_wide", "device_sn"): {"role": "sn"},
    ("refund_wide", "refund_amount"): {"role": "amount", "agg": "sum"},
    ("refund_wide", "created_at_raw"): {"role": "time"},
    # ticket 关联字段
    ("refund_wide", "ticket_no"): {"role": "dimension", "is_filterable": False},
    ("refund_wide", "service_item"): {"role": "category"},
    ("refund_wide", "service_category"): {"role": "category"},
    # ticket_finish_time 同 ticket.finish_time,89% 不可靠,隐藏防误用(详见 docs/数据时间字段使用规范.md)
    ("refund_wide", "ticket_finish_time"): {
        "role": "time", "is_filterable": False,
        "name_cn": "⚠️工单完成时间(不可靠,89%被批量覆盖)",
    },
    ("refund_wide", "ticket_handle_duration"): {"role": "measure", "agg": "avg"},
    ("refund_wide", "ticket_handler"): {"role": "person"},
    # merchant_refund 关联字段(mr_ 前缀避免与 refund 主表字段冲突)
    ("refund_wide", "wx_refund_no"): {"role": "dimension", "is_filterable": False},
    ("refund_wide", "mr_trade_time"): {"role": "time"},
    ("refund_wide", "mr_refund_amount"): {"role": "amount", "agg": "sum"},
    ("refund_wide", "mr_refund_status"): {"role": "category"},
    ("refund_wide", "mr_fee"): {"role": "amount", "agg": "sum"},
    ("refund_wide", "merchant_id"): {"role": "category"},
    # wallet 关联字段
    ("refund_wide", "wallet_id"): {"role": "dimension", "is_filterable": False},
    ("refund_wide", "wallet_op_time"): {"role": "time"},
    ("refund_wide", "wallet_op_amount"): {"role": "amount", "agg": "sum"},
    # external_complaint 关联字段(分类规则1用)
    ("refund_wide", "has_external_complaint"): {"role": "category", "enum": [True, False]},
    ("refund_wide", "external_complaint_count"): {"role": "measure", "agg": "sum"},
    ("refund_wide", "external_complaint_latest_time"): {"role": "time"},
    ("refund_wide", "matched_at"): {"role": "dimension", "is_filterable": False},
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
    # updated_at_raw 被 SYS 03:15 批量任务覆盖,非真实业务时间,隐藏防误用(详见 docs/数据时间字段使用规范.md)
    ("refund", "updated_at_raw"): {
        "role": "time", "is_filterable": False,
        "name_cn": "⚠️原始更新时间(被SYS批量覆盖,请用创建时间)",
    },
    # wallet
    ("wallet", "op_amount"): {"role": "amount", "agg": "sum"},
    ("wallet", "wallet_balance"): {"role": "amount", "agg": "sum"},
    ("wallet", "operator"): {"role": "person"},
    ("wallet", "device_sn"): {"role": "sn"},
    ("wallet", "op_type"): {"role": "category", "enum": ["用户提现", "订单退费转入"]},
    ("wallet", "op_time"): {"role": "time"},
    ("wallet", "account_id"): {"role": "category"},
    # device_combo (设备套餐)
    ("device_combo", "recharge_order_no"): {"role": "dimension", "is_pk": True, "is_filterable": False},
    ("device_combo", "device_sn"): {"role": "sn"},
    ("device_combo", "package_name"): {"role": "category"},
    ("device_combo", "package_type"): {"role": "category"},
    ("device_combo", "traffic_type"): {"role": "category"},
    ("device_combo", "merchant_name"): {"role": "category"},
    ("device_combo", "actual_amount"): {"role": "amount", "agg": "sum"},
    ("device_combo", "start_date"): {"role": "time"},
    ("device_combo", "end_date"): {"role": "time"},
    ("device_combo", "updated_at_raw"): {"role": "time"},
    ("device_combo", "created_at_raw"): {"role": "time"},
    ("device_combo", "valid_days"): {"role": "measure", "agg": "avg"},
    ("device_combo", "creator"): {"role": "category"},
    ("device_combo", "available_traffic"): {"role": "dimension", "is_filterable": False},
    ("device_combo", "used_traffic"): {"role": "dimension", "is_filterable": False},
    ("device_combo", "remaining_traffic"): {"role": "dimension", "is_filterable": False},
    ("device_combo", "status"): {"role": "category"},
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
    # finish_time 89% 非真实完成时间(50%被BMS批量覆盖/23%同步填充/17%同瞬填充),隐藏防误用(详见 docs/数据时间字段使用规范.md)
    ("ticket", "finish_time"): {
        "role": "time", "is_filterable": False,
        "name_cn": "⚠️完成时间(不可靠,89%被批量覆盖,请用登记时间)",
    },
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
    # external_complaint (监督局/315)
    ("external_complaint", "complaint_no"): {"role": "dimension", "is_pk": True, "is_filterable": False},
    ("external_complaint", "complaint_date"): {"role": "time"},
    ("external_complaint", "handle_time"): {"role": "time"},
    ("external_complaint", "device_sn"): {"role": "sn"},
    ("external_complaint", "follower"): {"role": "person"},
    ("external_complaint", "reception_cs"): {"role": "person"},
    ("external_complaint", "complaint_category"): {"role": "category"},
    ("external_complaint", "platform"): {"role": "category"},
    ("external_complaint", "company_entity"): {"role": "category"},
    ("external_complaint", "result"): {"role": "category"},
    ("external_complaint", "solution"): {"role": "category"},
    ("external_complaint", "keyword"): {"role": "category"},
    ("external_complaint", "package_name"): {"role": "category"},
    ("external_complaint", "package_price"): {"role": "amount", "agg": "sum"},
    ("external_complaint", "actual_pay_amount"): {"role": "amount", "agg": "sum"},
    ("external_complaint", "negotiate_refund_amount"): {"role": "amount", "agg": "sum"},
    ("external_complaint", "problem"): {"role": "dimension", "is_filterable": False},
    ("external_complaint", "handle_detail"): {"role": "dimension", "is_filterable": False},
    ("external_complaint", "internal_analysis"): {"role": "dimension", "is_filterable": False},
    ("external_complaint", "external_analysis"): {"role": "dimension", "is_filterable": False},
    # card_complaint (卡商群)
    ("card_complaint", "complaint_date"): {"role": "time"},
    ("card_complaint", "device_sn"): {"role": "sn"},
    ("card_complaint", "handler"): {"role": "person"},
    ("card_complaint", "complaint_group"): {"role": "category"},
    ("card_complaint", "problem_category"): {"role": "category"},
    ("card_complaint", "result"): {"role": "category"},
    ("card_complaint", "solution"): {"role": "category"},
    ("card_complaint", "problem"): {"role": "dimension", "is_filterable": False},
    ("card_complaint", "handle_result"): {"role": "dimension", "is_filterable": False},
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


def _clean_comment(comment: str | None) -> str | None:
    """清理字段注释，提取纯中文名称（去掉冒号后的示例值、括号等）"""
    if not comment:
        return None
    # 去掉冒号及其后面的内容
    if ':' in comment:
        comment = comment.split(':', 1)[0].strip()
    # 去掉括号及其内容
    import re
    comment = re.sub(r'\([^)]*\)', '', comment).strip()
    # 去掉特殊字符后面的内容
    for char in ['/', '\\', '...']:
        if char in comment:
            comment = comment.split(char, 1)[0].strip()
    return comment if comment else None


def ensure_data_sources_registered(db: Session) -> dict:
    """幂等注册：表已存在则更新元数据，字段集按模型反射重建"""
    stats = {"sources": 0, "fields": 0}
    for seed in DATA_SOURCE_SEEDS:
        is_placeholder = seed.get("placeholder", False)

        # 反射字段集(占位项无 model,跳过反射)
        reflected: list[dict] = []
        if not is_placeholder:
            mapper = inspect(seed["model"])
            for col in mapper.columns:
                if col.name in _EXCLUDED_FIELDS:
                    continue
                override = FIELD_OVERRIDES.get((seed["code"], col.name), {})
                reflected.append({
                    "field_name": col.name,
                    # name_cn 优先用 override(用于给不可靠字段加 ⚠️ 警告前缀),无则反射模型 comment
                    "field_name_cn": override.get("name_cn") or _clean_comment(col.comment) or None,
                    "data_type": _infer_data_type(col),
                    "semantic_role": override.get("role", "dimension"),
                    "agg_func": override.get("agg"),
                    "is_filterable": override.get("is_filterable", True),
                    "is_pk": override.get("is_pk", col.primary_key),
                    "enum_values": override.get("enum"),
                    "sort_order": len(reflected),
                })

        # 表名/状态:占位项用占位表名 + placeholder 状态(无物理表)
        table_name = f"_placeholder_{seed['code']}" if is_placeholder else seed["model"].__tablename__
        status = "placeholder" if is_placeholder else "active"
        time_field = None if is_placeholder else seed["time_field"]
        sn_field = None if is_placeholder else seed["sn_field"]
        pk_field = None if is_placeholder else seed["pk_field"]
        pk_strategy = "none" if is_placeholder else seed["pk_strategy"]

        # upsert DataSource
        ds = db.scalar(select(DataSource).where(DataSource.code == seed["code"]))
        if ds:
            ds.table_name = table_name
            ds.name_cn = seed["name_cn"]
            ds.time_field = time_field
            ds.sn_field = sn_field
            ds.pk_field = pk_field
            ds.pk_strategy = pk_strategy
            ds.sync_source = seed.get("sync_source")
            ds.category = seed.get("category")
            ds.status = status
        else:
            ds = DataSource(
                code=seed["code"],
                table_name=table_name,
                name_cn=seed["name_cn"],
                time_field=time_field,
                sn_field=sn_field,
                pk_field=pk_field,
                pk_strategy=pk_strategy,
                sync_source=seed.get("sync_source"),
                category=seed.get("category"),
                status=status,
            )
            db.add(ds)
            db.flush()
        stats["sources"] += 1

        # 字段集重建：先删旧字段（含已废弃字段），再 upsert 新字段集。
        # 用 on_conflict_do_update 而非裸 INSERT：多 worker 并发注册时（gunicorn 4 worker
        # 启动 lifespan/refresh 并发触发）会跨事务撞 uq_source_field 唯一约束，裸 INSERT
        # 直接报 duplicate key；on_conflict 让后到事务更新先到事务的行，幂等不报错。
        db.execute(delete(DataSourceField).where(DataSourceField.source_id == ds.id))
        if reflected:
            rows = [{"source_id": ds.id, **f} for f in reflected]
            stmt = pg_insert(DataSourceField).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["source_id", "field_name"],
                set_={
                    "field_name_cn": stmt.excluded.field_name_cn,
                    "data_type": stmt.excluded.data_type,
                    "semantic_role": stmt.excluded.semantic_role,
                    "agg_func": stmt.excluded.agg_func,
                    "is_filterable": stmt.excluded.is_filterable,
                    "is_pk": stmt.excluded.is_pk,
                    "enum_values": stmt.excluded.enum_values,
                    "sort_order": stmt.excluded.sort_order,
                },
            )
            db.execute(stmt)
        stats["fields"] += len(reflected)
    db.commit()
    return stats

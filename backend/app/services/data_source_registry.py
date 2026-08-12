"""数据源元数据注册 - 反射 + Override 混合

字段集从 SQLAlchemy 模型反射获得（永远与模型对齐），
语义角色/聚合/枚举/可筛选 由 FIELD_OVERRIDES 人工维护。
新表加入：建表迁移 + 在 DATA_SOURCE_SEEDS 加一行 + 调 refresh。
"""

import logging

from sqlalchemy import Boolean, DateTime, Float, Integer, Numeric, delete, inspect, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.models.card_complaint import CardComplaint
from app.models.cs_record import CsRecord
from app.models.data_source import DataSource, DataSourceField
from app.models.device import DeviceTag, DeviceTagBinding
from app.models.device_combo import DeviceCombo
from app.models.external_complaint import ExternalComplaint
from app.models.merchant_refund import MerchantRefund
from app.models.refund import Refund
from app.models.refund_wide import RefundWide
from app.models.scrm_operator import ScrmOperator
from app.models.scrm_session import ScrmSession
from app.models.scrm_session_transfer import ScrmSessionTransfer
from app.models.shanmao_refund import ShanmaoRefund
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
        "name_cn": "商户号现金流",
        "time_field": "trade_time",
        "sn_field": "device_no",
        "pk_field": None,
        "pk_strategy": "none",
        "category": "现金流",
    },
    {
        "code": "shanmao_refund",
        "model": ShanmaoRefund,
        "name_cn": "闪猫退款管理",
        "time_field": "apply_time",
        "sn_field": "device_sn",
        "pk_field": "refund_no",
        "pk_strategy": "primary_key",
        "sync_source": "bms",
        "category": "现金流",
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
    {
        "code": "device_tag",
        "model": DeviceTag,
        "name_cn": "设备标签定义",
        "time_field": "created_at",
        "sn_field": None,
        "pk_field": "id",
        "pk_strategy": "primary_key",
        "sync_source": "bms",
        "category": "基础数据",
        "description": "BMS 平台维护的标签字典(标签名/类型/颜色/描述)",
    },
    {
        "code": "device_tag_binding",
        "model": DeviceTagBinding,
        "name_cn": "设备打标明细",
        "time_field": "created_at",
        "sn_field": "device_sn",
        "pk_field": None,
        "pk_strategy": "none",
        "sync_source": "bms",
        "category": "基础数据",
        "description": "每台设备被打的所有标签(设备SN × 标签ID 多对多,含打标人)",
    },
    # ===== SCRM 微信聚合平台(客服接待评估报表) =====
    {
        "code": "scrm_operator",
        "model": ScrmOperator,
        "name_cn": "SCRM-客服账号列表",
        "time_field": "last_active_at",
        "pk_field": "operator_id",
        "pk_strategy": "upsert",
        "sync_source": "scrm",
        "category": "工单与客服",
        "description": "SCRM平台侧所有客服账号(含部门/状态/最后活跃时间),用于人员别名归一",
    },
    {
        "code": "scrm_session",
        "model": ScrmSession,
        "name_cn": "SCRM-客服接待会话",
        "time_field": "start_time",
        "pk_field": "session_id",
        "pk_strategy": "upsert",
        "sync_source": "scrm",
        "category": "工单与客服",
        "description": "私聊/群聊/虚拟会话三合一(含首响/均响/消息数/转接次数,核心绩效数据源)",
    },
    {
        "code": "scrm_session_transfer",
        "model": ScrmSessionTransfer,
        "name_cn": "SCRM-会话转接明细",
        "time_field": "transfer_time",
        "pk_field": None,
        "pk_strategy": "none",
        "sync_source": "scrm",
        "category": "工单与客服",
        "description": "会话转接事件流(转出→转入+时间+原因),用于转接率、工作量拆分归属",
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
    # shanmao_refund (闪猫退款)
    ("shanmao_refund", "refund_no"): {"role": "dimension", "is_pk": True, "is_filterable": False},
    ("shanmao_refund", "refund_amount"): {"role": "amount", "agg": "sum"},
    ("shanmao_refund", "actual_amount"): {"role": "amount", "agg": "sum"},
    ("shanmao_refund", "applicant"): {"role": "person"},
    ("shanmao_refund", "auditor"): {"role": "person"},
    ("shanmao_refund", "operator"): {"role": "person"},
    ("shanmao_refund", "device_sn"): {"role": "sn"},
    ("shanmao_refund", "merchant_name"): {"role": "category"},
    ("shanmao_refund", "status"): {"role": "category"},
    ("shanmao_refund", "refund_method"): {"role": "category"},
    ("shanmao_refund", "refund_reason"): {"role": "category"},
    ("shanmao_refund", "package_name"): {"role": "category"},
    ("shanmao_refund", "apply_time"): {"role": "time", "name_cn": "申请时间(主时间)"},
    ("shanmao_refund", "audit_time"): {"role": "time", "name_cn": "审核时间"},
    ("shanmao_refund", "refund_time"): {"role": "time", "name_cn": "退款完成时间"},
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
    # device_tag (标签定义表)
    ("device_tag", "id"): {"role": "dimension", "is_pk": True, "is_filterable": True, "name_cn": "标签ID"},
    ("device_tag", "tag_name"): {"role": "category", "name_cn": "标签名称"},
    ("device_tag", "tag_type"): {"role": "category", "enum": ["preset", "custom"], "name_cn": "标签类型(preset预设/custom自定义)"},
    ("device_tag", "color"): {"role": "dimension", "is_filterable": False, "name_cn": "标签颜色"},
    ("device_tag", "description"): {"role": "dimension", "is_filterable": False, "name_cn": "标签描述"},
    ("device_tag", "created_at"): {"role": "time", "name_cn": "创建时间"},
    # device_tag_binding (设备打标明细 / device_tag_rel 同表)
    ("device_tag_binding", "device_sn"): {"role": "sn", "name_cn": "设备SN"},
    ("device_tag_binding", "tag_id"): {"role": "dimension", "is_pk": True, "is_filterable": True, "name_cn": "标签ID"},
    ("device_tag_binding", "created_by"): {"role": "person", "name_cn": "打标人"},
    ("device_tag_binding", "created_at"): {"role": "time", "name_cn": "打标时间"},
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
    # ── 催单 & 派单 8 个新字段 (2026-08-03) ──
    ("ticket", "urge_count"): {"role": "measure", "agg": "sum", "name_cn": "催单次数"},
    ("ticket", "urge_status"): {"role": "category", "name_cn": "催单状态"},
    ("ticket", "last_dispatch_time"): {"role": "time", "name_cn": "最后派单时间"},
    ("ticket", "assignee"): {"role": "person", "name_cn": "分配人员"},
    ("ticket", "contact_person"): {"role": "person", "name_cn": "联系人员"},
    ("ticket", "dispatcher"): {"role": "person", "name_cn": "派发人员"},
    ("ticket", "last_urge_by"): {"role": "person", "name_cn": "最后催单人"},
    ("ticket", "last_urge_time"): {"role": "time", "name_cn": "最后催单时间"},
    # merchant_refund
    ("merchant_refund", "wx_refund_no"): {"role": "dimension", "is_filterable": True},
    ("merchant_refund", "merchant_refund_no"): {"role": "dimension", "is_filterable": True},
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
    # ===== SCRM: scrm_operator (客服账号快照) =====
    ("scrm_operator", "operator_id"):   {"role": "dimension", "is_pk": True, "is_filterable": True, "name_cn": "客服ID(SCRM)"},
    ("scrm_operator", "operator_name"): {"role": "person", "name_cn": "客服显示名"},
    ("scrm_operator", "person_id"):     {"role": "dimension", "is_filterable": True, "name_cn": "关联PersonID(归一后)"},
    ("scrm_operator", "department"):    {"role": "category", "name_cn": "SCRM部门"},
    ("scrm_operator", "position"):      {"role": "category", "name_cn": "岗位"},
    ("scrm_operator", "status"):        {"role": "category", "name_cn": "在线状态"},
    ("scrm_operator", "last_active_at"): {"role": "time", "name_cn": "最后活跃时间"},
    ("scrm_operator", "receive_count_today"): {"role": "measure", "agg": "sum", "name_cn": "当日接待量(快照值)"},
    # ===== SCRM: scrm_session (会话主表,绩效核心数据源) =====
    ("scrm_session", "session_id"):   {"role": "dimension", "is_pk": True, "is_filterable": True, "name_cn": "会话ID"},
    ("scrm_session", "session_type"): {"role": "category", "enum": ["private", "group", "virtual"], "name_cn": "会话类型"},
    ("scrm_session", "operator_id"):  {"role": "dimension", "is_filterable": True, "name_cn": "客服ID(SCRM)"},
    ("scrm_session", "operator_name"): {"role": "person", "name_cn": "客服名(可归一)"},
    ("scrm_session", "person_id"):    {"role": "dimension", "is_filterable": True, "name_cn": "PersonID(归一后,建议用此字段分组)"},
    ("scrm_session", "customer_id"):  {"role": "dimension", "is_filterable": True, "name_cn": "客户ID"},
    ("scrm_session", "customer_name"): {"role": "dimension", "is_filterable": False, "name_cn": "客户昵称"},
    ("scrm_session", "group_id"):     {"role": "dimension", "is_filterable": True, "name_cn": "群聊ID"},
    ("scrm_session", "start_time"):   {"role": "time", "name_cn": "会话开始时间(主时间)"},
    ("scrm_session", "end_time"):     {"role": "time", "name_cn": "会话结束时间"},
    ("scrm_session", "duration_sec"): {"role": "measure", "agg": "avg", "name_cn": "会话时长(秒)"},
    ("scrm_session", "msg_count_customer"): {"role": "measure", "agg": "sum", "name_cn": "客户消息数"},
    ("scrm_session", "msg_count_staff"):    {"role": "measure", "agg": "sum", "name_cn": "客服消息数"},
    ("scrm_session", "first_response_sec"): {"role": "measure", "agg": "avg", "name_cn": "首次响应时长(秒),核心质量指标"},
    ("scrm_session", "avg_response_sec"):   {"role": "measure", "agg": "avg", "name_cn": "平均响应时长(秒),核心质量指标"},
    ("scrm_session", "transfer_count"):     {"role": "measure", "agg": "sum", "name_cn": "转接次数(>0=发生转接)"},
    ("scrm_session", "question_type_id"):   {"role": "category", "name_cn": "问题类型ID"},
    ("scrm_session", "question_type_name"): {"role": "category", "name_cn": "问题类型(映射难度系数:咨询1.0/套餐1.8/退款2.2/投诉3.0)"},
    ("scrm_session", "sync_status"):   {"role": "category", "enum": ["open", "closed", "reconciled"], "name_cn": "同步状态"},
    # ===== SCRM: scrm_session_transfer (转接事件流) =====
    ("scrm_session_transfer", "session_id"): {"role": "dimension", "is_filterable": True, "name_cn": "会话ID"},
    ("scrm_session_transfer", "from_operator_id"):   {"role": "dimension", "is_filterable": True, "name_cn": "转出客服ID"},
    ("scrm_session_transfer", "from_operator_name"): {"role": "person", "name_cn": "转出客服名"},
    ("scrm_session_transfer", "to_operator_id"):     {"role": "dimension", "is_filterable": True, "name_cn": "转入客服ID"},
    ("scrm_session_transfer", "to_operator_name"):   {"role": "person", "name_cn": "转入客服名"},
    ("scrm_session_transfer", "transfer_time"): {"role": "time", "name_cn": "转接时间"},
    ("scrm_session_transfer", "transfer_reason"): {"role": "category", "name_cn": "转接原因"},
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
        time_field = seed.get("time_field") if not is_placeholder else None
        sn_field = seed.get("sn_field") if not is_placeholder else None
        pk_field = seed.get("pk_field") if not is_placeholder else None
        pk_strategy = seed.get("pk_strategy", "none") if not is_placeholder else "none"

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

    # 清理已从 seed 移除的旧数据源(如 other_refund 占位项),避免数据查看页继续显示"规划中"
    valid_codes = {s["code"] for s in DATA_SOURCE_SEEDS}
    obsolete = db.execute(
        select(DataSource).where(~DataSource.code.in_(valid_codes))
    ).scalars().all()
    for old_ds in obsolete:
        logger.info(f"清理已移除的数据源: {old_ds.code} ({old_ds.name_cn})")
        db.delete(old_ds)

    db.commit()
    return stats

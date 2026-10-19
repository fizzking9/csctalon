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

from app.models.bms_order import BmsOrder
from app.models.card_complaint import CardComplaint
from app.models.cash_flow_wide import CashFlowWide
from app.models.cs_record import CsRecord
from app.models.data_source import DataSource, DataSourceField
from app.models.device import DeviceTag, DeviceTagBinding
from app.models.device_combo import DeviceCombo
from app.models.device_flow_usage import DeviceFlowUsage
from app.models.external_complaint import ExternalComplaint
from app.models.manual_refund import ManualRefund
from app.models.merchant_refund import MerchantRefund
from app.models.refund import Refund
from app.models.refund_record_report import RefundRecordReport
from app.models.refund_wide import RefundWide
from app.models.return_tracking import ReturnTracking
from app.models.scrm_operator import ScrmOperator
from app.models.scrm_session import ScrmSession
from app.models.scrm_session_transfer import ScrmSessionTransfer
from app.models.shanmao_refund import ShanmaoRefund
from app.models.ticket import ServiceTicket
from app.models.wallet import WalletWithdrawal

# #333: 采纳三态/规则码枚举单一来源在采纳作业模块
from app.services.governance.refund_adoption import ADOPT_REASON_ENUM, ADOPT_STATE_ENUM

# ─────────────────────────────────────────────────────────────────────
# 数据查看树：分组口径与命名约束（工单 #100）
#
# 设计说明（Design Rationale）
#   目的：运维/数据/主管在「数据采集 → 数据查看」里按业务口径定位到**物理表**并下钻原始行。
#   逻辑：
#     分组按「钱从哪出 → 谁在抱怨 → 谁在处理 → 交易售后 → 设备底座」五段切，
#     与既有口径文档对齐（A~E 见 A-20260908-004；投诉三源见 投诉渠道口径与对账说明.md）。
#   口径：CATEGORY_ORDER 是**展示顺序唯一来源**；sort_order 决定组内顺序；
#         投影项见 DATA_SOURCE_PROJECTIONS（同表别名，不重复注册数据源）。
#   取舍：
#     · 不做「全库表目录」—— 只列可下钻的原始业务表；工作流/配置/日志/中间表各有功能页，
#       由页面 ⓘ 说明边界（否则树会被 80 张表稀释，失去"选一张表看数据"的用途）。
#     · 不把 A~E 做成 5 个独立数据源 —— 它们本就是既有表，另立 code 会导致同表双注册、
#       统计重复计数（违反 数据采集合并去重机制说明.md）。
#     · 命名不引入英文表名做前缀 —— 中文前缀只需回答"这是哪一路/哪一类"，表名由头部 tag 显示。
#
# 命名约束（详见 docs/standards/数据源命名与分组规范.md）：
#   1. code ↔ table_name ↔ name_cn 一一对应，name_cn 全树唯一
#   2. 同类表统一前缀：`A·`~`E·`（经营五路） / `投诉·` / `SCRM·` / `设备·`
#   3. name_cn 形如 `[域前缀·]<中文语义>`，禁止出现 code 原文
# ─────────────────────────────────────────────────────────────────────

# 数据查看左侧分组的展示顺序（唯一来源，前端 CATEGORY_ORDER 必须与此逐字一致）
CATEGORY_ORDER: list[str] = [
    "经营与对账",  # 钱从哪出：A~E 五路 + 物化宽表
    "投诉",  # 谁在抱怨：工单侧投影 + 外部315 + 卡商群（三源分桶口径）
    "工单与客服",  # 谁在处理：服务请求真身 + AI 助手 + SCRM 接待
    "交易与售后",  # 交易后链路：订单台账 + 退货跟踪
    "基础数据",  # 设备底座：套餐/标签/流量
]

# 表级种子：code → 模型 + 元数据
# 核心数据表约束:
# 1. 服务请求(服务工单):包含退款和投诉等服务工单,站在设备视角
# 2. 退费单:运营视角的费用操作
# 3. 提现操作记录:会员钱包的操作记录
# 4. 外部投诉:315和质量监督局的投诉
DATA_SOURCE_SEEDS: list[dict] = [
    # ── 经营五路 A~E（口径来源：A-20260908-004 现金流水统一宽表，LOCKED） ──
    {
        "code": "refund",
        "model": Refund,
        "name_cn": "A·退费单(线上直退)",
        "time_field": "created_at_raw",
        "sn_field": "device_sn",
        "pk_field": "refund_no",
        "pk_strategy": "primary_key",
        "sync_source": "bms",
        "category": "经营与对账",
        "sort_order": 10,
        "description": "【A 路】BMS 退费单台账,口径 = refund_method='线上' 且 status='已退款'。经营出款四路之一(A+B+C+D),与 E 路(退款记录报表)逐笔勾稽。",
    },
    {
        "code": "wallet",
        "model": WalletWithdrawal,
        "name_cn": "B·钱包提现(用户取现)",
        "time_field": "op_time",
        "sn_field": "device_sn",
        "pk_field": None,
        "pk_strategy": "none",
        "sync_source": "bms",
        "category": "经营与对账",
        "sort_order": 20,
        "description": "【B 路】会员钱包操作记录,口径 = op_type='用户提现' 且备注非失败/冻结关键词。经营出款四路之一;含「订单退费转入」等非现金退出行,不计入 B 路。",
    },
    {
        "code": "shanmao_refund",
        "model": ShanmaoRefund,
        "name_cn": "C·闪猫退款",
        "time_field": "apply_time",
        "sn_field": "device_sn",
        "pk_field": "refund_no",
        "pk_strategy": "primary_key",
        "sync_source": "bms",
        "category": "经营与对账",
        "sort_order": 30,
        "description": "【C 路】闪猫加速平台退款申请,口径 = status='已退款',金额 = actual_amount。经营出款四路之一。",
    },
    {
        "code": "manual_refund",
        "model": ManualRefund,
        "name_cn": "D·手动退款登记",
        "time_field": "register_time",
        "sn_field": "device_sn",
        "pk_field": None,
        "pk_strategy": "none",
        "category": "经营与对账",
        "sort_order": 40,
        "description": "【D 路】客服在商户号后台手动操作的退款登记台账(商户号手机退款处理登记)。是否已退款=是 的行构成 D 路(手动退款)经营出款;未退款行 = 登记蓄水(水位)。已排除 quality_status IN ('dup_residual','ignored') 的治理行。",
    },
    {
        "code": "refund_record_report",
        "model": RefundRecordReport,
        "name_cn": "E·退款记录报表(对账基准)",
        "time_field": "refund_time",
        "sn_field": "device_sn",
        "pk_field": None,
        "pk_strategy": "none",
        "sync_source": "bms",
        "category": "经营与对账",
        "sort_order": 50,
        "description": "【E 路】BMS 财务退款记录报表(线上真实退款水单),口径 = refund_status IN ('退款成功','成功','SUCCESS')。⚠️ E 是**对账基准**而非经营口径,不与 A+B+C+D 累加;两侧差额 = 商户侧已扣款但未登记四路的退款(见 投诉渠道口径与对账说明.md 的缺口对账)。",
    },
    {
        "code": "cash_flow_wide",
        "model": CashFlowWide,
        "name_cn": "聚合·经营四路出款(物化)",
        "time_field": "pay_time",
        "sn_field": "device_sn",
        "pk_field": "id",
        "pk_strategy": "primary_key",
        "category": "经营与对账",
        "sort_order": 60,
        # internal=True 标记本表由系统物化构建,不来自 BMS/手动上传,UI 上禁止导入/同步
        "internal": True,
        "description": "A+B+C+D 四路**逐笔**物化为一行(route 列区分路线),供对账与快速多维分析。E 不进本表;e_matched 仅为对账标记。口径与 analytics._cash_routes_total 强一致。",
    },
    {
        "code": "refund_wide",
        "model": RefundWide,
        "name_cn": "视图·A路退费关联宽表(物化)",
        "time_field": "created_at_raw",
        "sn_field": "device_sn",
        "pk_field": "refund_no",
        "pk_strategy": "primary_key",
        "category": "经营与对账",
        "sort_order": 70,
        # internal=True 标记本表由系统物化构建,不来自 BMS/手动上传,UI 上禁止导入/同步
        "internal": True,
        "description": "A 路退费单与工单/钱包/外部投诉的**预关联结果**(引擎禁 JOIN,故物化)。行范围 = 线上+已退款 ∪ 带 wallet 关联 ∪ 设备SN有外部投诉(见 A-20260908-001/002)。",
    },
    {
        "code": "merchant_refund",
        "model": MerchantRefund,
        "name_cn": "⚠️旧·商户号款项(已停用)",
        "time_field": "trade_time",
        "sn_field": "device_no",
        "pk_field": None,
        "pk_strategy": "none",
        "category": "经营与对账",
        "sort_order": 80,
        "description": "⚠️ **已停用作 E 统计取数**(2026-09-05 起由 refund_record_reports 替代,见 数据时间字段使用规范.md v1.1)。仅保留供 matcher/宽表历史关联,不要用于任何金额统计。",
    },
    {
        "code": "ticket",
        "model": ServiceTicket,
        "name_cn": "工单·服务请求(投诉+退款)",
        "time_field": "register_time",
        "sn_field": "device_sn",
        "pk_field": "ticket_no",
        "pk_strategy": "primary_key",
        "sync_source": "bms",
        "category": "工单与客服",
        "sort_order": 10,
        "description": "BMS 服务请求工单**真身**(本表唯一数据源)。投诉类判据 = service_category 空白或='投诉'(BMS 导出未选类别即空白,属已知特性),见 投诉渠道口径与对账说明.md §1.1。「投诉」分组下的同名项是本表的**投影**。",
    },
    {
        "code": "cs_record",
        "model": CsRecord,
        "name_cn": "客服·智能助手处理记录",
        "time_field": "operation_time",
        "sn_field": "device_sn",
        "pk_field": None,
        "pk_strategy": "none",
        "category": "工单与客服",
        "sort_order": 20,
        "sync_source": "bms",
        "description": "ai-acc 平台智能助手(AI客服)处理记录,含问题分类/解决方案/用户是否接受。AI 接待口径见 A-20260913-001。",
    },
    {
        "code": "external_complaint",
        "model": ExternalComplaint,
        "name_cn": "投诉·外部315/监督局",
        "time_field": "complaint_date",
        "sn_field": "device_sn",
        "pk_field": "dedup_hash",
        "pk_strategy": "upsert",
        "sync_source": "workbuddy",
        "category": "投诉",
        "sort_order": 20,
        "description": "投诉三源之一（外部投诉桶）。315投诉/市监局/监所等外部渠道明细登记,由 workbuddy/共享文档采集。通过设备SN与 service_tickets 外部投诉类工单关联,作为工单口径的更详细补充。归类判据见 投诉渠道口径与对账说明.md §1.2。",
    },
    {
        "code": "card_complaint",
        "model": CardComplaint,
        "name_cn": "投诉·卡商群",
        "time_field": "complaint_date",
        "sn_field": "device_sn",
        "pk_field": "dedup_hash",
        "pk_strategy": "upsert",
        "sync_source": "workbuddy",
        "category": "投诉",
        "sort_order": 30,
        "description": "投诉三源之一（(外)卡商桶）。卡商群内投诉明细登记,由 workbuddy/共享文档采集。⚠️ (外)卡商**完全独立统计,不纳入投诉总量**（见 投诉渠道口径与对账说明.md §1.4）。",
    },
    {
        "code": "device_combo",
        "model": DeviceCombo,
        "name_cn": "设备·套餐",
        "time_field": "created_at_raw",
        "sn_field": "device_sn",
        "pk_field": "recharge_order_no",
        "pk_strategy": "primary_key",
        "sync_source": "bms",
        "category": "基础数据",
        "sort_order": 10,
        "description": "BMS 设备套餐(充值/续费)台账,含套餐名/流量/有效期/实付金额。",
    },
    {
        "code": "device_tag",
        "model": DeviceTag,
        "name_cn": "设备·标签字典",
        "time_field": "created_at",
        "sn_field": None,
        "pk_field": "id",
        "pk_strategy": "primary_key",
        "sync_source": "bms",
        "category": "基础数据",
        "sort_order": 20,
        "description": "BMS 平台维护的标签字典(标签名/类型/颜色/描述)。仅定义,不含打标关系。",
    },
    {
        "code": "device_tag_binding",
        "model": DeviceTagBinding,
        "name_cn": "设备·打标明细",
        "time_field": "created_at",
        "sn_field": "device_sn",
        "pk_field": None,
        "pk_strategy": "none",
        "sync_source": "bms",
        "category": "基础数据",
        "sort_order": 30,
        "description": "每台设备被打的所有标签(设备SN × 标签ID 多对多,含打标人)。⚠️ 物理表名与 code 不一致(表=device_tag_rel,与 ORM 多对多关联表同表双映射),属命名规范显式例外。",
    },
    {
        "code": "device_flow_usage",
        "model": DeviceFlowUsage,
        "name_cn": "设备·月流量",
        "time_field": "fetched_at",
        "sn_field": "device_sn",
        "pk_field": None,
        "pk_strategy": "none",
        "category": "基础数据",
        "sort_order": 40,
        "description": "设备月流量使用(单SN×月汇总,设备审计;来自设备流量 HTTP API)。唯一键 = SN+月份。",
    },
    # ===== 交易与售后（原错放在「现金流」/「工单与客服」） =====
    {
        "code": "bms_order",
        "model": BmsOrder,
        "name_cn": "订单·支付台账",
        "time_field": "create_date",
        "sn_field": "device_sn",
        "pk_field": "order_no",
        "pk_strategy": "primary_key",
        "sync_source": "bms",
        "category": "交易与售后",
        "sort_order": 10,
        "description": "BMS 支付管理订单台账(含设备SN/商户/支付/激活时间)。**不作金额统计口径** —— 金额口径看「经营与对账」分组;本表用于佐证退费单来源订单。",
    },
    {
        "code": "return_tracking",
        "model": ReturnTracking,
        "name_cn": "售后·退货跟踪",
        "time_field": "created_at_raw",
        "sn_field": "device_sn",
        "pk_field": None,
        "pk_strategy": "none",
        "sync_source": "bms",
        "category": "交易与售后",
        "sort_order": 20,
        "description": "BMS 售后退货/换货全流程跟踪(设备SN维度,含退货类型/签收/快递/原因)。与 A 路退费单是**两条不同链路**(退货 ≠ 退费),不可互相替代。",
    },
    # ===== SCRM 微信聚合平台(客服接待评估报表) =====
    {
        "code": "scrm_operator",
        "model": ScrmOperator,
        "name_cn": "SCRM·客服账号列表",
        "time_field": "last_active_at",
        "pk_field": "operator_id",
        "pk_strategy": "upsert",
        "sync_source": "scrm",
        "category": "工单与客服",
        "sort_order": 30,
        "description": "SCRM 平台侧所有客服账号(含部门/状态/最后活跃时间),用于人员别名归一。",
    },
    {
        "code": "scrm_session",
        "model": ScrmSession,
        "name_cn": "SCRM·客服接待会话",
        "time_field": "start_time",
        "pk_field": "session_id",
        "pk_strategy": "upsert",
        "sync_source": "scrm",
        "category": "工单与客服",
        "sort_order": 40,
        "description": "私聊/群聊/虚拟会话三合一(含首响/均响/消息数/转接次数),核心绩效数据源。",
    },
    {
        "code": "scrm_session_transfer",
        "model": ScrmSessionTransfer,
        "name_cn": "SCRM·会话转接明细",
        "time_field": "transfer_time",
        "pk_field": None,
        "pk_strategy": "none",
        "sync_source": "scrm",
        "category": "工单与客服",
        "sort_order": 50,
        "description": "会话转接事件流(转出→转入+时间+原因),用于转接率、工作量拆分归属。",
    },
]

# ─────────────────────────────────────────────────────────────────────
# 数据源投影（别名）—— 同一张表在多个业务分组下出现时的**只读展示项**
#
# 为什么需要：投诉工作流的使用者从「投诉」这一组进入，而不是从「工单与客服」；
#   但 service_tickets 只能注册**一次**（双注册会导致同一张表出现在两个 code 下、
#   统计与去重口径分裂，违反 数据采集合并去重机制说明.md）。
# 怎么做：投影项不落 data_sources 行、不参与注册/引擎，仅由 /data-sources 接口
#   在响应里合成；前端渲染为同一明细页，并强制显示「投影」标签 + 真身位置。
# 约束（见 docs/standards/数据源命名与分组规范.md）：
#   ① projection_of 必须指向已注册的 code；② 必须写真身分组；③ 只能只读。
# ─────────────────────────────────────────────────────────────────────
DATA_SOURCE_PROJECTIONS: list[dict] = [
    {
        "code": "complaint_ticket",
        "projection_of": "ticket",
        "name_cn": "投诉·服务请求工单",
        "category": "投诉",
        "sort_order": 10,
        "true_body_category": "工单与客服",
        "true_body_name": "工单·服务请求(投诉+退款)",
        "description": "service_tickets 的**只读投影**（真身在「工单与客服」）。投诉类判据 = service_category 空白或='投诉'；本页展示工单全量明细,不预置筛选,以免与投诉分析页口径打架。",
    },
]

# 字段级 override：(source_code, field_name) → {role, agg, enum, is_filterable, is_pk}
# 未列出的字段默认 role=dimension, is_filterable=True, is_pk=col.primary_key
FIELD_OVERRIDES: dict[tuple[str, str], dict] = {
    # cash_flow_wide (现金流水统一宽表, A-20260908-004)
    ("cash_flow_wide", "id"): {"role": "dimension", "is_pk": True, "is_filterable": False},
    ("cash_flow_wide", "route"): {"role": "category", "enum": ["A", "B", "C", "D"], "name_cn": "路线"},
    ("cash_flow_wide", "biz_no"): {"role": "dimension", "is_filterable": False},
    ("cash_flow_wide", "src_id"): {"role": "dimension", "is_filterable": False},
    ("cash_flow_wide", "pay_time"): {"role": "time"},
    ("cash_flow_wide", "pay_amount"): {"role": "amount", "agg": "sum"},
    ("cash_flow_wide", "device_sn"): {"role": "sn"},
    ("cash_flow_wide", "operator"): {"role": "person"},
    ("cash_flow_wide", "e_key"): {"role": "dimension", "is_filterable": False},
    ("cash_flow_wide", "e_matched"): {"role": "category", "enum": [True, False]},
    ("cash_flow_wide", "reason"): {"role": "category"},
    ("cash_flow_wide", "merchant"): {"role": "category"},
    ("cash_flow_wide", "package"): {"role": "category"},
    ("cash_flow_wide", "has_external_complaint"): {"role": "category", "enum": [True, False]},
    ("cash_flow_wide", "auditor"): {"role": "person"},
    ("cash_flow_wide", "matched_at"): {"role": "dimension", "is_filterable": False},
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
    # A-20260908-001: ticket_finish_time 列已删除(89% 被批量覆盖不可靠, 见 A-20260902-005)
    ("refund_wide", "ticket_handle_duration"): {"role": "measure", "agg": "avg"},
    ("refund_wide", "ticket_handler"): {"role": "person"},
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
    # manual_refund (手动退款登记 D路)
    ("manual_refund", "register_time"): {"role": "time", "name_cn": "登记日期(主时间)"},
    ("manual_refund", "device_sn"): {"role": "sn"},
    ("manual_refund", "order_no"): {"role": "dimension", "is_filterable": True, "name_cn": "订单号"},
    ("manual_refund", "merchant_id"): {"role": "category", "name_cn": "商户ID"},
    ("manual_refund", "order_amount"): {"role": "amount", "agg": "sum", "name_cn": "订单金额"},
    ("manual_refund", "registrant"): {"role": "person", "name_cn": "登记人"},
    ("manual_refund", "is_refunded"): {"role": "category", "enum": ["是", "否"], "name_cn": "是否已退款"},
    ("manual_refund", "refund_amount"): {"role": "amount", "agg": "sum"},
    ("manual_refund", "refund_operator"): {"role": "person", "name_cn": "退款操作人"},
    ("manual_refund", "op_time"): {"role": "time", "name_cn": "操作时间(实际退款操作时间)"},
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
    ("external_complaint", "complaint_category"): {"role": "category"},
    ("external_complaint", "platform"): {"role": "category"},
    ("external_complaint", "company_entity"): {"role": "category"},
    ("external_complaint", "result"): {"role": "category"},
    ("external_complaint", "solution"): {"role": "category"},
    ("external_complaint", "keyword"): {"role": "category"},
    ("external_complaint", "problem"): {"role": "dimension", "is_filterable": False},
    ("external_complaint", "handle_detail"): {"role": "dimension", "is_filterable": False},
    ("external_complaint", "matched_ticket_no"): {"role": "dimension", "is_filterable": True, "name_cn": "关联工单号(按SN匹配)"},
    ("external_complaint", "matched_at"): {"role": "time", "name_cn": "关联工单时间"},
    ("external_complaint", "dedup_hash"): {"role": "dimension", "is_filterable": False, "name_cn": "去重指纹"},
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
    ("card_complaint", "matched_ticket_no"): {"role": "dimension", "is_filterable": True, "name_cn": "关联工单号(按SN匹配)"},
    ("card_complaint", "matched_at"): {"role": "time", "name_cn": "关联工单时间"},
    ("card_complaint", "dedup_hash"): {"role": "dimension", "is_filterable": False, "name_cn": "去重指纹"},
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
    # ===== BMS 五源扩容(2026-09-05) =====
    # refund_record_report 退款记录报表(E路水单)
    ("refund_record_report", "platform_order_no"): {"role": "dimension", "is_filterable": True, "name_cn": "平台订单号"},
    ("refund_record_report", "wx_order_no"): {"role": "dimension", "name_cn": "微信订单号"},
    ("refund_record_report", "merchant_id"): {"role": "category", "name_cn": "商户号(对账锚点)"},
    ("refund_record_report", "merchant_name"): {"role": "category", "name_cn": "商户名"},
    ("refund_record_report", "device_sn"): {"role": "sn"},
    ("refund_record_report", "product_name"): {"role": "category", "name_cn": "商品名称"},
    ("refund_record_report", "refund_time"): {"role": "time", "name_cn": "退款时间(精确到秒)"},
    ("refund_record_report", "apply_refund_amount"): {"role": "amount", "agg": "sum", "name_cn": "申请退款金额"},
    ("refund_record_report", "refund_amount"): {"role": "amount", "agg": "sum", "name_cn": "退款金额"},
    ("refund_record_report", "refund_status"): {"role": "category", "name_cn": "退款状态"},
    # bms_order 订单管理
    ("bms_order", "order_no"): {"role": "dimension", "is_pk": True, "is_filterable": False, "name_cn": "订单号"},
    ("bms_order", "device_sn"): {"role": "sn"},
    ("bms_order", "combo_name"): {"role": "category", "name_cn": "套餐名称"},
    ("bms_order", "combo_price"): {"role": "amount", "agg": "sum", "name_cn": "套餐价格"},
    ("bms_order", "payable_price"): {"role": "amount", "agg": "sum", "name_cn": "应付价格"},
    ("bms_order", "actual_pay_price"): {"role": "amount", "agg": "sum", "name_cn": "实际支付价格"},
    ("bms_order", "order_status"): {"role": "category", "name_cn": "订单状态"},
    ("bms_order", "pay_type"): {"role": "category", "name_cn": "支付类型"},
    ("bms_order", "pay_date"): {"role": "time", "name_cn": "支付时间"},
    ("bms_order", "create_date"): {"role": "time", "name_cn": "创建时间(同步锚点)"},
    ("bms_order", "business_name"): {"role": "category", "name_cn": "商户名称"},
    ("bms_order", "business_id"): {"role": "category", "name_cn": "内部商户ID"},
    ("bms_order", "mch_id"): {"role": "category", "name_cn": "微信商户Id"},
    ("bms_order", "active_date"): {"role": "time", "name_cn": "激活时间"},
    # return_tracking 退货跟踪
    ("return_tracking", "device_sn"): {"role": "sn"},
    ("return_tracking", "shop"): {"role": "category", "name_cn": "店铺"},
    ("return_tracking", "merchant"): {"role": "category", "name_cn": "商户"},
    ("return_tracking", "paid_amount"): {"role": "amount", "agg": "sum", "name_cn": "实付金额"},
    ("return_tracking", "return_type"): {"role": "category", "name_cn": "退货类型"},
    ("return_tracking", "refund_amount"): {"role": "amount", "agg": "sum", "name_cn": "退款金额"},
    ("return_tracking", "return_sign_status"): {"role": "category", "name_cn": "退货签收状态"},
    ("return_tracking", "return_express_no"): {"role": "dimension", "name_cn": "退货快递单号"},
    ("return_tracking", "return_reason"): {"role": "category", "name_cn": "退货原因"},
    ("return_tracking", "order_time"): {"role": "time", "name_cn": "下单时间"},
    ("return_tracking", "return_time"): {"role": "time", "name_cn": "退货时间"},
    ("return_tracking", "sign_time"): {"role": "time", "name_cn": "签收时间"},
    ("return_tracking", "created_at_raw"): {"role": "time", "name_cn": "创建时间(同步锚点)"},
    # device_flow_usage 设备月流量
    ("device_flow_usage", "device_sn"): {"role": "sn"},
    ("device_flow_usage", "merchant_id"): {"role": "category", "name_cn": "商户ID"},
    ("device_flow_usage", "merchant_name"): {"role": "category", "name_cn": "商户名称"},
    ("device_flow_usage", "stat_month"): {"role": "category", "name_cn": "月份"},
    ("device_flow_usage", "flow_gb"): {"role": "amount", "agg": "sum", "name_cn": "月流量GB"},
    ("device_flow_usage", "month_complete"): {"role": "category", "name_cn": "是否完整月"},
    ("device_flow_usage", "fetched_at"): {"role": "time", "name_cn": "抓取时间"},
}

# ── #333: 采纳治理四列展示配置（经营五路 A~E 共用, 程序化展开） ────────────────
# 目的: 数据查看树五路原始表行级展示「这行为什么进/没进统计」——
#   adopt_state 三态徽标 + 规则码中文 tooltip + 待定行可筛选(工单 #333)。
# 口径单一来源: ADOPT_REASON_ENUM / ADOPT_STATE_ENUM 定义在采纳作业模块
#   (app.services.governance.refund_adoption), 规则码变更必须同处升版。
# 只读约束: 本页仅展示, 人工覆盖一律走后端 admin API adoption_overrides(铁律 15)。
_ADOPT_SOURCE_CODES = ("refund", "wallet", "shanmao_refund", "manual_refund", "refund_record_report")
for _ac in _ADOPT_SOURCE_CODES:
    FIELD_OVERRIDES[(_ac, "adopt_state")] = {
        "role": "category",
        "enum": list(ADOPT_STATE_ENUM),
        "is_filterable": True,
        "name_cn": "采纳状态(是否进统计)",
    }
    FIELD_OVERRIDES[(_ac, "adopt_reason")] = {
        "role": "dimension",
        # enum 下发 "规则码=中文释义" 列表, 供前端 tooltip; 不参与筛选
        "enum": list(ADOPT_REASON_ENUM),
        "is_filterable": False,
        "name_cn": "采纳规则码",
    }
    FIELD_OVERRIDES[(_ac, "adopt_rule_ver")] = {
        "role": "dimension",
        "is_filterable": False,
        "name_cn": "采纳规则版本",
    }
    FIELD_OVERRIDES[(_ac, "adopted_at")] = {
        "role": "time",
        "is_filterable": False,
        "name_cn": "采纳判定时间",
    }

# 反射时排除的系统字段（TimestampMixin 的系统时间 + raw_extra 冗余）
_EXCLUDED_FIELDS = {"id", "created_at", "updated_at", "raw_extra"}

# 组内默认排序基数：投影项(投诉组 0~9) 排在真身之后仍同组可见,
# 未声明 sort_order 的种子用该值(保证老种子不写 sort_order 也不会排到最后之外)
_DEFAULT_SORT_ORDER = 999


def seed_sort_order(seed: dict) -> int:
    """取种子的组内排序号(未声明 → 999,排在同组末尾)"""
    return int(seed.get("sort_order", _DEFAULT_SORT_ORDER))


def category_order_index(category: str | None) -> int:
    """取分组在 CATEGORY_ORDER 中的下标(未登记的分组排到最后)"""
    if category in CATEGORY_ORDER:
        return CATEGORY_ORDER.index(category)
    return len(CATEGORY_ORDER)


def build_source_projections() -> list[dict]:
    """把 DATA_SOURCE_PROJECTIONS 展开为接口可直接返回的投影项(不落库)。

    真身的 table_name / time_field / status 等元数据从 DATA_SOURCE_SEEDS 复制,
    保证投影与真身永远不漂移(改种子即同步)。
    """
    seed_by_code = {s["code"]: s for s in DATA_SOURCE_SEEDS}
    out: list[dict] = []
    for proj in DATA_SOURCE_PROJECTIONS:
        true_body = seed_by_code.get(proj["projection_of"])
        if not true_body:
            logger.warning(
                f"投影 {proj['code']} 指向未注册的 code {proj['projection_of']},已跳过"
            )
            continue
        out.append({
            **proj,
            # 复用真身元数据(表名/时间字段/SN字段等),避免两处维护
            "table_name": true_body["model"].__tablename__,
            "time_field": true_body.get("time_field"),
            "sn_field": true_body.get("sn_field"),
            "sync_source": true_body.get("sync_source"),
            "status": "active",
            # 投影**恒为只读**(它是别名,写操作必须回到真身,否则用户不知道改的是哪张表)
            "readonly": True,
            "is_projection": True,
        })
    return out


def resolve_projection_code(code: str) -> str:
    """投影 code → 真身 code(非投影原样返回)。

    数据浏览/导出按 code 查 data_sources;投影未落库,必须先解析回真身,
    否则会报「数据源未注册」。这是投影机制**唯一**需要拦截的地方。
    """
    for proj in DATA_SOURCE_PROJECTIONS:
        if proj["code"] == code:
            return proj["projection_of"]
    return code


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

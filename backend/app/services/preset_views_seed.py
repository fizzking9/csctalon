"""预设视图种子 - 把 analytics.py 4 大看板迁移为 AnalysisView 配置

幂等注册:按 code upsert,已存在则更新配置(允许调优预设视图)
启动时由 main.py lifespan 调用
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis_view import AnalysisView

# 预设视图配置:对应 analytics.py 中的 14 个函数
PRESET_VIEWS: list[dict] = [
    # ---------- 看板1:运营分析 ----------
    {
        "code": "preset_refund_trend",
        "name": "退款趋势(按日)",
        "description": "线上真实退款金额与笔数按日趋势(排除 sys/system 系统退款=退到会员钱包非现金流出)",
        "chart_type": "line",
        "segments": [{
            "source_code": "refund",
            "label": "退款",
            "dimensions": [
                {"field": "created_at_raw", "alias": "日期", "role": "time", "time_grain": "day"}
            ],
            "measures": [
                {"field": "refund_amount", "agg": "sum", "alias": "退款金额"},
                {"field": "*", "agg": "count", "alias": "笔数"},
            ],
            "filters": [
                {"field": "refund_method", "op": "eq", "value": "线上"},
                {"field": "operator", "op": "is_not_null", "value": None},
                {"field": "operator", "op": "notin", "value": ["sys", "system", "SYS", "System"]},
            ],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_refund_status",
        "name": "退费状态分布",
        "description": "按退费状态汇总金额与笔数",
        "chart_type": "pie",
        "segments": [{
            "source_code": "refund",
            "label": "状态",
            "dimensions": [{"field": "status", "alias": "状态", "role": "category"}],
            "measures": [
                {"field": "refund_amount", "agg": "sum", "alias": "金额"},
                {"field": "*", "agg": "count", "alias": "笔数"},
            ],
            "filters": [],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": None,
    },
    {
        "code": "preset_refund_reason_top",
        "name": "退费原因TOP10",
        "description": "退款类工单按服务项(真实退款原因)汇总金额与笔数。refund.refund_reason 99%是'用户退款'无意义,真实原因在 service_tickets.service_item",
        "chart_type": "bar",
        "segments": [{
            "source_code": "ticket",
            "label": "退费原因",
            "dimensions": [{"field": "service_item", "alias": "退费原因", "role": "category"}],
            "measures": [
                {"field": "refund_amount", "agg": "sum", "alias": "金额"},
                {"field": "*", "agg": "count", "alias": "笔数"},
            ],
            "filters": [
                {"field": "service_category", "op": "eq", "value": "退款"},
                {"field": "service_item", "op": "is_not_null", "value": None},
            ],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_refund_method",
        "name": "退款方式占比",
        "description": "按退款方式汇总金额与笔数",
        "chart_type": "pie",
        "segments": [{
            "source_code": "refund",
            "label": "方式",
            "dimensions": [{"field": "refund_method", "alias": "方式", "role": "category"}],
            "measures": [
                {"field": "refund_amount", "agg": "sum", "alias": "金额"},
                {"field": "*", "agg": "count", "alias": "笔数"},
            ],
            "filters": [],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": None,
    },
    # ---------- 看板2:现金流分析 ----------
    {
        "code": "preset_cash_trend",
        "name": "已登记现金流趋势(估算)",
        "description": "直接退款(线上+人工) + 用户提现,按日相加=已登记估算现金流(商户号实际现金流的组成部分,不含闪猫/其他退款)",
        "chart_type": "line",
        "segments": [
            {
                "source_code": "refund",
                "label": "直接退款",
                "dimensions": [
                    {"field": "created_at_raw", "alias": "日期", "role": "time", "time_grain": "day"}
                ],
                "measures": [{"field": "refund_amount", "agg": "sum", "alias": "金额"}],
                "filters": [
                    {"field": "refund_method", "op": "eq", "value": "线上"},
                    {"field": "operator", "op": "is_not_null", "value": None},
                    {"field": "operator", "op": "notin", "value": ["sys", "system", "SYS", "System"]},
                ],
            },
            {
                "source_code": "wallet",
                "label": "用户提现",
                "dimensions": [
                    {"field": "op_time", "alias": "日期", "role": "time", "time_grain": "day"}
                ],
                "measures": [{"field": "op_amount", "agg": "sum", "alias": "金额"}],
                "filters": [{"field": "op_type", "op": "eq", "value": "用户提现"}],
            },
        ],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_wallet_vs_withdraw",
        "name": "退回钱包 vs 实际提现",
        "description": "订单退费转入 vs 用户提现对比(暴露提现缺口)",
        "chart_type": "bar",
        "segments": [
            {
                "source_code": "wallet",
                "label": "退回钱包",
                "dimensions": [
                    {"field": "op_time", "alias": "日期", "role": "time", "time_grain": "day"}
                ],
                "measures": [{"field": "op_amount", "agg": "sum", "alias": "退回钱包"}],
                "filters": [{"field": "op_type", "op": "eq", "value": "订单退费转入"}],
            },
            {
                "source_code": "wallet",
                "label": "实际提现",
                "dimensions": [
                    {"field": "op_time", "alias": "日期", "role": "time", "time_grain": "day"}
                ],
                "measures": [{"field": "op_amount", "agg": "sum", "alias": "实际提现"}],
                "filters": [{"field": "op_type", "op": "eq", "value": "用户提现"}],
            },
        ],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_merchant_cash",
        "name": "商户号现金流对账(按商户号)",
        "description": "按商户号汇总实际退款金额、笔数、手续费(仅 refund_status='SUCCESS' 的微信侧真实扣款,对账基准,不分ORIGINAL/BALANCE桶)",
        "chart_type": "table",
        "segments": [{
            "source_code": "merchant_refund",
            "label": "商户号现金流",
            "dimensions": [{"field": "merchant_id", "alias": "商户号", "role": "category"}],
            "measures": [
                {"field": "refund_amount", "agg": "sum", "alias": "退款金额"},
                {"field": "*", "agg": "count", "alias": "笔数"},
                {"field": "fee", "agg": "sum", "alias": "手续费"},
            ],
            "filters": [{"field": "refund_status", "op": "eq", "value": "SUCCESS"}],
            "limit": 50,
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": None,
    },
    # ---------- 看板3:退款分类分析 ----------
    {
        "code": "preset_category_service_item",
        "name": "按服务项分类",
        "description": "按服务项(退款原因分类)汇总",
        "chart_type": "pie",
        "segments": [{
            "source_code": "ticket",
            "label": "服务项",
            "dimensions": [{"field": "service_item", "alias": "服务项", "role": "category"}],
            "measures": [
                {"field": "refund_amount", "agg": "sum", "alias": "金额"},
                {"field": "*", "agg": "count", "alias": "笔数"},
            ],
            "filters": [{"field": "service_item", "op": "is_not_null", "value": None}],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": None,
    },
    {
        "code": "preset_category_merchant",
        "name": "按商户分类",
        "description": "按商户汇总退款TOP20",
        "chart_type": "pie",
        "segments": [{
            "source_code": "refund",
            "label": "商户",
            "dimensions": [{"field": "merchant_name", "alias": "商户", "role": "category"}],
            "measures": [
                {"field": "refund_amount", "agg": "sum", "alias": "金额"},
                {"field": "*", "agg": "count", "alias": "笔数"},
            ],
            "filters": [{"field": "merchant_name", "op": "is_not_null", "value": None}],
            "limit": 20,
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": None,
    },
    {
        "code": "preset_category_package",
        "name": "按套餐分类",
        "description": "按套餐汇总退款TOP20",
        "chart_type": "pie",
        "segments": [{
            "source_code": "refund",
            "label": "套餐",
            "dimensions": [{"field": "package_name", "alias": "套餐", "role": "category"}],
            "measures": [
                {"field": "refund_amount", "agg": "sum", "alias": "金额"},
                {"field": "*", "agg": "count", "alias": "笔数"},
            ],
            "filters": [{"field": "package_name", "op": "is_not_null", "value": None}],
            "limit": 20,
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": None,
    },
    # ---------- 看板4:人员操作分析 ----------
    {
        "code": "preset_operator_ticket",
        "name": "人员工单处理统计",
        "description": "按处理人汇总工单数、退款金额、平均时长(排除 SYS 系统自动赠送工单与 客户 自处理,仅统计真人客服工作量)",
        "chart_type": "bar",
        "segments": [{
            "source_code": "ticket",
            "label": "处理人",
            "dimensions": [{"field": "handler", "alias": "处理人", "role": "person"}],
            "measures": [
                {"field": "*", "agg": "count", "alias": "工单数"},
                {"field": "refund_amount", "agg": "sum", "alias": "退款金额"},
                {"field": "handle_duration", "agg": "avg", "alias": "平均时长"},
            ],
            "filters": [
                {"field": "handler", "op": "is_not_null", "value": None},
                {"field": "handler", "op": "notin", "value": ["SYS", "客户"]},
            ],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_operator_audit",
        "name": "人员审核统计",
        "description": "按审核人汇总审核数与金额",
        "chart_type": "bar",
        "segments": [{
            "source_code": "refund",
            "label": "审核人",
            "dimensions": [{"field": "auditor", "alias": "审核人", "role": "person"}],
            "measures": [
                {"field": "*", "agg": "count", "alias": "审核数"},
                {"field": "refund_amount", "agg": "sum", "alias": "审核金额"},
            ],
            "filters": [{"field": "auditor", "op": "is_not_null", "value": None}],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_operator_refund",
        "name": "人员退费操作统计",
        "description": "按操作人汇总退费数与金额(排除sys/system)",
        "chart_type": "bar",
        "segments": [{
            "source_code": "refund",
            "label": "操作人",
            "dimensions": [{"field": "operator", "alias": "操作人", "role": "person"}],
            "measures": [
                {"field": "*", "agg": "count", "alias": "操作数"},
                {"field": "refund_amount", "agg": "sum", "alias": "退款金额"},
            ],
            "filters": [
                {"field": "operator", "op": "is_not_null", "value": None},
                {"field": "operator", "op": "notin", "value": ["sys", "system", "SYS", "System"]},
            ],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    # ---------- 看板5:客服接待分析(智能助手处理记录) ----------
    {
        "code": "preset_cs_reception_trend",
        "name": "客服接待量趋势(按日)",
        "description": "客服接待笔数按日趋势(智能助手处理记录)",
        "chart_type": "line",
        "segments": [{
            "source_code": "cs_record",
            "label": "接待",
            "dimensions": [
                {"field": "operation_time", "alias": "日期", "role": "time", "time_grain": "day"}
            ],
            "measures": [
                {"field": "*", "agg": "count", "alias": "接待量"},
            ],
            "filters": [],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_cs_reception_by_operator",
        "name": "客服接待量排行",
        "description": "按客服操作人汇总接待笔数(工作量排名)",
        "chart_type": "bar",
        "segments": [{
            "source_code": "cs_record",
            "label": "客服",
            "dimensions": [{"field": "operator", "alias": "操作人", "role": "person"}],
            "measures": [
                {"field": "*", "agg": "count", "alias": "接待量"},
                {"field": "device_sn", "agg": "count_distinct", "alias": "设备数"},
            ],
            "filters": [
                {"field": "operator", "op": "is_not_null", "value": None},
            ],
            "order_by": {"field": "接待量", "direction": "desc"},
            "limit": 30,
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_cs_workload_hour",
        "name": "客服工作负荷分布(按时段)",
        "description": "按操作小时(00-23)汇总接待量,评估工作负荷时段分布,为排班提供参考",
        "chart_type": "bar",
        "segments": [{
            "source_code": "cs_record",
            "label": "时段",
            "dimensions": [
                {"field": "operation_time", "alias": "时段", "role": "time", "time_grain": "hour"}
            ],
            "measures": [
                {"field": "*", "agg": "count", "alias": "接待量"},
            ],
            "filters": [],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_cs_recover_rate",
        "name": "退款挽回率(按人)",
        "description": "单 segment + 度量级 filter:总接待数 vs 已接受数,比率度量算挽回率=已接受/总接待×100%",
        "chart_type": "table",
        "segments": [{
            "source_code": "cs_record",
            "label": "客服挽回",
            "dimensions": [{"field": "operator", "alias": "操作人", "role": "person"}],
            "measures": [
                {"field": "*", "agg": "count", "alias": "总接待数"},
                # 度量级 filter:仅统计 user_accepted='已接受' 的记录(PostgreSQL FILTER 子句)
                {"field": "*", "agg": "count", "alias": "已接受数", "filter": [{"field": "user_accepted", "op": "eq", "value": "已接受"}]},
            ],
            "filters": [{"field": "operator", "op": "is_not_null", "value": None}],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "ratios": [{"alias": "挽回率%", "numerator": "已接受数", "denominator": "总接待数", "as_percent": True}],
        "time_range_default": {"days": 30},
    },
    # ---------- 看板6:投诉专项(外部投诉 315/监督局 + 卡商群) ----------
    {
        "code": "preset_external_complaint_trend",
        "name": "外部投诉趋势(按日)",
        "description": "外部投诉(315/监督局)笔数按日趋势",
        "chart_type": "line",
        "segments": [{
            "source_code": "external_complaint",
            "label": "外部投诉",
            "dimensions": [
                {"field": "complaint_date", "alias": "日期", "role": "time", "time_grain": "day"}
            ],
            "measures": [{"field": "*", "agg": "count", "alias": "投诉量"}],
            "filters": [],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_external_complaint_category",
        "name": "外部投诉分类分布",
        "description": "按投诉分类(套餐/计费、网络/技术、退款/售后、充值/规则、其他)汇总投诉量",
        "chart_type": "pie",
        "segments": [{
            "source_code": "external_complaint",
            "label": "分类",
            "dimensions": [{"field": "complaint_category", "alias": "投诉分类", "role": "category"}],
            "measures": [{"field": "*", "agg": "count", "alias": "投诉量"}],
            "filters": [{"field": "complaint_category", "op": "is_not_null", "value": None}],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": None,
    },
    {
        "code": "preset_external_complaint_by_follower",
        "name": "外部投诉跟进人统计",
        "description": "按跟进人汇总投诉量与协商退款金额(工作量与成本)",
        "chart_type": "bar",
        "segments": [{
            "source_code": "external_complaint",
            "label": "跟进人",
            "dimensions": [{"field": "follower", "alias": "跟进人", "role": "person"}],
            "measures": [
                {"field": "*", "agg": "count", "alias": "投诉量"},
                {"field": "negotiate_refund_amount", "agg": "sum", "alias": "协商退款金额"},
            ],
            "filters": [{"field": "follower", "op": "is_not_null", "value": None}],
            "limit": 30,
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_external_complaint_valid_rate",
        "name": "外部投诉结案率(按分类)",
        "description": "按投诉分类汇总总投诉量与已结案量,比率度量算结案率=结案/总数×100%(结果含'结案'视为已结案)",
        "chart_type": "table",
        "segments": [{
            "source_code": "external_complaint",
            "label": "结案率",
            "dimensions": [{"field": "complaint_category", "alias": "投诉分类", "role": "category"}],
            "measures": [
                {"field": "*", "agg": "count", "alias": "投诉量"},
                # 度量级 filter:结果包含"结案"(和解结案/撤销结案/暂行办结/调解结案/调节结案)
                {"field": "*", "agg": "count", "alias": "已结案", "filter": [{"field": "result", "op": "contains", "value": "结案"}]},
            ],
            "filters": [{"field": "complaint_category", "op": "is_not_null", "value": None}],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "ratios": [{"alias": "结案率%", "numerator": "已结案", "denominator": "投诉量", "as_percent": True}],
        "time_range_default": None,
    },
    {
        "code": "preset_card_complaint_trend",
        "name": "卡商群投诉趋势(按日)",
        "description": "卡商群投诉笔数按日趋势",
        "chart_type": "line",
        "segments": [{
            "source_code": "card_complaint",
            "label": "卡商群投诉",
            "dimensions": [
                {"field": "complaint_date", "alias": "日期", "role": "time", "time_grain": "day"}
            ],
            "measures": [{"field": "*", "agg": "count", "alias": "投诉量"}],
            "filters": [],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_card_complaint_category",
        "name": "卡商群投诉分类分布",
        "description": "按投诉问题分类汇总卡商群投诉量",
        "chart_type": "pie",
        "segments": [{
            "source_code": "card_complaint",
            "label": "分类",
            "dimensions": [{"field": "problem_category", "alias": "问题分类", "role": "category"}],
            "measures": [{"field": "*", "agg": "count", "alias": "投诉量"}],
            "filters": [{"field": "problem_category", "op": "is_not_null", "value": None}],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": None,
    },
    {
        "code": "preset_card_complaint_by_handler",
        "name": "卡商群投诉处理人统计",
        "description": "按处理人汇总卡商群投诉量",
        "chart_type": "bar",
        "segments": [{
            "source_code": "card_complaint",
            "label": "处理人",
            "dimensions": [{"field": "handler", "alias": "处理人", "role": "person"}],
            "measures": [{"field": "*", "agg": "count", "alias": "投诉量"}],
            "filters": [{"field": "handler", "op": "is_not_null", "value": None}],
            "limit": 30,
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    # ---------- 退款现金流量视图体系(重构) [全域统一核算 v3] ----------
    # 视图1:整体趋势(四路经营A/B/C/D柱状堆积 vs 实际现金流E曲线)
    # 业务关系 v3: A(退费单线上直退)/B(钱包用户取现)/C(闪猫退款独立表)/D(其他退款独立表)
    #             四路经营出款(来自经营报表导出,data_source=business类型)
    #             E(商户号现金流)是微信侧实际真扣款(第五张独立表,对账基准)
    #   经营估算 = A+B+C+D (C/D独立表未建=暂0,建表后自动并入)
    #   实际现金流 = E (merchant_refunds.refund_status='SUCCESS' 合计,不分ORIGINAL/BALANCE桶)
    #   对账偏差 = 经营估算 − 实际现金流 (正值表示有未登记的商户侧退款)
    # 因此商户号现金流E不与经营四路堆叠相加(否则重复计算),而是独立曲线 overlay
    # 呈现:四路经营A/B/C/D柱状堆积(经营估算) + 实际现金流E曲线overlay,柱顶与曲线高度差=对账偏差
    {
        "code": "preset_cash_trend_v2",
        "name": "现金流趋势(四路经营 vs 实际对账)",
        "description": "【v3统一核算】四路经营出款(A.退费单线上直退+B.钱包用户取现+C.闪猫退款+D.其他退款,柱状堆积=经营估算) vs 实际现金流E(merchant_refunds.refund_status='SUCCESS'合计,曲线overlay,对账基准)。对账偏差=柱顶(A+B+C+D) − 曲线高度(E),正值=未登记的商户侧退款,C/D独立表未建暂=0",
        "chart_type": "bar",
        "segments": [
            {
                "source_code": "refund",
                "label": "直接退款",
                "dimensions": [{"field": "created_at_raw", "alias": "日期", "role": "time", "time_grain": "day"}],
                "measures": [{"field": "refund_amount", "agg": "sum", "alias": "直接退款"}],
                "filters": [
                    {"field": "refund_method", "op": "eq", "value": "线上"},
                    {"field": "status", "op": "eq", "value": "已退款"},
                    {"field": "operator", "op": "notin", "value": ["sys", "system", "SYS", "System"]},
                ],
                "stack_group": "估算",
            },
            {
                "source_code": "wallet",
                "label": "用户提现",
                "dimensions": [{"field": "op_time", "alias": "日期", "role": "time", "time_grain": "day"}],
                "measures": [{"field": "op_amount", "agg": "sum", "alias": "用户提现"}],
                "filters": [{"field": "op_type", "op": "eq", "value": "用户提现"}],
                "stack_group": "估算",
            },
            {
                "source_code": "merchant_refund",
                "label": "商户号现金流(实际)",
                "dimensions": [{"field": "trade_time", "alias": "日期", "role": "time", "time_grain": "day"}],
                "measures": [{"field": "refund_amount", "agg": "sum", "alias": "商户号现金流(实际)"}],
                "filters": [{"field": "refund_status", "op": "eq", "value": "SUCCESS"}],
                "series_type": "line",
            },
        ],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 90},
    },
    # 视图2:退费审核人占比(饼图)
    {
        "code": "preset_refund_operator_v2",
        "name": "退费审核人占比",
        "description": "线上已退款按审核人(归一化)金额占比分布,TOP10+其他。看板时间范围(7/30天)生效",
        "chart_type": "pie",
        "segments": [{
            "source_code": "refund",
            "label": "审核人",
            "dimensions": [{"field": "auditor", "alias": "审核人", "role": "person"}],
            "measures": [
                {"field": "refund_amount", "agg": "sum", "alias": "退款金额"},
            ],
            "filters": [
                {"field": "refund_method", "op": "eq", "value": "线上"},
                {"field": "status", "op": "eq", "value": "已退款"},
                {"field": "auditor", "op": "notin", "value": ["sys", "system", "SYS", "System"]},
            ],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    # 视图4:提现 vs 转入(池子水位,柱状+曲线混合)
    {
        "code": "preset_wallet_pool_v2",
        "name": "提现池子水位",
        "description": "转入/提现柱状对比 + 净水位累计曲线。转入<提现=池子在缩小。看板时间范围生效",
        "chart_type": "bar",
        "segments": [
            {
                "source_code": "wallet",
                "label": "转入",
                "dimensions": [{"field": "op_time", "alias": "日期", "role": "time", "time_grain": "day"}],
                "measures": [{"field": "op_amount", "agg": "sum", "alias": "转入"}],
                "filters": [{"field": "op_type", "op": "eq", "value": "订单退费转入"}],
            },
            {
                "source_code": "wallet",
                "label": "提现",
                "dimensions": [{"field": "op_time", "alias": "日期", "role": "time", "time_grain": "day"}],
                "measures": [{"field": "op_amount", "agg": "sum", "alias": "提现"}],
                "filters": [{"field": "op_type", "op": "eq", "value": "用户提现"}],
            },
        ],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "cumulative": [{"alias": "净水位", "inflow": "转入", "outflow": "提现"}],
        "time_range_default": {"days": 180},
    },
    # 视图3:日记录按人员/原因(退费宽表+分类规则)
    {
        "code": "preset_refund_daily_detail_v2",
        "name": "退款日记录(人员/原因/分类)",
        "description": "线上已退款明细:操作人/退费原因/服务项/外部投诉标记,按优先级分类(外部>原因>审核人)",
        "chart_type": "table",
        "segments": [{
            "source_code": "refund_wide",
            "label": "退款明细",
            "dimensions": [
                {"field": "refund_no", "alias": "退费单号", "role": "dimension"},
                {"field": "created_at_raw", "alias": "日期", "role": "time", "time_grain": "day"},
                {"field": "operator", "alias": "操作人", "role": "person"},
                {"field": "refund_reason", "alias": "退费原因", "role": "category"},
                {"field": "service_item", "alias": "服务项", "role": "category"},
            ],
            "measures": [{"field": "refund_amount", "agg": "sum", "alias": "退款金额"}],
            "filters": [
                {"field": "refund_method", "op": "eq", "value": "线上"},
                {"field": "status", "op": "eq", "value": "已退款"},
                {"field": "operator", "op": "notin", "value": ["sys", "system", "SYS", "System"]},
            ],
            "classification_target": "refund_no",
            "limit": 100,
        }],
        "merge_strategy": "add",
        "classification_rule_names": ["客诉-外部投诉", "客诉-原因", "客诉-审核人"],
        "time_range_default": {"days": 7},
    },
    # 视图5:提现SN关联退费单/工单时间分布
    {
        "code": "preset_wallet_refund_timing_v2",
        "name": "提现-退款时序关联",
        "description": "有提现记录的退款:提现时点 vs 退费单/工单时间,用于审计提现与退款的时序关系",
        "chart_type": "table",
        "segments": [{
            "source_code": "refund_wide",
            "label": "提现时序",
            "dimensions": [
                {"field": "wallet_op_time", "alias": "提现日期", "role": "time", "time_grain": "day"},
                {"field": "refund_no", "alias": "退费单号", "role": "dimension"},
                {"field": "ticket_no", "alias": "工单号", "role": "dimension"},
                {"field": "device_sn", "alias": "设备SN", "role": "sn"},
            ],
            "measures": [
                {"field": "wallet_op_amount", "agg": "sum", "alias": "提现金额"},
                {"field": "refund_amount", "agg": "sum", "alias": "退款金额"},
            ],
            "filters": [{"field": "wallet_id", "op": "is_not_null", "value": None}],
            "limit": 100,
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    # ─────────────────────────────────────────────────────
    # 催单风险分析 8 个手动视图 (urge_analysis.py HANDLERS 注册, 实际 SQL 在 urge_analysis 模块)
    # 这里的 segments/filters 仅为占位: execute_view 会先按 view.code 分派到 HANDLERS, 不走 engine
    # ─────────────────────────────────────────────────────
    # --- 退款看板 5 张: 催单风险预测(预测性退款参考) ---
    {
        "code": "preset_refund_urge_kpi",
        "name": "退款催单风险 KPI",
        "description": "当前快照:待处理退款工单数、平均待处理时长(h)、平均催单次数、催单≥3次占比%、催单≥3次工单数",
        "chart_type": "table",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": None,
    },
    {
        "code": "preset_refund_urge_bucket",
        "name": "催单次数分桶 → 工单数 & 预估金额",
        "description": "当前待处理退款工单按催单次数分桶(0/1-2/3-4/≥5)。柱=工单数,线=预估退款金额合计,用于预判长尾高风险金额规模",
        "chart_type": "bar",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": None,
    },
    {
        "code": "preset_refund_urge_person_top",
        "name": "最后催单人 / 分配人员 TOP",
        "description": "当前催单≥3次的待处理退款工单:按最后催单人 & 分配人员 分别排名前15,用于定位需要重点督导的人员/派单分布",
        "chart_type": "table",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": None,
    },
    {
        "code": "preset_refund_urge_scatter",
        "name": "催单×时长×金额 风险清单(Top100)",
        "description": "散点图(以表格呈现):每行1工单,列=工单号/风险等级/催单次数/待处理h/预估金额/退款原因/分配/最后催单人/电话。按 催单次数→待处理时长→预估金额 倒序,一眼找出高风险长尾工单",
        "chart_type": "table",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": None,
    },
    {
        "code": "preset_refund_urge_top50",
        "name": "高风险退款工单 Top50 明细",
        "description": "复合评分=催单次数×10+待处理天数×3+LN(预估金额+1)×2。列含登记时间、最后催单时间、分配/派发人员、最后催单人、联系人员、处理节点、电话,直接用于派单催办",
        "chart_type": "table",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": None,
    },
    # --- 投诉看板 3 张: 催单→投诉关联分析 ---
    {
        "code": "preset_complaint_urge_trend",
        "name": "高催单量日 vs 投诉量 趋势",
        "description": "按日(受 start/end 过滤):柱=当日催单≥3次的退款工单数;线=外部投诉+卡商群投诉合计。若两者同向波动,说明催单积压是投诉主因",
        "chart_type": "bar",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_complaint_urge_conversion",
        "name": "催单次数分桶 → 投诉转化率",
        "description": "按 start/end 区间登记的退款工单核:每个催单分桶(0/1-2/3-4/≥5)的 退款工单数、最终产生投诉的工单数(按手机号关联)、投诉转化率%",
        "chart_type": "bar",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_complaint_urge_contribution",
        "name": "投诉里「有催单退款工单」贡献占比",
        "description": "按 start/end 区间的全部投诉:分为 3 类饼块 - ①有催单(≥1次)的退款工单用户投诉;②退款工单但无催单的投诉;③与退款工单无关的投诉。用于判断催单积压对投诉总量的贡献度",
        "chart_type": "pie",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
]


def ensure_preset_views_registered(db: Session) -> dict:
    """幂等注册预设视图:按 code upsert

    支持 classification_rule_names:seed 配置中可写规则名称列表(优先级从高到低),
    本函数查 classification_rules 表解析为 id 列表填入 classification_rule_ids。
    需在 ensure_preset_classification_rules_registered 之后调用。
    """
    from app.models.analysis_view import ClassificationRule

    # 预加载所有分类规则 name→id 映射(供 classification_rule_names 解析)
    rule_name_to_id: dict[str, int] = {
        r.name: r.id
        for r in db.scalars(select(ClassificationRule).where(ClassificationRule.is_active.is_(True)))
    }

    stats = {"presets": 0, "created": 0, "updated": 0, "skipped_rules": 0}
    for idx, cfg in enumerate(PRESET_VIEWS):
        # 解析 classification_rule_names → ids(顺序=优先级)
        rule_names = cfg.get("classification_rule_names")
        rule_ids = cfg.get("classification_rule_ids", [])
        if rule_names:
            resolved = [rule_name_to_id[n] for n in rule_names if n in rule_name_to_id]
            if len(resolved) != len(rule_names):
                # 部分规则未注册,记录跳过但继续(用已解析的)
                stats["skipped_rules"] += len(rule_names) - len(resolved)
            rule_ids = resolved

        existing = db.scalar(select(AnalysisView).where(AnalysisView.code == cfg["code"]))
        if existing:
            # 更新配置(允许调优预设)
            existing.name = cfg["name"]
            existing.description = cfg.get("description")
            existing.chart_type = cfg["chart_type"]
            existing.segments = cfg["segments"]
            existing.merge_strategy = cfg.get("merge_strategy", "add")
            existing.classification_rule_ids = rule_ids
            existing.time_range_default = cfg.get("time_range_default")
            existing.ratios = cfg.get("ratios", [])
            existing.cumulative = cfg.get("cumulative", [])
            existing.sort_order = idx
            stats["updated"] += 1
        else:
            view = AnalysisView(
                code=cfg["code"],
                name=cfg["name"],
                description=cfg.get("description"),
                chart_type=cfg["chart_type"],
                segments=cfg["segments"],
                merge_strategy=cfg.get("merge_strategy", "add"),
                classification_rule_ids=rule_ids,
                time_range_default=cfg.get("time_range_default"),
                ratios=cfg.get("ratios", []),
                cumulative=cfg.get("cumulative", []),
                is_preset=True,
                status="active",
                sort_order=idx,
            )
            db.add(view)
            stats["created"] += 1
        stats["presets"] += 1
    db.commit()
    return stats

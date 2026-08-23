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
        "name": "退费原因TOP10 金额+单数",
        "description": "退款类工单按服务项(真实退款原因)汇总 退款金额+单数 横向双柱 TOP10。双数值轴:金额(元)+单数。refund.refund_reason 99%是'用户退款'无意义,真实原因在 service_tickets.service_item",
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
        "description": "四路经营出款(线上直退+用户提现+闪猫退款+手动退款)按日合计=已登记估算现金流(商户号实际现金流的组成部分,对账基准为商户号现金流E)",
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
        "description": "按跟进人汇总投诉量(2026-08-20 协商退款金额字段已随精简下线)",
        "chart_type": "bar",
        "segments": [{
            "source_code": "external_complaint",
            "label": "跟进人",
            "dimensions": [{"field": "follower", "alias": "跟进人", "role": "person"}],
            "measures": [
                {"field": "*", "agg": "count", "alias": "投诉量"},
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
    # ---------- 退款现金流量视图体系 [全域统一核算 v4] ----------
    # 视图1:整体趋势(四路经营A/B/C/D柱状堆积 vs 实际现金流E曲线)
    # 业务关系 v4: A(退费单线上直退)/B(钱包用户取现)/C(闪猫退款 shanmao_refunds 已建表)
    #              D(手动退款 manual_refunds 已建表, is_refunded=是 计入)
    #              E(商户号现金流)是微信侧实际真扣款(第五张独立表,对账基准,商户号流水同步)
    #   经营估算 = A+B+C+D
    #   实际现金流 = E (merchant_refunds.refund_status='SUCCESS' 合计,不分ORIGINAL/BALANCE桶)
    #   对账偏差 = 经营估算 − 实际现金流 (正值表示有未登记的商户侧退款)
    # 因此商户号现金流E不与经营四路堆叠相加(否则重复计算),而是独立曲线 overlay
    # 呈现:四路经营A/B/C/D柱状堆积(经营估算) + 实际现金流E曲线overlay,柱顶与曲线高度差=对账偏差
    {
        "code": "preset_cash_trend_v2",
        "name": "现金流趋势(四路经营 vs 实际对账)",
        "description": "【v4统一核算】四路经营出款(A.退费单线上直退+B.钱包用户取现+C.闪猫退款+D.手动退款,柱状堆积=经营估算) vs 实际现金流E(merchant_refunds.refund_status='SUCCESS'合计,曲线overlay,对账基准)。对账偏差=柱顶(A+B+C+D) − 曲线高度(E),正值=商户侧已扣款但未登记四路的退款。E为商户号流水快照(截止日见数据提示),对账偏差仅供参考",
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
    # 视图2:退费审核人 金额+单数(横向双柱, 2026-08-12 改 HANDLER)
    {
        "code": "preset_refund_operator_v2",
        "name": "退费审核人 金额+单数",
        "description": "线上已退款按审核人(归一化) 退款金额+单数 横向双柱 TOP10。双数值轴:金额(元)+单数。看板时间范围生效",
        "chart_type": "bar",
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
    # ================================================================
    # 看板5-6 扩充:SCRM 微信聚合平台客服接待评估视图(10 个新视图)
    # 数据源:scrm_session(核心绩效) + scrm_session_transfer(转接)
    # ================================================================
    {
        "code": "preset_scrm_session_trend",
        "name": "SCRM-会话接待量趋势(按日+类型)",
        "description": "私聊/群聊/虚拟三类会话按日趋势对比,核心工作量总体观测",
        "chart_type": "line",
        "segments": [{
            "source_code": "scrm_session",
            "label": "会话",
            "dimensions": [
                {"field": "start_time", "alias": "日期", "role": "time", "time_grain": "day"},
                {"field": "session_type", "alias": "类型", "role": "category"},
            ],
            "measures": [
                {"field": "*", "agg": "count", "alias": "会话数"},
                {"field": "msg_count_staff", "agg": "sum", "alias": "客服消息数"},
                {"field": "msg_count_customer", "agg": "sum", "alias": "客户消息数"},
            ],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_scrm_session_by_person",
        "name": "SCRM-客服接待量排行(归一到Person)",
        "description": "按已归并到 Person 的客服维度汇总会话数+消息量(工作量排名,按会话数倒序TOP30)",
        "chart_type": "bar",
        "segments": [{
            "source_code": "scrm_session",
            "label": "客服",
            "dimensions": [{"field": "person_id", "alias": "客服(Person归一)", "role": "person"}],
            "measures": [
                {"field": "*", "agg": "count", "alias": "接待会话数"},
                {"field": "msg_count_staff", "agg": "sum", "alias": "客服消息数"},
                {"field": "duration_sec", "agg": "avg", "alias": "平均会话时长(秒)"},
            ],
            "filters": [{"field": "person_id", "op": "is_not_null", "value": None}],
            "order_by": {"field": "接待会话数", "direction": "desc"},
            "limit": 30,
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_scrm_session_type_pie",
        "name": "SCRM-会话类型占比",
        "description": "私聊/群聊/虚拟会话的数量&消息数占比饼图",
        "chart_type": "pie",
        "segments": [{
            "source_code": "scrm_session",
            "label": "类型",
            "dimensions": [{"field": "session_type", "alias": "会话类型", "role": "category"}],
            "measures": [
                {"field": "*", "agg": "count", "alias": "会话数"},
                {"field": "msg_count_staff", "agg": "sum", "alias": "客服消息数"},
            ],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_scrm_quality_ranking",
        "name": "SCRM-服务质量排行(首响+均响,按人)",
        "description": "按已归一的客服排名:首响时长(秒)越短越好、均响越短越好 — 表格+条形图,一眼看需要辅导的客服",
        "chart_type": "table",
        "segments": [{
            "source_code": "scrm_session",
            "label": "客服",
            "dimensions": [{"field": "person_id", "alias": "客服(Person归一)", "role": "person"}],
            "measures": [
                {"field": "*", "agg": "count", "alias": "样本会话数"},
                {"field": "first_response_sec", "agg": "avg", "alias": "首响时长均值(秒)"},
                {"field": "avg_response_sec", "agg": "avg", "alias": "均响时长均值(秒)"},
                {"field": "duration_sec", "agg": "avg", "alias": "平均会话时长(秒)"},
            ],
            "filters": [
                {"field": "person_id", "op": "is_not_null", "value": None},
                {"field": "quality_computed", "op": "eq", "value": True},
            ],
            "order_by": {"field": "首响时长均值(秒)", "direction": "asc"},
            "limit": 50,
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_scrm_first_response_buckets",
        "name": "SCRM-首次响应时长分布(分桶直方图)",
        "description": "分桶统计:<10秒 / 10-30秒 / 30-60秒 / 1-5分 / >5分 — 服务响应速度整体分布",
        "chart_type": "bar",
        "segments": [{
            "source_code": "scrm_session",
            "label": "响应速度",
            "dimensions": [
                {"field": "first_response_sec", "alias": "首响分桶", "role": "dimension",
                 "buckets": [
                     {"to": 10, "label": "<10s(优秀)"},
                     {"from": 10, "to": 30, "label": "10-30s(良好)"},
                     {"from": 30, "to": 60, "label": "30-60s(一般)"},
                     {"from": 60, "to": 300, "label": "1-5min(偏慢)"},
                     {"from": 300, "label": ">5min(需整改)"},
                 ]},
            ],
            "measures": [
                {"field": "*", "agg": "count", "alias": "会话数"},
            ],
            "filters": [
                {"field": "first_response_sec", "op": "is_not_null", "value": None},
                {"field": "session_type", "op": "eq", "value": "private"},
            ],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_scrm_transfer_rate",
        "name": "SCRM-转接率统计(按人/按组)",
        "description": "每个客服的累计转接次数、平均每会话转接次数(首解能力评估:越接近0越好)",
        "chart_type": "table",
        "segments": [{
            "source_code": "scrm_session",
            "label": "客服",
            "dimensions": [{"field": "person_id", "alias": "客服(Person归一)", "role": "person"}],
            "measures": [
                {"field": "*", "agg": "count", "alias": "总会话数"},
                {"field": "transfer_count", "agg": "sum", "alias": "累计转接次数"},
                {"field": "transfer_count", "agg": "avg", "alias": "平均每会话转接次数"},
            ],
            "filters": [{"field": "person_id", "op": "is_not_null", "value": None}],
            "order_by": {"field": "累计转接次数", "direction": "desc"},
            "limit": 50,
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_scrm_workload_hourly",
        "name": "SCRM-工作量时段分布(小时热力)",
        "description": "按 0-23 小时分桶的会话量分布,用于排班优化(早/中/晚班人力分配依据)",
        "chart_type": "bar",
        "segments": [{
            "source_code": "scrm_session",
            "label": "时段",
            "dimensions": [
                {"field": "start_time", "alias": "小时", "role": "time", "time_grain": "hour"},
            ],
            "measures": [
                {"field": "*", "agg": "count", "alias": "会话数"},
                {"field": "msg_count_staff", "agg": "sum", "alias": "客服消息数"},
            ],
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 14},
    },
    {
        "code": "preset_scrm_question_type_dist",
        "name": "SCRM-问题类型分布(难度系数观测)",
        "description": "按 question_type_name 汇总会话数:咨询×1.0/套餐×1.8/退款×2.2/投诉×3.0 的难度系数加权工作量可按此表估算",
        "chart_type": "bar",
        "segments": [{
            "source_code": "scrm_session",
            "label": "问题类型",
            "dimensions": [{"field": "question_type_name", "alias": "问题类型", "role": "category"}],
            "measures": [
                {"field": "*", "agg": "count", "alias": "会话数"},
                {"field": "first_response_sec", "agg": "avg", "alias": "首响均值(秒)"},
                {"field": "duration_sec", "agg": "avg", "alias": "平均时长(秒)"},
            ],
            "filters": [{"field": "question_type_name", "op": "is_not_null", "value": None}],
            "order_by": {"field": "会话数", "direction": "desc"},
            "limit": 30,
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_scrm_msg_count_vs_duration",
        "name": "SCRM-人员:消息量 vs 会话时长(散点对比)",
        "description": "每个客服:横轴=平均会话时长(秒),纵轴=平均客服消息数/会话 — 观测“说话快/慢”“啰嗦/高效”差异",
        "chart_type": "scatter",
        "segments": [{
            "source_code": "scrm_session",
            "label": "客服",
            "dimensions": [{"field": "person_id", "alias": "客服", "role": "person"}],
            "measures": [
                {"field": "duration_sec", "agg": "avg", "alias": "平均时长(秒)"},
                {"field": "msg_count_staff", "agg": "avg", "alias": "平均客服消息数"},
                {"field": "*", "agg": "count", "alias": "样本会话数"},
            ],
            "filters": [{"field": "person_id", "op": "is_not_null", "value": None}],
            "limit": 50,
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_scrm_transfer_flow",
        "name": "SCRM-转接流向Top(转出→转入客服)",
        "description": "转接明细按转出与转入客服聚合:谁最常把会话转给谁,用于培训、责任归属与技能匹配评估",
        "chart_type": "table",
        "segments": [{
            "source_code": "scrm_session_transfer",
            "label": "转接",
            "dimensions": [
                {"field": "from_operator_name", "alias": "转出客服", "role": "person"},
                {"field": "to_operator_name", "alias": "转入客服", "role": "person"},
            ],
            "measures": [
                {"field": "*", "agg": "count", "alias": "转接次数"},
            ],
            "filters": [],
            "order_by": {"field": "转接次数", "direction": "desc"},
            "limit": 50,
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },

    # ============================================================
    # 退款挽回 5 个预设视图 (2026-08-04 新口径, 对应 analytics.py HANDLERS)
    # 这些视图使用 手动 HANDLER 分派 (code 命中 analytics.HANDLERS),
    # segments 字段仅作元数据占位,实际计算由 HANDLER 完成
    # ============================================================
    {
        "code": "preset_refund_recovery_kpi",
        "name": "退款挽回 KPI 核心",
        "description": "核心口径:KPI 汇总(分母SN/成功/失败/成功率/费占比/实际退款金额). 分母=退款专项组接待+真退款类型(4类)SN去重; 失败=分母SN中T+7内进入退款工单或退款单; 成功率=(分母-失败)/分母. 2026-08-04 新口径.",
        "chart_type": "table",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_refund_recovery_daily_trend",
        "name": "退款挽回 近30天 日趋势",
        "description": "近30天 退款挽回 日粒度趋势:总接待SN/成功/失败(柱) + 成功率%(双Y轴折线). 新口径:T+7判定失败, 分母=退款专项组+真退款4类问题.",
        "chart_type": "line",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_refund_recovery_hourly_today",
        "name": "退款挽回 今日按小时趋势",
        "description": "今日 00:00 ~ 当前时间, 按小时 接待SN柱 + 成功率%折线. 供运营今日视图实时监控, 今日数据因T+7未完全收敛, 随时间自动更新.",
        "chart_type": "bar",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 1},
    },
    {
        "code": "preset_refund_recovery_operator_rank",
        "name": "退款挽回 人员明细排名",
        "description": "按退款专员/主管维度 接待SN数/成功/失败/成功率排名 + 独立的退款操作SN/操作金额统计. 注:成功率=接待侧口径(非退款操作侧), 纯退款执行人无接待SN 成功率显示为“—”.",
        "chart_type": "table",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_refund_recovery_reason_dist",
        "name": "退款挽回 失败原因分布(归一化7大类)",
        "description": "挽留失败 SN 的退款原因归一化分布(柱+占比%双Y轴). 7大类:设备硬件/充值问题/服务体验/经济因素/合规风控/用户主观/其他未分类. 真实原因从 refunds.refund_reason 与 service_tickets.service_item 交叉汇总归一.",
        "chart_type": "bar",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_refund_recovery_reason_wish",
        "name": "退款意愿 原因分布(全接待SN)",
        "description": "所有接待过退款类问题的SN(分母), 按cs_record.problem_type归一化原因分布(柱+占比%双Y轴). 反映客户退款意愿的原始原因构成.",
        "chart_type": "bar",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
    {
        "code": "preset_refund_recovery_reason_saved",
        "name": "挽回成功SN 原因分布",
        "description": "挽回成功SN(分母-失败) 按cs_record.problem_type归一化原因分布(柱+占比%双Y轴). 反映哪些原因的退款意愿被成功拦截.",
        "chart_type": "bar",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },

    # ============================================================
    # 服务请求管理专项 6 个预设视图 (本次 2026-08-06 新口径)
    # 全部使用 analytics.py HANDLERS 自定义实现 (segments 仅占位元数据)
    # ============================================================
    {
        "code": "preset_ticket_work_content_top20",
        "name": "工单工作内容 Top20 (类别×服务项)",
        "description": "近7天 服务类别×服务项 组合 Top20 排名: 工单量/同比/在途积压/平均处理时长/覆盖SN/覆盖手机号. 用于一眼看清「现在客服大部分时间在处理什么」.",
        "chart_type": "table",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": None,
    },
    {
        "code": "preset_complaint_subtype_rootcause_heatmap",
        "name": "投诉子类×六大类根因 热力矩阵",
        "description": "投诉子类 Top10 × 网络质量/套餐争议/退款协商失败/降成争议/其它/未关联SN 六大类根因 热力占比表. 根因按 SN±24h 窗口 JOIN cs_record.problem_category/problem_type 归并, 与投诉根因柱图同口径.",
        "chart_type": "table",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 7},
    },
    {
        "code": "preset_today_sn_top_coverage",
        "name": "今日 SN TOP 覆盖",
        "description": "今日接待工单量 Top20 SN 设备清单: 今日工单数/其中投诉/覆盖手机号/首次最近进线/服务项摘要. 用于排查高重复设备及对应的问题集中爆发点.",
        "chart_type": "table",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": None,
    },
    {
        "code": "preset_today_phone_dup_complaint",
        "name": "今日手机号 TOP 重复投诉",
        "description": "当日同一手机号 ≥ 2 次投诉 (三表 UNION: 工单投诉 + 外部投诉 + 卡商投诉) 的 Top20 用户清单: 脱敏手机号/今日投诉次数/今日总进线次数/投诉子类摘要/关联SN. 用于定位重点客户/纠纷用户, 优先派发主管跟进.",
        "chart_type": "table",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": None,
    },
    {
        "code": "preset_today_handler_sn_rank",
        "name": "今日处理人 覆盖 SN 排行",
        "description": "按「今日真实覆盖去重 SN 数」排名 (而不是纯结单数量, 后者会被批量覆盖污染). 更准确反映每位客服今天真正帮了多少台设备/用户解决问题. 口径与 today_kpi.ticket_done 有效完工推算一致.",
        "chart_type": "table",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": None,
    },
    {
        "code": "preset_today_cold_tickets",
        "name": "在途冷工单 Top50 (压单最久)",
        "description": "冷工单=当前仍未结 (status ∉ 已完成/无需处理/已取消) 且距登记 >24 小时. 按压单时长倒序输出 Top50: 工单号/距登记h/状态/服务项/处理人/派发人/最后活动时间/催单次数/SN/手机号. 主管每天优先督办这张表.",
        "chart_type": "table",
        "segments": [],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": None,
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

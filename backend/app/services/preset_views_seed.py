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
        "description": "退款金额与笔数按日趋势",
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
            "filters": [],
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
        "description": "按退费原因汇总金额TOP10",
        "chart_type": "bar",
        "segments": [{
            "source_code": "refund",
            "label": "原因",
            "dimensions": [{"field": "refund_reason", "alias": "原因", "role": "category"}],
            "measures": [
                {"field": "refund_amount", "agg": "sum", "alias": "金额"},
                {"field": "*", "agg": "count", "alias": "笔数"},
            ],
            "filters": [{"field": "refund_reason", "op": "is_not_null", "value": None}],
            "order_by": {"field": "金额", "direction": "desc"},
            "limit": 10,
        }],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": None,
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
        "name": "真实现金流趋势(双源相加)",
        "description": "直接退款(线上+人工) + 用户提现,按日相加",
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
        "name": "商户号对账",
        "description": "按商户号汇总退款金额、笔数、手续费",
        "chart_type": "table",
        "segments": [{
            "source_code": "merchant_refund",
            "label": "商户",
            "dimensions": [{"field": "merchant_id", "alias": "商户号", "role": "category"}],
            "measures": [
                {"field": "refund_amount", "agg": "sum", "alias": "退款金额"},
                {"field": "*", "agg": "count", "alias": "笔数"},
                {"field": "fee", "agg": "sum", "alias": "手续费"},
            ],
            "filters": [{"field": "refund_amount", "op": "gt", "value": 0}],
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
        "description": "按处理人汇总工单数、退款金额、平均时长",
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
                {"field": "operator", "op": "notin", "value": ["sys", "system"]},
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
        "description": "按客服汇总总接待量与用户已接受数,前端算挽回率=已接受/总接待",
        "chart_type": "table",
        "segments": [
            {
                "source_code": "cs_record",
                "label": "总接待",
                "dimensions": [{"field": "operator", "alias": "操作人", "role": "person"}],
                "measures": [{"field": "*", "agg": "count", "alias": "总接待数"}],
                "filters": [{"field": "operator", "op": "is_not_null", "value": None}],
            },
            {
                "source_code": "cs_record",
                "label": "已接受",
                "dimensions": [{"field": "operator", "alias": "操作人", "role": "person"}],
                "measures": [{"field": "*", "agg": "count", "alias": "已接受数"}],
                "filters": [
                    {"field": "operator", "op": "is_not_null", "value": None},
                    {"field": "user_accepted", "op": "eq", "value": "已接受"},
                ],
            },
        ],
        "merge_strategy": "add",
        "classification_rule_ids": [],
        "time_range_default": {"days": 30},
    },
]


def ensure_preset_views_registered(db: Session) -> dict:
    """幂等注册预设视图:按 code upsert"""
    stats = {"presets": 0, "created": 0, "updated": 0}
    for idx, cfg in enumerate(PRESET_VIEWS):
        existing = db.scalar(select(AnalysisView).where(AnalysisView.code == cfg["code"]))
        if existing:
            # 更新配置(允许调优预设)
            existing.name = cfg["name"]
            existing.description = cfg.get("description")
            existing.chart_type = cfg["chart_type"]
            existing.segments = cfg["segments"]
            existing.merge_strategy = cfg.get("merge_strategy", "add")
            existing.classification_rule_ids = cfg.get("classification_rule_ids", [])
            existing.time_range_default = cfg.get("time_range_default")
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
                classification_rule_ids=cfg.get("classification_rule_ids", []),
                time_range_default=cfg.get("time_range_default"),
                is_preset=True,
                status="active",
                sort_order=idx,
            )
            db.add(view)
            stats["created"] += 1
        stats["presets"] += 1
    db.commit()
    return stats

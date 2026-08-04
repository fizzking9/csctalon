"""预设看板种子 - 把 24 个预设视图组合为 4 个常规决策看板

幂等注册:按 code upsert,已存在则更新配置。
启动时由 main.py lifespan 调用(必须在 ensure_preset_views_registered 之后,
因为 items 通过 view_code 查 AnalysisView.id 填充)。

看板层不重新实现聚合,前端按 items 逐个调 /analysis-views/execute(view_id 模式)。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis_view import AnalysisView
from app.models.dashboard import Dashboard

# 4 个常规预设看板:items 引用 preset_views_seed 中的 view_code
PRESET_DASHBOARDS: list[dict] = [
    # ---------- 退款看板(17 视图: 5 催单风险预测 + 5 退款挽回分析 + 7 现金流 v2 基础) ----------
    # 前 5 个=2026-08-03 新增的"预测性退款参考因素"模块(催单风险分析);
    # 中 5 个=2026-08-04 新增"退款挽回分析"模块(成功率/日趋势/今日小时/人员排名/失败原因);
    # 后 7 个=原有的现金流/审核人/池子/原因/方式/日记录/时序
    {
        "code": "preset_dashboard_refund",
        "name": "退款看板",
        "description": "【催单风险】KPI/分桶/人员/TOP50;【退款挽回】成功率新口径(KPI/日趋势/今日小时/人员排名/失败原因);【现金流】趋势/审核/池子/原因/方式/日记录/时序",
        "category": "退款",
        "items": [
            # ── 催单风险 (预测性退款参考) ──
            {"view_code": "preset_refund_urge_kpi", "span": 24, "title": "退款催单风险 KPI(当前快照)"},
            {"view_code": "preset_refund_urge_bucket", "span": 12, "title": "催单次数分桶 工单数(柱) + 预估金额(线)"},
            {"view_code": "preset_refund_urge_person_top", "span": 12, "title": "最后催单人 / 分配人员 TOP(≥3次)"},
            {"view_code": "preset_refund_urge_scatter", "span": 24, "title": "催单×时长×金额 风险清单 Top100"},
            {"view_code": "preset_refund_urge_top50", "span": 24, "title": "高风险退款工单 Top50 明细"},
            # ── 退款挽回分析 (成功率 2026-08-04 新口径) ──
            {"view_code": "preset_refund_recovery_kpi", "span": 24, "title": "退款挽回 KPI 核心 (分母=退款专员+真退款4类 · T+7判定)"},
            {"view_code": "preset_refund_recovery_daily_trend", "span": 12, "title": "近30天 挽回成功率(线) + 接待SN(柱)"},
            {"view_code": "preset_refund_recovery_hourly_today", "span": 12, "title": "今日按小时 挽回趋势(00:00~当前)"},
            {"view_code": "preset_refund_recovery_operator_rank", "span": 24, "title": "退款专员 接待/挽回/退款操作 明细排名"},
            {"view_code": "preset_refund_recovery_reason_dist", "span": 12, "title": "挽留失败SN 原因分布(归一化7大类)"},
            # 退款挽回专用 reason_dist 12 span; 右侧原 preset_refund_status 可搭配看状态分布. 为保持 24 对齐,右侧用 status/preset_refund_reason_top
            {"view_code": "preset_refund_status", "span": 12, "title": "退费状态分布饼图"},
            # ── 原现金流模块 ──
            {"view_code": "preset_cash_trend_v2", "span": 24},
            {"view_code": "preset_refund_operator_v2", "span": 12},
            {"view_code": "preset_wallet_pool_v2", "span": 12},
            {"view_code": "preset_refund_reason_top", "span": 12},
            {"view_code": "preset_refund_method", "span": 12},
            {"view_code": "preset_refund_daily_detail_v2", "span": 24},
            {"view_code": "preset_wallet_refund_timing_v2", "span": 24},
        ],
        "time_range_default": {"days": 30},
        "visibility": "manager",
    },
    # ---------- 投诉看板(10 视图: 3 催单→投诉关联 + 7 原投诉基础) ----------
    # 前 3 个=2026-08-03 新增"催单对投诉影响"关联分析
    {
        "code": "preset_dashboard_complaint",
        "name": "投诉看板",
        "description": "【催单对投诉影响】高催单日vs投诉趋势 + 分桶转化率% + 贡献占比饼， + 外部投诉/卡商群投诉 趋势/分类/处理人/结案率",
        "category": "投诉",
        "items": [
            # ── 催单→投诉关联 ──
            {"view_code": "preset_complaint_urge_trend", "span": 24, "title": "高催单日(柱) vs 投诉量(线) 趋势"},
            {"view_code": "preset_complaint_urge_conversion", "span": 12, "title": "催单次数分桶 → 投诉转化率"},
            {"view_code": "preset_complaint_urge_contribution", "span": 12, "title": "投诉中「有催单退款工单」贡献占比"},
            # ── 原投诉基础模块 ──
            {"view_code": "preset_external_complaint_trend", "span": 12},
            {"view_code": "preset_card_complaint_trend", "span": 12},
            {"view_code": "preset_external_complaint_category", "span": 12},
            {"view_code": "preset_card_complaint_category", "span": 12},
            {"view_code": "preset_external_complaint_by_follower", "span": 12},
            {"view_code": "preset_card_complaint_by_handler", "span": 12},
            {"view_code": "preset_external_complaint_valid_rate", "span": 24},
        ],
        "time_range_default": {"days": 30},
        "visibility": "manager",
    },
    # ---------- 接待看板(原 4 + 新增 4 个 SCRM 视图) ----------
    {
        "code": "preset_dashboard_reception",
        "name": "接待看板",
        "description": "客服接待量趋势/排行 + 工作负荷时段分布 + 退款挽回率 (左区cs_record、右区SCRM微信聚合)",
        "category": "接待",
        "items": [
            {"view_code": "preset_cs_reception_trend", "span": 12},
            {"view_code": "preset_scrm_session_trend", "span": 12},
            {"view_code": "preset_cs_reception_by_operator", "span": 8},
            {"view_code": "preset_scrm_session_by_person", "span": 8},
            {"view_code": "preset_scrm_session_type_pie", "span": 8},
            {"view_code": "preset_cs_workload_hour", "span": 12},
            {"view_code": "preset_scrm_workload_hourly", "span": 12},
            {"view_code": "preset_cs_recover_rate", "span": 24},
        ],
        "time_range_default": {"days": 30},
        "visibility": "manager",
    },
    # ---------- 绩效看板(原 3 + 新增 5 个 SCRM 客服质量视图) ----------
    {
        "code": "preset_dashboard_performance",
        "name": "绩效看板",
        "description": "人员操作量统计(工单/审核/退费) + SCRM 服务质量(首响/均响/转接/难度/消息效率) — 作为绩效评估的重要维度",
        "category": "绩效",
        "items": [
            {"view_code": "preset_operator_ticket", "span": 8},
            {"view_code": "preset_operator_audit", "span": 8},
            {"view_code": "preset_operator_refund", "span": 8},
            {"view_code": "preset_scrm_quality_ranking", "span": 24},
            {"view_code": "preset_scrm_first_response_buckets", "span": 12},
            {"view_code": "preset_scrm_question_type_dist", "span": 12},
            {"view_code": "preset_scrm_transfer_rate", "span": 12},
            {"view_code": "preset_scrm_msg_count_vs_duration", "span": 12},
            {"view_code": "preset_scrm_transfer_flow", "span": 24},
        ],
        "time_range_default": {"days": 30},
        "visibility": "manager",
    },
]


def ensure_preset_dashboards_registered(db: Session) -> dict:
    """幂等注册预设看板:按 code upsert。

    items 中 view_code → view_id 查表填充,找不到的 view_code 跳过并计数。
    依赖 ensure_preset_views_registered 已完成。
    """
    stats = {"presets": 0, "created": 0, "updated": 0, "skipped_views": 0}

    # 预加载所有预设视图 code→id 映射,避免逐 item 查询
    code_to_id: dict[str, int] = {
        v.code: v.id
        for v in db.execute(
            select(AnalysisView).where(AnalysisView.code.isnot(None))
        ).scalars()
    }

    for idx, cfg in enumerate(PRESET_DASHBOARDS):
        # 填充 view_id,跳过找不到的 view_code
        items = []
        for it in cfg["items"]:
            vid = code_to_id.get(it["view_code"])
            if not vid:
                stats["skipped_views"] += 1
                continue
            items.append({
                "view_id": vid,
                "view_code": it["view_code"],
                "title": it.get("title"),
                "span": it.get("span", 12),
                "visible": it.get("visible", True),
            })

        existing = db.scalar(select(Dashboard).where(Dashboard.code == cfg["code"]))
        if existing:
            existing.name = cfg["name"]
            existing.description = cfg.get("description")
            existing.category = cfg["category"]
            existing.items = items
            existing.time_range_default = cfg.get("time_range_default")
            existing.visibility = cfg.get("visibility", "manager")
            existing.sort_order = idx
            stats["updated"] += 1
        else:
            db.add(Dashboard(
                code=cfg["code"],
                name=cfg["name"],
                description=cfg.get("description"),
                category=cfg["category"],
                items=items,
                time_range_default=cfg.get("time_range_default"),
                is_preset=True,
                status="active",
                visibility=cfg.get("visibility", "manager"),
                sort_order=idx,
            ))
            stats["created"] += 1
        stats["presets"] += 1

    db.commit()
    return stats

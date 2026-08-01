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
    # ---------- 退款看板(8 视图:5 个现金流 v2 + 3 个基础分布) ----------
    # 现金流量重构(2026-07):用 5 个 v2 视图替换旧现金流视图;
    # 服务项/商户/套餐分类暂移除(数据繁杂,待后续归一化汇聚后再加回)
    {
        "code": "preset_dashboard_refund",
        "name": "退款看板",
        "description": "退款现金流趋势(堆积) + 退费审核人占比 + 提现池子水位(柱状+曲线) + 退费原因/方式 + 退款日记录 + 提现-退款时序",
        "category": "退款",
        "items": [
            # 第一行:整体趋势全宽(堆积四类金额)
            {"view_code": "preset_cash_trend_v2", "span": 24},
            # 第二行:退费审核人占比 + 提现池子水位(柱状+曲线)
            {"view_code": "preset_refund_operator_v2", "span": 12},
            {"view_code": "preset_wallet_pool_v2", "span": 12},
            # 第三行:基础分布(原因/方式);退费状态无业务意义已移除
            {"view_code": "preset_refund_reason_top", "span": 12},
            {"view_code": "preset_refund_method", "span": 12},
            # 第四行:退款日记录(人员/原因/分类,table)
            {"view_code": "preset_refund_daily_detail_v2", "span": 24},
            # 第五行:提现-退款时序关联(table)
            {"view_code": "preset_wallet_refund_timing_v2", "span": 24},
        ],
        "time_range_default": {"days": 30},
        "visibility": "manager",
    },
    # ---------- 投诉看板(7 视图:外部投诉4+卡商群3) ----------
    {
        "code": "preset_dashboard_complaint",
        "name": "投诉看板",
        "description": "外部投诉(315/监督局) + 卡商群投诉 趋势/分类/处理人 + 结案率",
        "category": "投诉",
        "items": [
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
    # ---------- 接待看板(4 视图:客服接待) ----------
    {
        "code": "preset_dashboard_reception",
        "name": "接待看板",
        "description": "客服接待量趋势/排行 + 工作负荷时段分布 + 退款挽回率",
        "category": "接待",
        "items": [
            {"view_code": "preset_cs_reception_trend", "span": 12},
            {"view_code": "preset_cs_reception_by_operator", "span": 12},
            {"view_code": "preset_cs_workload_hour", "span": 12},
            {"view_code": "preset_cs_recover_rate", "span": 12},
        ],
        "time_range_default": {"days": 30},
        "visibility": "manager",
    },
    # ---------- 绩效看板(3 视图:人员操作量统计,非绩效得分) ----------
    {
        "code": "preset_dashboard_performance",
        "name": "绩效看板",
        "description": "仅展示人员操作量统计(工单处理/审核/退费操作),非绩效得分;绩效算法在别处",
        "category": "绩效",
        "items": [
            {"view_code": "preset_operator_ticket", "span": 8},
            {"view_code": "preset_operator_audit", "span": 8},
            {"view_code": "preset_operator_refund", "span": 8},
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

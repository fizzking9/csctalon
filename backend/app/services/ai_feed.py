"""AI 分析助手 - 数据底座 feed 组装 + Prompt 构造 (A-20260904-003 LOCKED)

- 数据源: **内部 service 直连聚合**, 与 external /brief 同一底层函数/同一 Redis 缓存
  (external._build_brief 仅纯组装 + cached_call, 无鉴权/限频副作用), 登录用户走 ManagerUser。
- 口径进上下文: Business Glossary(ai_glossary) 分层注入, 不整篇灌规划文本。
- Prompt 三层: System 铁律 / Business Context 口径卡 / Data feed + User Query。

#98 (2026-09-15) 重构:
- 日运营数据: 基于今日值班看板接口(today_kpi / today_reception_breakdown /
  today_refund_breakdown / today_queue / today_staff_efficiency)整合, 与前端看板
  共享 Redis 缓存(today:* 命名空间, cache_warmer 预热)。AI 仅做增量分析补充。
- 其他时间窗口(周/月/任意区间): 以日为颗粒度, 汇总数据 + 关键指标日走势数组
  (daily_trend), 超限自动降级(前14天逐日 + 后续按周聚合)。
"""

import datetime as _dt
import json
import logging

from sqlalchemy.orm import Session

from app.api.v1.external import _build_brief, _complaint_enhanced, _window
from app.core.config import settings
from app.core.redis_client import cache_ttl_for, cached_call, get_redis
from app.services import analytics as A
from app.services.ai_focus_service import keywords_to_scopes
from app.services.ai_glossary import glossary_all, glossary_for_question

logger = logging.getLogger(__name__)

# System 铁律(固定, 防幻觉/防越权)
# ── #98 (2026-09-15) 兜底铁律 8/9/10 ──────────────────────────────────────
#   即使运营把所有 ai_focus_configs 关停(focus 是建议性参考, 可关), 铁律仍强制约束,
#   构成"防主观推断 / 业务判定方向 / 工作时间"三条底线。与 inference-discipline /
#   business-benchmark / work-time-ai-assist 三条 seed 一一对应, 但 seed 可关铁律不可关。
_SYSTEM_PROMPT = (
    "你是本客服/经营系统的数据分析助手。你的职责是基于【给定数据】回答经营/退款/投诉/接待问题。\n"
    "铁律:\n"
    "1. 所有数字必须来自【给定数据】, 严禁编造、外推或臆测; 数据中没有的写\"暂无/未提供\"。\n"
    "2. 严格按【业务口径】理解指标, 不要自创定义。\n"
    "3. 回答中文, 结论注明数据窗口(如 今日/本周/本月 截至时间)。\n"
    "   **结构要求**: 周报/月报**必须**按 H2 锚点结构化输出, 锚点固定为 "
    "`## 接待分析` / `## 退款分析` / `## 投诉分析` / `## 趋势对比` / `## 结论建议`"
    "(无数据的段写\"暂无\", 不要省略锚点); 日报保持简洁, 不强制 H2。\n"
    "   **长度约束**: 前台实时问答 ≤500 字(突出核心结论, 不展开逐项明细; 超长会被截断); "
    "后台预生成草稿(数据窗口说明含\"预生成\"字样时)可详尽展开, 不受此限。\n"
    "4. 只回答与经营数据相关的问题; 无关问题礼貌说明无法回答。\n"
    "5. 金额一律以元为单位。\n"
    "6. **回答开头必须写明本次统计窗口**(直接引用【数据窗口说明】里的起止时间), 让用户能核对窗口"
    "是否符合预期; 若用户问的时间范围与给定窗口不一致, 先指出差异再作答, 不要默默按别的窗口回答。\n"
    "7. 给定数据里的 yoy_value/yoy_pct 是\"前一个等长周期\"的对比值, **不是另一天的独立明细**; "
    "不要把它当成完整的一天数据来陈述。若用户要某天的明细而该窗口未提供, 如实说明。\n"
    "8. **防主观推断**: 不同口径的数字(如 registered=窗口内新登记 vs handled=窗口内完成含历史单)"
    "禁止直接相减或比较大小; 必须显式标注口径差异后再下结论。"
    "禁止做\"处理量 > 登记量 → 消化存量\"这类无证据因果推断; "
    "数据未提供的归因(如\"退款上升因 XX 活动\")禁止编造, 一律写\"原因需结合运营侧确认\"。\n"
    "9. **业务判定方向**: 退款(金额/笔数/均单价)越少越好; 接待量与会话数越多越好; "
    "投诉越少越好, 严重性 外部投诉 > 商户号投诉 > 内部投诉(外部 1 件 > 商户号 10 件); "
    "退款挽回率越高越好; 积压投诉越低越好。陈述\"环比涨跌\"后必须按此基准标注\"利好/利空\", "
    "不得只报数字不评判。\n"
    "10. **工作时间**: 客服工作时间 08:00~23:59; 非工作时间(00:00~07:59)由 AI 协助处理。"
    "解读\"接待量为 0 / 完工单为 0\"时, 必须先核对窗口是否落在非工作时间, "
    "不得直接判定\"无人值班\"或\"系统故障\"。AI 接管率上升=利好(可控指标前置)。"
)

# 预设快捷触发句(前端展示用; 与 GLOSSARY_ROUTE 关键词对应)
QUICK_QUESTIONS = [
    "今日退款情况如何？四路分布和涉诉怎么样？",
    "昨日投诉分析：三源登记/处理/积压如何？",
    "本周接待量和各专项会话设备情况怎么样？",
    "今日经营总览：接待、退款、投诉一起说说。",
]


def _build_feed(db: Session, period: str, date: str | None,
                start: str | None = None, end: str | None = None) -> dict:
    """组装 feed(与 external /brief 同底层同缓存)。

    - period: daily/weekly/monthly
    - date:   period=daily 时可指定某整天(缺省=今天实时)
    - start/end: 任意区间(与 external brief v1.2 同源同护栏; 同时给出则优先于 period/date,
      支持"历史标准周/任意区间"问答, 2026-09-07)

    #98 (2026-09-15) 双层结构:
      - **日运营**: 基于 _build_brief 的 KPI/三源投诉/专项接待 + 增量补今日值班看板的
        接待分项/退款分项/处理队列/人均效率(top5)→ 让 AI 在"今日经营总览"类开放题里
        能给出看板级颗粒度的判断, 而非只有 KPI 数字。
      - **其他时间窗口(周/月/区间)**: 在 brief 基础上额外注入 `daily_trend` 数组
        (按日颗粒度的关键指标走势), 让 AI 能描述"哪天涨/哪天跌"。

    返回 brief 结构(纯只读, 命中 today:kpi/refund:do/complaint:* 既有缓存)。

    自愈(2026-09-04 事故): BMS 写入高峰期 today:kpi 缓存 TTL 被放大到 300s+,
    若同步中某次写入空 routes 会污染缓存 → AI/看板读到"四路未提供"。检测到
    "退款总额有值但四路 routes 全空"即判定坏缓存 → 删除该 key 强制重算一次。
    """
    feed = _build_brief(db, period, date, start=start, end=end)

    def _routes_empty(rf: dict) -> bool:
        routes = (rf.get("routes") or {})
        amounts = [(v or {}).get("amount") for v in (routes.get("route_a"), routes.get("route_b"),
                                                     routes.get("route_c"), routes.get("route_d"))]
        return all(a is None for a in amounts)

    rf = feed.get("refund") or {}
    total_amt = ((rf.get("total_amount") or {}).get("value")) if isinstance(rf.get("total_amount"), dict) else None
    # 退款总额有值(说明数据存在) 但四路 amount 全 None → 坏缓存(空 routes 被缓存)
    if total_amt is not None and _routes_empty(rf):
        win = _window(period, date, start=start, end=end)
        key = f"today:kpi:None:{win['start']}:{win['end']}"
        r = get_redis()
        if r is not None:
            try:
                r.delete(key)
                logger.warning("ai_feed 检测到 today:kpi 空 routes 坏缓存, 已删除 key=%s 强制重算", key)
            except Exception as e:  # noqa: BLE001
                logger.warning("ai_feed 删除坏缓存失败: %s", e)
        # 重算一次(此时缓存已删, _build_brief 内部 cached_call 会直调 today_kpi)
        feed = _build_brief(db, period, date, start=start, end=end)
    # 精简 reception.by_specialty(2026-09-04): _build_brief 的 by_specialty 含完整成员
    # 名单(数十人姓名)会占满 AI_ANALYZE_MAX_CHARS, 把 refund/complaint 关键段挤出截断
    # → 模型答"四路未提供"。AI 只需负责人+规模, 不需要逐人名单, 压缩后 feed 显著变小。
    if isinstance(feed.get("reception"), dict):
        feed["reception"] = {**feed["reception"], "by_specialty": _compact_specialty(feed["reception"].get("by_specialty"))}

    # ── #98 (2026-09-15) 双层注入: 日运营补今日看板, 其他窗口补日走势 ──
    win = _window(period, date, start=start, end=end)
    is_daily = (win["period"] == "daily")
    if is_daily:
        # 日运营: 整合今日值班看板内容, 让 AI 有看板级颗粒度
        _enrich_with_today_kanban(db, feed, win["start"], win["end"])
    else:
        # 周/月/区间: 按日颗粒度补走势数组, 让 AI 能描述哪天涨跌
        _enrich_with_daily_trend(db, feed, win["start"], win["end"])
    return feed


def _enrich_with_today_kanban(db: Session, feed: dict, start_s: str, end_s: str) -> None:
    """日运营: 在 brief 基础上增量补今日值班看板的接待分项/退款分项/处理队列/人均效率 top5。

    所有调用走 cached_call + today:* 命名空间, 与前端看板 + cache_warmer 共享缓存;
    任一接口失败不影响主 feed(brief 已含核心 KPI), 仅记 WARNING。

    只取**brief 未覆盖**的看板数据, 避免重复:
      - reception_breakdown.categories / top_staff(分项饼图 + Top10 客服)
      - refund_breakdown   (退款原因分布)
      - queue              (今日待办 + 进行中专项 + 异常事件计数, 仅取 count + top3)
      - staff_efficiency   (人均效率 top5, 仅取 name/work_score/reception_count)
    """
    try:
        rb = cached_call(
            f"today:reception:None:{start_s}:{end_s}",
            cache_ttl_for(start_s, end_s, 60),
            A.today_reception_breakdown, db, None, start_s, end_s,
        )
        if isinstance(feed.get("reception"), dict):
            feed["reception"]["breakdown"] = _compact_reception_breakdown(rb)
    except Exception as e:  # noqa: BLE001
        logger.warning("ai_feed enrich today:reception 失败(忽略): %s", e)

    try:
        rfb = cached_call(
            f"today:refund:None:{start_s}:{end_s}",
            cache_ttl_for(start_s, end_s, 60),
            A.today_refund_breakdown, db, None, start_s, end_s,
        )
        if isinstance(feed.get("refund"), dict):
            feed["refund"]["breakdown"] = _compact_refund_breakdown(rfb)
    except Exception as e:  # noqa: BLE001
        logger.warning("ai_feed enrich today:refund 失败(忽略): %s", e)

    try:
        q = cached_call(
            f"today:queue:None:{start_s}:{end_s}",
            cache_ttl_for(start_s, end_s, 60),
            A.today_queue, db, None, start_s, end_s,
        )
        feed["queue"] = _compact_queue(q)
    except Exception as e:  # noqa: BLE001
        logger.warning("ai_feed enrich today:queue 失败(忽略): %s", e)

    try:
        # topn=200 与 cache_warmer._warm_analytics key 逐字符对齐 → 命中预热缓存
        eff = cached_call(
            f"today:staff:None:{start_s}:{end_s}:200:0:person:None:None:None",
            cache_ttl_for(start_s, end_s, 60),
            A.today_staff_efficiency,
            db, None, start_s, end_s, 200, False, "person",
            department=None, group_id=None, specialty_id=None,
        )
        feed["staff_top5"] = _compact_staff_efficiency(eff)
    except Exception as e:  # noqa: BLE001
        logger.warning("ai_feed enrich today:staff 失败(忽略): %s", e)

    # #106 (2026-09-15): 补时间趋势(接待/退款/投诉按时间粒度), 让 AI 能描述"哪个时段高峰/低谷"
    try:
        ht = cached_call(
            f"today:hourly:None:{start_s}:{end_s}",
            cache_ttl_for(start_s, end_s, 60),
            A.today_hourly_trend, db, None, start_s, end_s,
        )
        feed["hourly_trend"] = _compact_hourly_trend(ht)
    except Exception as e:  # noqa: BLE001
        logger.warning("ai_feed enrich today:hourly 失败(忽略): %s", e)

    # #107 (2026-09-15): 补退款挽回(设备成功/失败 + 按人员 + 按类型), 复用今日看板 today:rr 缓存
    try:
        rr = cached_call(
            f"today:rr:None:{start_s}:{end_s}",
            cache_ttl_for(start_s, end_s, 60),
            A.refund_recovery_analysis, db, None, start_s, end_s,
        )
        feed["refund_recovery"] = _compact_refund_recovery(rr)
    except Exception as e:  # noqa: BLE001
        logger.warning("ai_feed enrich today:refund-recovery 失败(忽略): %s", e)

    # #139 (2026-09-17) C: 日报补投诉三源分布投影
    #   _build_brief 顶层 complaint.by_source 已含三源(内部/商户号/外部)×
    #   (registered/handled/backlog), 但 _select_feed_sections 按 scope 切 feed 段,
    #   问句只命中 refund/reception scope 时 complaint 段会被切走 → AI 答不出三源。
    #   此处投影 by_source 到顶层 complaint_breakdown, 并加入 _CONSTANT_FEED_KEYS
    #   (见下方), 让所有 scope 切片都保留三源拆分(轻量投影, 不重复 total)。
    try:
        if isinstance(feed.get("complaint"), dict):
            by_src = feed["complaint"].get("by_source") or []
            feed["complaint_breakdown"] = [
                {"source": s.get("source"),
                 "registered": s.get("registered"),
                 "handled": s.get("handled"),
                 "backlog": s.get("backlog")}
                for s in by_src if isinstance(s, dict)
            ]
    except Exception as e:  # noqa: BLE001
        logger.warning("ai_feed 投影 complaint_breakdown 失败(忽略): %s", e)


def _compact_reception_breakdown(rb: dict) -> dict:
    """压缩 today_reception_breakdown: 仅留 categories + top_staff 前5(去逐条明细)。"""
    if not isinstance(rb, dict):
        return {}
    cats = rb.get("categories") or []
    # 只保留非零分类, 避免长尾占用 token
    cats_compact = [{"name": c.get("name"), "value": c.get("value")}
                    for c in cats if isinstance(c, dict) and (c.get("value") or 0) > 0]
    top = rb.get("top_staff") or []
    # #114 (2026-09-15): 5→3 降 feed token; 与其它 _compact_* 对齐
    top_compact = [{"name": t.get("name"), "sessions": t.get("sessions")}
                   for t in top[:3] if isinstance(t, dict)]
    return {"categories": cats_compact, "top_staff": top_compact}


def _compact_refund_breakdown(rfb: dict) -> dict:
    """压缩 today_refund_breakdown: 仅留 top5 退款原因(去逐笔明细)。"""
    if not isinstance(rfb, dict):
        return {}
    # today_refund_breakdown 返回结构含 routes 或 reasons 字段; 取最大前5
    reasons = rfb.get("reasons") or rfb.get("top_reasons") or []
    if isinstance(reasons, list):
        reasons_compact = [{"name": r.get("name") or r.get("reason"),
                            "count": r.get("count") or r.get("value"),
                            "amount": r.get("amount")}
                           for r in reasons[:3] if isinstance(r, dict)]
    else:
        reasons_compact = []
    return {"top_reasons": reasons_compact}


def _compact_queue(q: dict) -> dict:
    """压缩 today_queue: 仅留 todos/tasks/incidents 的计数 + 前3条(去逐项明细)。"""
    if not isinstance(q, dict):
        return {}
    todos = q.get("todos") or []
    tasks = q.get("tasks") or []
    incidents = q.get("incidents") or []
    return {
        "todo_count": len(todos),
        "active_task_count": len(tasks),
        "incident_count": len(incidents),
        "top_todos": [{"title": t.get("title"), "priority": t.get("priority"),
                       "deadline": t.get("deadline")} for t in todos[:3] if isinstance(t, dict)],
        "top_incidents": [{"title": i.get("title"), "severity": i.get("severity"),
                           "is_closed": i.get("is_closed")} for i in incidents[:3] if isinstance(i, dict)],
    }


def _compact_hourly_trend(ht: dict) -> dict:
    """压缩 today_hourly_trend: 仅留关键指标时间序列(接待/退款/投诉登记+处理), 去四路拆分。

    原始返回含 24 槽 × 10+ 指标数组, 直接传会超 token。压缩为:
      - labels: 时间标签(如 09:00)
      - reception / refund_count / complaint / complaint_done 四个数组
    AI 可据此描述"哪个时段接待高峰/投诉集中"。
    """
    if not isinstance(ht, dict):
        return {}
    labels = ht.get("slot_labels") or ht.get("hour_labels") or []
    return {
        "grain": ht.get("grain"),
        "labels": labels,
        "reception": ht.get("reception") or [],
        "refund_count": ht.get("refund_count_total") or ht.get("refund_count") or [],
        "complaint": ht.get("complaint") or [],
        "complaint_done": ht.get("complaint_done") or [],
    }


def _compact_refund_recovery(rr: dict) -> dict:
    """压缩 refund_recovery_analysis: 留总体KPI + 人员top10 + 原因top5(去逐笔明细/周趋势)。

    保留:
      - kpi: total_sn/success_sn/failed_sn/success_rate + 成功/失败/放弃笔数
      - staff_detail: top10(按total_sn) 的 name/total_sn/success_sn/failed_sn/success_rate
      - reason_dist: top5 的 reason/order_cnt/success_rate
    AI 可据此分析"退款挽回成功率、谁挽回得好、哪类退款难挽回"。
    """
    if not isinstance(rr, dict):
        return {}
    kpi = rr.get("kpi") or {}
    kpi_compact = {
        "total_sn": kpi.get("total_sn"),
        "success_sn": kpi.get("success_sn"),
        "failed_sn": kpi.get("failed_sn"),
        "success_rate": kpi.get("success_rate"),
        "success_cnt": kpi.get("success_cnt"),
        "fail_cnt": kpi.get("fail_cnt"),
        "abandon_cnt": kpi.get("abandon_cnt"),
    }
    staff = rr.get("staff_detail") or []
    if isinstance(staff, list):
        # 过滤 total_sn=0 的人员(无挽回数据), 按 total_sn 倒序取 top3
        # #114 (2026-09-15): 5→3 降 feed token
        staff_sorted = sorted(
            [s for s in staff if isinstance(s, dict) and (s.get("total_sn") or 0) > 0],
            key=lambda x: x.get("total_sn") or 0, reverse=True,
        )[:3]
        staff_compact = [
            {"name": s.get("name") or s.get("operator"),
             "total_sn": s.get("total_sn"),
             "success_sn": s.get("success_sn"),
             "failed_sn": s.get("failed_sn"),
             "success_rate": s.get("success_rate")}
            for s in staff_sorted
        ]
    else:
        staff_compact = []
    reasons = rr.get("reason_dist") or []
    if isinstance(reasons, list):
        # #114 (2026-09-15): 5→3 降 feed token
        reasons_compact = [
            {"reason": r.get("reason"), "order_cnt": r.get("order_cnt"),
             "success_rate": r.get("success_rate")}
            for r in reasons[:3] if isinstance(r, dict)
        ]
    else:
        reasons_compact = []
    return {"kpi": kpi_compact, "by_staff": staff_compact, "by_reason": reasons_compact}


def _compact_staff_efficiency(eff: dict) -> list[dict]:
    """压缩 today_staff_efficiency: 仅留 top5 真人员工(name/work_score/reception_count)。"""
    if not isinstance(eff, dict):
        return []
    data = eff.get("data") or []
    out = []
    for r in data:
        if not isinstance(r, dict) or r.get("is_virtual"):
            continue
        out.append({
            "name": r.get("name"),
            "work_score": r.get("work_score"),
            "reception_count": r.get("reception_count"),
        })
        if len(out) >= 3:
            break
    return out


def _enrich_with_daily_trend(db: Session, feed: dict, start_s: str, end_s: str) -> None:
    """周/月/区间: 按日颗粒度补 daily_trend 数组, 让 AI 能描述哪天涨跌。

    走 cached_call + today:kpi:None:{d}:{d} 按日缓存(历史日 24h, 今日 60s),
    与今日看板同源同缓存。每条只取关键指标: date / reception / refund_count /
    refund_amount / complaint / complaint_handled。

    #136 (2026-09-17) 月报小时粒度补充:
      - span > 14 (月报路径) 时, 不再降级为「前14天逐日 + 后续按周聚合」,
        改为按日全展开(cap 31 天, 防超长月+1)。月报 max_tokens=5000, feed 输入给
        122b 模型, 可容纳 ~12KB(360 标量)。

    #139 (2026-09-17) A+D 扩展:
      - **A. 周报也补 hourly_summary**: 取消 #136 月报限定, 周/月报均对每条
        daily_trend 调 _enrich_daily_hourly_summary。周报 max_tokens=4000,
        7 天 × 6 标量 = ~42 标量(~400 字符), 在 feed 限 6000 字符内可接受。
      - **D. 周/月报补人均效率 top5**: 复用 today:staff:None:{start_s}:{end_s}:
        200:0:person:None:None:None 缓存(与今日看板 today:staff 同源同预热),
        调 today_staff_efficiency 整窗 → _compact_staff_efficiency 取 top3。
        AI 可答「本周/本月谁接待最多/效率最高」。
    """
    try:
        trend, _span = _days_range_trend(db, start_s, end_s)
        if trend:
            feed["daily_trend"] = trend
            # #139 A: 取消月报限定, 周/月报均补 hourly_summary
            _enrich_daily_hourly_summary(db, trend)
    except Exception as e:  # noqa: BLE001
        logger.warning("ai_feed enrich daily_trend 失败(忽略): %s", e)

    # #139 D: 周/月报补人均效率 top5(整窗一次性, 不按日重复)
    try:
        eff = cached_call(
            f"today:staff:None:{start_s}:{end_s}:200:0:person:None:None:None",
            cache_ttl_for(start_s, end_s, 60),
            A.today_staff_efficiency,
            db, None, start_s, end_s, 200, False, "person",
            department=None, group_id=None, specialty_id=None,
        )
        feed["staff_top5"] = _compact_staff_efficiency(eff)
    except Exception as e:  # noqa: BLE001
        logger.warning("ai_feed enrich weekly/monthly staff_top5 失败(忽略): %s", e)


def _days_range_trend(db: Session, start_s: str, end_s: str) -> tuple[list[dict], int]:
    """生成 [start_s, end_s] 区间内每天的 key metrics 走势数组。

    - 单日跨度 → 返回空(日运营模式由 _enrich_with_today_kanban 处理, 无需走势)
    - ≤14 天 → 按日逐条
    - >14 天 → 按日全展开(cap 31 天, 防超长月+1; #136 取消旧「前14天+周聚合」降级)

    返回 (trend_list, span_days)。

    #139 (2026-09-17) B 扩展:
      - 每条新增 complaint_handled / complaint_pending 两字段, 复用
        `complaint:enhanced:{d}:{d}` 缓存(与 _build_brief 顶层 complaint 段同源同缓存),
        取 _complaint_enhanced 返回的 total.handled / total.backlog。
      - complaint_handled = 窗口内真实完成的投诉工单(finish∈窗口 且 finish>=register+3min,
        与 complaints._is_real_finish / external._complaint_enhanced 同口径)
      - complaint_pending = 窗口内登记且归一化后未闭环的存量(backlog, 同 _complaint_enhanced)
      - 与 daily_trend.complaint(registered 总数)三字段成组, AI 可答「哪天投诉登记/处理/积压」。
    """
    try:
        s_d = _dt.date.fromisoformat(start_s)
        e_d = _dt.date.fromisoformat(end_s)
    except (ValueError, TypeError):
        return [], 0
    span = (e_d - s_d).days + 1
    if span <= 1:
        return [], span  # 单日由 today_kanban 路径处理

    today = _dt.date.today()
    days: list[_dt.date] = []
    # #136: 取消 span>14 降级, 按日全展开; 仅做 cap 31 防止异常长窗口
    cap = 31
    d = s_d
    while d <= e_d and len(days) < cap:
        days.append(d)
        d += _dt.timedelta(days=1)

    out: list[dict] = []
    seen: set[str] = set()
    for d in days:
        d_iso = d.isoformat()
        if d_iso in seen:
            continue
        seen.add(d_iso)
        # ⚠️ 历史日 future guard: 今天还没到的日期跳过(不会有数据)
        if d > today:
            continue
        try:
            # 复用 today_kpi 缓存(today:kpi:None:{d}:{d}), 与今日看板同源
            kpi = cached_call(
                f"today:kpi:None:{d_iso}:{d_iso}",
                cache_ttl_for(d_iso, d_iso, 60),
                A.today_kpi, db, None, d_iso, d_iso,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("ai_feed daily_trend 取 %s 失败(跳过): %s", d_iso, e)
            continue
        # #139 B: 取当日投诉处理/积压, 复用 complaint:enhanced:{d}:{d} 缓存
        #   (与 _build_brief 顶层 complaint 段同源同缓存, 不重复 SQL)
        #   失败仅置 None, 不影响 daily_trend 主链路
        comp_handled = None
        comp_pending = None
        try:
            ce = cached_call(
                f"complaint:enhanced:{d_iso}:{d_iso}",
                cache_ttl_for(d_iso, d_iso, 60),
                _complaint_enhanced, db, d_iso, d_iso,
            )
            ce_total = (ce.get("total") or {}) if isinstance(ce, dict) else {}
            comp_handled = ce_total.get("handled")
            comp_pending = ce_total.get("backlog")
        except Exception as e:  # noqa: BLE001
            logger.warning("ai_feed daily_trend 投诉处理/积压取 %s 失败(忽略): %s", d_iso, e)
        # 取关键指标(去 yoy, 走势只看本期值)
        reception = kpi.get("reception") or {}
        out.append({
            "date": d_iso,
            "reception": reception.get("value"),
            "device_count": reception.get("device_count"),
            "refund_count": (kpi.get("refund_count") or {}).get("value"),
            "refund_amount": (kpi.get("refund_amount") or {}).get("value"),
            "complaint": (kpi.get("complaint") or {}).get("value"),
            # #139 B: 窗口内真实完成/积压, 口径同 _complaint_enhanced.total
            "complaint_handled": comp_handled,
            "complaint_pending": comp_pending,
        })
    return out, span


def _enrich_daily_hourly_summary(db: Session, trend: list[dict]) -> None:
    """#136 月报路径: 对 daily_trend 每条额外取当日 hourly 数据压缩后嵌入。

    - 复用 today:hourly:None:{d}:{d} 缓存(与今日看板同源同预热, today_hourly_trend
      在单日 span_days=1 时自动按 hour 粒度聚合, slot_labels=["00:00","01:00",...])。
    - 任一天取数失败仅 log warning, hourly_summary 字段留空(不影响主 daily_trend)。
    """
    for item in trend:
        d_iso = item.get("date")
        if not d_iso:
            continue
        try:
            ht = cached_call(
                f"today:hourly:None:{d_iso}:{d_iso}",
                cache_ttl_for(d_iso, d_iso, 60),
                A.today_hourly_trend, db, None, d_iso, d_iso,
            )
            item["hourly_summary"] = _compact_daily_hourly_summary(ht)
        except Exception as e:  # noqa: BLE001
            logger.warning("ai_feed daily hourly_summary 取 %s 失败(忽略): %s", d_iso, e)


# 工作时段(08:00~23:59, _SYSTEM_PROMPT 铁律 10), 索引 8~23; 夜间 00:00~07:59 索引 0~7
_WORK_HOUR_IDX = list(range(8, 24))
_NIGHT_HOUR_IDX = list(range(0, 8))


def _compact_daily_hourly_summary(ht: dict | None) -> dict:
    """#136 压缩 today_hourly_trend(单日 hour 粒度) 为 6 标量/天。

    输入 today_hourly_trend 返回结构(grain=hour, 24 槽):
      - slot_labels: ["00:00","01:00",...,"23:00"]
      - reception: list[24]
      - refund_count_total: list[24]
    输出(每天 6 标量, ~12KB/月报):
      - peak_hour / peak_reception: 接待峰值小时(全天范围)
      - low_hour / low_reception: 工作时段内谷值小时
      - night_reception: 夜间(00-07)接待合计 = AI 协助处理量
      - refund_peak_hour: 退款笔数峰值小时

    说明:
      - 谷值只取工作时段内, 避免凌晨 0 接待被当「谷值」(本就是预期低谷)
      - night_reception 让 AI 能算 AI 接管率(night_reception / reception_total)
      - 不输出 low_hour 的工作时段限制说明在 _SYSTEM_PROMPT 铁律 10 已约束
    """
    # 类型断言: cached_call 失败可能返回 None, 此时返回空 dict 让上游字段缺失
    if ht is None or not isinstance(ht, dict):  # noqa: SIM108
        return {}
    labels = ht.get("slot_labels") or ht.get("hour_labels") or []
    reception = ht.get("reception") or []
    refund_cnt = ht.get("refund_count_total") or ht.get("refund_count") or []

    def _safe_val(arr, i):
        """取数组第 i 个值, None/越界返回 0(用于求和/极值, 缺数据视作 0)。"""
        if not isinstance(arr, list) or i >= len(arr):
            return 0
        v = arr[i]
        return (v if isinstance(v, (int, float)) else 0)

    def _label(i: int) -> str:
        return labels[i] if (isinstance(labels, list) and i < len(labels)) else f"{i:02d}:00"

    # 峰值小时(全天范围): argmax(reception)
    peak_idx = max(
        range(len(reception)) if isinstance(reception, list) and reception else [0],
        key=lambda i: _safe_val(reception, i),
        default=0,
    )
    # 谷值小时(工作时段内 8~23): argmin(reception[8:24]) — 避免凌晨 0 被当谷值
    work_vals = [(_safe_val(reception, i), i) for i in _WORK_HOUR_IDX]
    low_idx = min(work_vals, key=lambda x: x[0], default=(0, 8))[1]
    # 退款笔数峰值小时(全天范围)
    refund_peak_idx = max(
        range(len(refund_cnt)) if isinstance(refund_cnt, list) and refund_cnt else [0],
        key=lambda i: _safe_val(refund_cnt, i),
        default=0,
    )
    # 夜间(00-07)接待合计 = AI 协助处理量
    night_reception = sum(_safe_val(reception, i) for i in _NIGHT_HOUR_IDX)

    return {
        "peak_hour": _label(peak_idx),
        "peak_reception": _safe_val(reception, peak_idx),
        "low_hour": _label(low_idx),
        "low_reception": _safe_val(reception, low_idx),
        "night_reception": night_reception,
        "refund_peak_hour": _label(refund_peak_idx),
    }


def _compact_specialty(by_specialty) -> list[dict]:
    """压缩 by_specialty: 每专项只留 specialty/负责人 lead/成员数/会话数/设备数(去逐人名单)。"""
    if not isinstance(by_specialty, list):
        return by_specialty
    out = []
    for s in by_specialty:
        if not isinstance(s, dict):
            continue
        members = s.get("members") or []
        sessions = (s.get("sessions") or {}).get("value")
        devices = (s.get("devices") or {}).get("value")
        _lead = s.get("lead") or {}
        lead_name = _lead.get("name") if isinstance(_lead, dict) else None
        out.append({
            "specialty": s.get("specialty"),
            "lead": lead_name,
            # ── #91 (2026-09-14): 必须带**语义标签** ─────────────────────
            # 为什么不能只给名字：
            #   「负责人」在本系统是**两套独立体系** ——
            #     专项负责人（业务，此处的 lead）vs 行政组长（管理）。
            #   原实现只给 name，模型无从区分，于是把专项 lead 当成
            #   "这个组的组长"来复述 —— 用户实测反馈
            #   「明明有清晰的负责人、组长划分，为什么会出现唐彩绘是负责人、
            #     董梦玲是负责人的说法」，一部分正是模型在复述专项目录里的
            #   陈旧的 lead 字段而**不知道那是"专项"而非"行政组"**。
            #   给上 role_label 后，模型在回答时会带上限定语，不再混为一谈。
            "lead_role": "专项负责人",
            "lead_note": "专项业务负责人（≠行政组长；行政组长请勿用此字段推断）",
            "member_count": len(members),
            "sessions": sessions,
            "devices": devices,
        })
    return out


def _trim_feed(feed: dict) -> str:
    """feed → JSON 文本, 并按 AI_ANALYZE_MAX_CHARS 裁剪(防 token 超限)。"""
    raw = json.dumps(feed, ensure_ascii=False, default=str)
    limit = max(1000, int(getattr(settings, "AI_ANALYZE_MAX_CHARS", 6000) or 6000))
    if len(raw) <= limit:
        return raw
    # 截断并尽量保留 _spec(口径) — 截断到限长, 尾部标注省略
    return raw[:limit] + "\n...[数据已按长度截断]"


# 恒量段: 不管问句命中哪个 scope 都始终保留, 供模型理解窗口与口径
# (#98 层2): 这些段体积小但语义关键(period/date_start/end 喂窗口,
# _spec 喂口径单位), 截掉会让模型答错窗口或金额单位。
_CONSTANT_FEED_KEYS = ("period", "date", "date_start", "date_end",
                       "generated_at", "_spec", "range", "daily_trend",
                       "hourly_trend", "refund_recovery",
                       # #139: 投诉三源拆分投影, 让所有 scope 切片都保留(避免被切走)
                       "complaint_breakdown",
                       # #139: 周/月报路径补的 staff_top5 也作恒量段保留
                       "staff_top5")


def _select_feed_sections(feed: dict, scopes) -> dict:
    """按 scope 集合选取 feed 业务段; 恒量段始终保留; scopes 为空回退全量。

    #98 (2026-09-15) 层2: 降输入 token。scopes 由 ai_focus_service.
    keywords_to_scopes 从问句关键词推断, 值集合(reception/refund/complaint)
    与 feed 顶层段名逐字符对齐, 故可直接做段切片。
    问"今日退款"→ 只喂 refund 段(含 a_route.litigation_*, 不漏涉诉率),
    不喂 reception/complaint 段, 减少 60-70% 输入 token。

    ⚠️ 2026-09-15 修复: 原版只接受单 scope, 问句"今日经营总览：接待、退款、
    投诉一起说说。"同时命中三域但只取首个 → 另两段被切掉, AI 说"未提供"。
    现版接受 set/list, 把所有命中业务段都保留。
    """
    if scopes is None:
        return feed
    if isinstance(scopes, str):
        scopes = {scopes}
    scopes = set(scopes) - {None, ""}
    if not scopes or not isinstance(feed, dict):
        return feed
    out = {k: feed[k] for k in _CONSTANT_FEED_KEYS if k in feed}
    for s in scopes:
        if s in feed:
            out[s] = feed[s]
    return out


def build_messages(db: Session, period: str, date: str | None, question: str,
                   start: str | None = None, end: str | None = None,
                   window_label: str | None = None, window_source: str | None = None) -> list[dict]:
    """构造发给模型的 messages(三层 Prompt)。

    window_label/window_source(工单 #66): 上层(ai.py)把"窗口怎么定出来的"传下来,
    既写进 Prompt 让模型明确当前窗口, 也便于排查 —— 自然语言推断错误时, 从这里就能看出
    模型被喂的是哪个窗口, 而不是靠猜。
    """
    feed = _build_feed(db, period, date, start=start, end=end)
    # #98 层2: 按问句 scope 切 feed 段(只留命中业务段+恒量段), 降输入 token。
    # scopes 空集(问句未命中 refund/complaint/reception 关键词)→ 回退全量, 兼容。
    # ⚠️ 2026-09-15 修复: 改用 keywords_to_scopes 取所有命中 scope, 避免多域
    #   问句(如"今日经营总览：接待、退款、投诉一起说说")只取首个 scope 致
    #   其它业务段被切掉, AI 答"未提供"。
    feed = _select_feed_sections(feed, keywords_to_scopes(question))
    feed_json = _trim_feed(feed)
    # 口径卡: 按问句路由选子集; 未命中全量
    glossary = glossary_for_question(question)
    # 窗口说明: 优先用上层给的 label(含自然语言来源); 否则退回区间/period 描述
    win_label = f"period={period}"
    if start is not None or end is not None:
        win_label = f"period=range(start={start}, end={end})"
    if window_label:
        win_label = f"{win_label}; 窗口判定={window_label}"
        if window_source == "nl":
            win_label += "(由问句中的时间表达自动识别)"
    business_ctx = (
        "【业务口径(必须遵守)】\n" + (glossary or glossary_all()) +
        "\n\n【数据窗口说明】" +
        f" {win_label}; date_start={feed.get('date_start')}; date_end={feed.get('date_end')}; " +
        f" generated_at={feed.get('generated_at')}"
    )
    # 关注点/分析视角(#26, A-20260904-003 v1.2): DB 配置的建议性指导, 注入于口径与数据之间。
    # 空库/无命中 → 空串, 保持改造前输出一致(回归安全)。仅作运营侧重参考, 数据仍以 feed 为准。
    focus = ""
    try:
        from app.services.ai_focus_service import focus_for_question
        focus = focus_for_question(db, question)
    except Exception:  # noqa: BLE001  Focus 故障不影响主链路
        logger.warning("ai_feed 加载 focus 失败(忽略): 保持无 focus 输出")
    data_ctx = "【给定数据(JSON, 金额单位=元)】\n" + feed_json
    system = _SYSTEM_PROMPT
    if focus:
        body = (
            f"{business_ctx}\n\n"
            "【本期关注点 / 分析视角(运营侧重参考, 供你组织回答时侧重; 数据仍以给定数据为准)】\n"
            f"{focus}\n\n{data_ctx}\n\n请回答以下问题：{question}"
        )
    else:
        body = f"{business_ctx}\n\n{data_ctx}\n\n请回答以下问题：{question}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": body},
    ]


def available_scope_options() -> list[dict]:
    """GET /ai/config 返回可用 period 与快捷问(内部; ManagerUser)。"""
    return [
        {"period": "daily", "label": "当日"},
        {"period": "weekly", "label": "本周(周一起)"},
        {"period": "monthly", "label": "本月"},
        {"quick_questions": QUICK_QUESTIONS},
    ]

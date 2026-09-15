"""AI 分析助手 - 数据底座 feed 组装 + Prompt 构造 (A-20260904-003 LOCKED)

- 数据源: **内部 service 直连聚合**, 与 external /brief 同一底层函数/同一 Redis 缓存
  (external._build_brief 仅纯组装 + cached_call, 无鉴权/限频副作用), 登录用户走 ManagerUser。
- 口径进上下文: Business Glossary(ai_glossary) 分层注入, 不整篇灌规划文本。
- Prompt 三层: System 铁律 / Business Context 口径卡 / Data feed + User Query。
"""

import json
import logging

from sqlalchemy.orm import Session

from app.api.v1.external import _build_brief, _window
from app.core.config import settings
from app.core.redis_client import get_redis
from app.services.ai_focus_service import keyword_to_scope
from app.services.ai_glossary import glossary_all, glossary_for_question

logger = logging.getLogger(__name__)

# System 铁律(固定, 防幻觉/防越权)
_SYSTEM_PROMPT = (
    "你是本客服/经营系统的数据分析助手。你的职责是基于【给定数据】回答经营/退款/投诉/接待问题。\n"
    "铁律:\n"
    "1. 所有数字必须来自【给定数据】, 严禁编造、外推或臆测; 数据中没有的写\"暂无/未提供\"。\n"
    "2. 严格按【业务口径】理解指标, 不要自创定义。\n"
    "3. 回答中文, 结构清晰(可用标题/要点/小表格), 结论注明数据窗口(如 今日/本周/本月 截至时间)。\n"
    "4. 只回答与经营数据相关的问题; 无关问题礼貌说明无法回答。\n"
    "5. 金额一律以元为单位。\n"
    "6. **回答开头必须写明本次统计窗口**(直接引用【数据窗口说明】里的起止时间), 让用户能核对窗口"
    "是否符合预期; 若用户问的时间范围与给定窗口不一致, 先指出差异再作答, 不要默默按别的窗口回答。\n"
    "7. 给定数据里的 yoy_value/yoy_pct 是\"前一个等长周期\"的对比值, **不是另一天的独立明细**; "
    "不要把它当成完整的一天数据来陈述。若用户要某天的明细而该窗口未提供, 如实说明。"
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
    return feed


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
                       "generated_at", "_spec", "range")


def _select_feed_sections(feed: dict, scope: str | None) -> dict:
    """按 scope 选取 feed 业务段; 恒量段始终保留; scope=None 回退全量。

    #98 (2026-09-15) 层2: 降输入 token。scope 由 ai_focus_service.
    keyword_to_scope 从问句关键词推断, 值(reception/refund/complaint)
    与 feed 顶层段名逐字符对齐, 故可直接做段切片。
    问"今日退款"→ 只喂 refund 段(含 a_route.litigation_*, 不漏涉诉率),
    不喂 reception/complaint 段, 减少 60-70% 输入 token。
    """
    if scope is None or not isinstance(feed, dict):
        return feed
    out = {k: feed[k] for k in _CONSTANT_FEED_KEYS if k in feed}
    if scope in feed:
        out[scope] = feed[scope]
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
    # scope=None(问句未命中 refund/complaint/reception 关键词)→ 回退全量, 兼容。
    feed = _select_feed_sections(feed, keyword_to_scope(question))
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

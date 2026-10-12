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

# ── #307 (2026-10-12) 实时问答专用 System Prompt(gen_system_live) ──────────
# 为什么拆双版: 实时问答与后台预生成的目标相反 —— 实时追求「快、准、简」(用户在
# 等待, 只问一个焦点), 预生成追求「全、深、结构」(H2 五锚点供摘取层定位)。
# 历史问题(实测取证): 单一 gen_system v3(3892 字符) 同时服务两种调用, 导致:
#   ① 问「今日退款」却输出全套 5 个 H2 锚点(含「接待分析 暂无」空段);
#   ② 把口径纪律原文背给用户(提示词泄漏, 用户要结论不要纪律课);
#   ③ 「≤500 字」约束对 120b 完全失效(实测 700~1600 字符);
#   ④ verdict 无从引用 —— 模型按铁律 9 自判「处理量下降=利空」这类无据判定。
# 实时版配套改造: feed 层预计算 verdict(#307 _add_verdicts), 模型只叙述不判定。
_SYSTEM_PROMPT_LIVE = (
    "你是客服经营数据助手，基于【给定数据】用中文简洁回答实时问句。\n"
    "铁律:\n"
    "1. 所有数字必须来自【给定数据】，严禁编造；环比(yoy_pct)、占比(amount_pct/"
    "count_pct)等派生数字均已由系统算好，直接原样引用，禁止自行计算。\n"
    "2. 数据中带 verdict(利好/利空/持平)的指标直接引用该判定；无 verdict 的指标"
    "只报数字，禁止自行评判好坏。\n"
    "3. 某指标没有对比字段时，直接陈述本期数值即可，禁止写\"变化率未随数据下发\""
    "\"无法计算\"等系统内部话术。\n"
    "4. 只回答问句所问的内容：问退款就只说退款。禁止输出 \"## 章节标题\"、表格和"
    "未问到的段落；用简短分点叙述，全文不超过 300 字。\n"
    "5. 禁止向用户复述统计口径、系统规则、数据缺失原因；指标确实无数据时写\"暂无\"。\n"
    "6. 回答第一行写明统计窗口(引用【数据窗口说明】起止时间，格式 YYYY-MM-DD HH:mm)；"
    "金额一律元；所有对比均为环比(前一个等长周期)，禁止使用\"同比\"一词。\n"
    "7. 只回答经营数据相关的问题；无关问题礼貌说明无法回答。\n"
    "8. 若上下文给出【本次必须覆盖维度】清单，则逐维度给出数据(用 **维度**：加粗行)，"
    "此时长度可放宽至 800 字，但仍禁止未问段落的空泛展开。\n"
    "示例(问\"今日退款情况如何？\"):\n"
    "统计窗口：2026-10-12 00:00 至 20:20\n"
    "- 退款总额 26,061.70 元，环比 +2.4%（利空）；221 笔，环比持平。\n"
    "- 四路：A 线上直退 13,926.30 元（53.4%）、B 钱包提现 6,208.30 元（23.8%）、"
    "D 手动退款 5,927.10 元（22.7%）。\n"
    "- 涉诉率 37.4%，环比下降（利好）。\n"
    "- 建议：关注 A 路大额单笔。\n"
)

# 预设快捷触发句(前端展示用; 与 GLOSSARY_ROUTE 关键词对应)
# ⚠️ 快捷句必须落在「已有预生成快照」的窗口/域上, 否则展示出来却必走实时 LLM,
#    又慢又消耗算力。周报 weekly_compare_last 每周一 02:00 才生成「上一周」快照
#    (trigger_at=1-02:00, 窗口截止昨天), 周中问「本周」无重叠快照(区间重叠匹配
#    天然排除), 历史实测该问句 100% 走 live。故周维度快捷句固定问「上周」。
QUICK_QUESTIONS = [
    "今日退款情况如何？四路分布和涉诉怎么样？",
    "昨日投诉分析：三源登记/处理/积压如何？",
    "上周接待量和各专项会话、设备情况怎么样？",
    "今日经营总览：接待、退款、投诉一起说说。",
]


# ── #307 (2026-10-12) verdict 预计算: 凡工具能算的(利好/利空), 不写成对模型的要求 ──
# 方向基准与 business-benchmark 卡/预生成版铁律 9 完全一致(单一来源, 双处引用):
#   退款金额/笔数 越少越好; 接待量 越多越好; 投诉登记/积压 越少越好; 投诉处理 越多越好;
#   涉诉率(A路) 下降=利好(涉诉少=退款争议少)。
# 历史问题: verdict 全靠模型按铁律自判 → 实测输出「处理 89 下降（利空）」这类
# 无据判定(铁律 9 并未定义处理量方向)。现在代码算好, 模型只叙述。
def _verdict_from_yoy(yoy_pct, *, more_is_better: bool) -> str | None:
    """按环比百分比与业务方向给 verdict; 无环比值返回 None(模型只报数不评判)。

    |yoy| < 0.05 视为持平(与 feed 下发的一位小数精度一致: 0.0/-0.0 不算涨跌)。
    """
    if yoy_pct is None:
        return None
    try:
        v = float(yoy_pct)
    except (TypeError, ValueError):
        return None
    if abs(v) < 0.05:
        return "持平"
    up = v > 0
    return ("利好" if up else "利空") if more_is_better else ("利空" if up else "利好")


def _add_verdicts(feed: dict) -> None:
    """给 feed 关键指标附加 verdict(利好/利空/持平) —— #307, 就地修改。

    覆盖: refund.total_amount/total_count(退款越少越好)、
          refund.a_route.litigation_ratio(按 delta_pct_points, 下降=利好)、
          reception.total(接待越多越好)。
    无 yoy 字段或 yoy=None 的指标不加(实时版铁律 2: 无 verdict 只报数字)。
    仅加键不改既有键, external /brief 对外契约不受影响(verdict 在 AI 侧投影)。
    """
    rf = feed.get("refund")
    if isinstance(rf, dict):
        for key in ("total_amount", "total_count"):
            node = rf.get(key)
            if isinstance(node, dict) and "yoy_pct" in node:
                node["verdict"] = _verdict_from_yoy(node.get("yoy_pct"), more_is_better=False)
        ar = rf.get("a_route")
        if isinstance(ar, dict) and isinstance(ar.get("litigation_ratio"), dict):
            lr = ar["litigation_ratio"]
            lr["verdict"] = _verdict_from_yoy(lr.get("delta_pct_points"), more_is_better=False)
    rc = feed.get("reception")
    if isinstance(rc, dict):
        tot = rc.get("total")
        if isinstance(tot, dict) and "yoy_pct" in tot:
            tot["verdict"] = _verdict_from_yoy(tot.get("yoy_pct"), more_is_better=True)


def _enrich_complaint_breakdown(feed: dict) -> None:
    """#307: complaint 侧投影补前值/环比/verdict(就地下发 AI 专属, 不改 external 契约)。

    原 #139 投影只取 registered/handled/backlog 裸数字:
      ① complaint.by_source 项已有的 backlog_prev_pct 被丢弃;
      ② 前值(在 complaint.prev 块)不可达 —— 问退款的问句 scope 切片会把 complaint
         段切走, 模型手里只剩裸数组 → 只能输出「变化率未随数据下发」噪音(实测取证)。
    发布后实测补刀(2026-10-12): 模型对无 verdict 的 registered/handled 自判
      「处理量环比上升 10.5%(利空)」—— 判反(处理越多越好)且比率系自算(幻觉风险)。
      凡工具能算的: 代码补 registered_prev_pct/handled_prev_pct + verdict
      (登记越少越好, 处理越多越好), 模型只引用。
    """
    rc = feed.get("complaint")
    if not isinstance(rc, dict):
        return
    prev = rc.get("prev") if isinstance(rc.get("prev"), dict) else {}

    def _pct(cur, prv):
        if cur is None or prv in (None, 0):
            return None
        return round((cur - prv) / prv * 100, 1)

    def _enrich_node(node: dict, psrc: dict) -> None:
        # 前值(登记/处理/积压)
        for k in ("registered", "handled", "backlog"):
            if k in node and k in psrc:
                node[f"{k}_prev"] = psrc.get(k)
        # 派生环比(代码算, 模型禁自算)
        for k in ("registered", "handled"):
            if node.get(f"{k}_prev_pct") is None and k in psrc:
                node[f"{k}_prev_pct"] = _pct(node.get(k), psrc.get(k))
        # verdict: 登记越少越好, 处理越多越好, 积压越少越好
        if "registered_prev_pct" in node and node.get("registered_prev_pct") is not None:
            node["registered_verdict"] = _verdict_from_yoy(node["registered_prev_pct"], more_is_better=False)
        if "handled_prev_pct" in node and node.get("handled_prev_pct") is not None:
            node["handled_verdict"] = _verdict_from_yoy(node["handled_prev_pct"], more_is_better=True)
        if node.get("backlog_prev_pct") is not None:
            node["backlog_verdict"] = _verdict_from_yoy(node["backlog_prev_pct"], more_is_better=False)

    # ① total 节点(就地加键; complaint.total 已有 backlog_prev_pct, #prompt-v2.1)
    if isinstance(rc.get("total"), dict):
        ptotal = prev.get("total") if isinstance(prev.get("total"), dict) else {}
        _enrich_node(rc["total"], ptotal)
    # ② by_source 各项 + complaint_breakdown 投影(scope 切片后仍可达)
    out = []
    pmap = {x.get("source"): x for x in (prev.get("by_source") or []) if isinstance(x, dict)}
    for s in rc.get("by_source") or []:
        if not isinstance(s, dict):
            continue
        _enrich_node(s, pmap.get(s.get("source")) or {})
        out.append({k: s.get(k) for k in (
            "source", "registered", "handled", "backlog",
            "registered_prev", "handled_prev", "backlog_prev",
            "registered_prev_pct", "handled_prev_pct", "backlog_prev_pct",
            "registered_verdict", "handled_verdict", "backlog_verdict",
        ) if s.get(k) is not None or k in ("source", "registered", "handled", "backlog")})
    if out:
        feed["complaint_breakdown"] = out


def _format_win_times(feed: dict) -> None:
    """#307: 窗口时间格式化到分钟(去微秒) —— 实测模型会照抄
    「2026-10-12T20:20:06.543534+08:00」秒级时间戳, 用户看到冗长不友好。
    仅处理 date_start/date_end/generated_at 的 ISO 串: YYYY-MM-DDTHH:mm → YYYY-MM-DD HH:mm。
    """
    for k in ("date_start", "date_end", "generated_at"):
        v = feed.get(k)
        if isinstance(v, str) and "T" in v and len(v) >= 16:
            feed[k] = f"{v[:10]} {v[11:16]}"


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

    # #189 (2026-09-24): refund 段字段重排 —— 涉诉(a_route)与 E 对账基准(actual_cashflow_e)
    #   原本在内层 dict 最末(_build_brief 的字典顺序), 一旦发生段内裁剪必被切掉;
    #   生产实测日报"涉诉/E 对账基准"100% 写"暂无"即由此而来。提到段内前部,
    #   使段内截断时也优先保住。仅调整 AI 侧投影顺序, 不改 external.py 对外契约。
    if isinstance(feed.get("refund"), dict):
        _rf = feed["refund"]
        _ordered = {k: _rf[k] for k in ("total_amount", "total_count", "routes",
                                        "actual_cashflow_e", "a_route", "routes_prev", "breakdown")
                    if k in _rf}
        for _k, _v in _rf.items():          # 未登记键按原顺序补在其后
            _ordered.setdefault(_k, _v)
        feed["refund"] = _ordered

    # ── #98 (2026-09-15) 双层注入: 日运营补今日看板, 其他窗口补日走势 ──
    win = _window(period, date, start=start, end=end)
    is_daily = (win["period"] == "daily")
    if is_daily:
        # 日运营: 整合今日值班看板内容, 让 AI 有看板级颗粒度
        _enrich_with_today_kanban(db, feed, win["start"], win["end"])
    else:
        # 周/月/区间: 按日颗粒度补走势数组, 让 AI 能描述哪天涨跌
        _enrich_with_daily_trend(db, feed, win["start"], win["end"])
    # #307: verdict 预计算 + complaint_breakdown 补前值/verdict + 窗口时间格式化
    # (均在 AI 侧投影层, 不改 external /brief 对外契约)
    _add_verdicts(feed)
    _enrich_complaint_breakdown(feed)
    _format_win_times(feed)
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
    #   #307 (2026-10-12): 投影构造移至 _enrich_complaint_breakdown(_build_feed 末尾统一
    #   调用) —— 原投影只取 registered/handled/backlog 裸数字, 丢弃 by_source 项已有的
    #   backlog_prev_pct 与 complaint.prev 前值, 模型只能写「变化率未随数据下发」噪音。
    #   complaint 段本体仍保留(问投诉的问句 scope 命中时完整下发)。


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

    #139 (2026-09-17) A+D 扩展(2026-09-23 修订):
      - **A. ~~周报也补 hourly_summary~~(已移除)**: 用户明确「周月报不关注按小时的数据」,
        小时粒度对周月报是噪音且占 token(月报约 12KB); 现仅保留按天 daily_trend 走势。
        函数 _enrich_daily_hourly_summary 保留但不再调用, 以备区间问句按需启用。
      - **D. 周/月报补人均效率 top5**: 复用 today:staff:None:{start_s}:{end_s}:
        200:0:person:None:None:None 缓存(与今日看板 today:staff 同源同预热),
        调 today_staff_efficiency 整窗 → _compact_staff_efficiency 取 top3。
        AI 可答「本周/本月谁接待最多/效率最高」。
    """
    try:
        trend, _span = _days_range_trend(db, start_s, end_s)
        if trend:
            feed["daily_trend"] = trend
            # 不再补 hourly_summary(2026-09-23): 周/月报数据量大且按天走势已足够,
            # 小时粒度对周月报是噪音且占 token(月报约 12KB); 用户明确「不关注按小时的数据」。
            # 函数 _enrich_daily_hourly_summary 保留但不再在此调用。
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


# ── #189 (2026-09-24) 段裁剪优先级 ────────────────────────────────────────
# 原 _trim_feed 是"字符前缀截断"(`raw[:limit]`): ① 产出被腰斩的**非法 JSON**;
# ② 业务段被排在恒量段之后 → 必被切掉。生产实测(3500 上限): 模板 1 完整 feed 6522
#    字符, `refund` 段内 `a_route`(涉诉)/`actual_cashflow_e`(E 对账) 恒被切 → 日报
#    "涉诉/E 对账基准"100% 写"暂无"; reception/complaint 整段随机丢失。
# 现改为**按段取舍**: 整段保留或整段省略, 保证产出始终是合法 JSON; 省略的段名登记到
# `_trimmed_out_sections`, 让模型区分"超预算未下发"与"系统无此数据"。
# 数值越小越先保留; 0 = 必保(业务段 + 窗口元信息), 未登记段默认最低优先级。
_SECTION_PRIORITY: dict[str, int] = {
    # 业务段(必保): 四路/涉诉/E 在 refund 段内, 三源在 complaint 段内, AI 接管率/专项在 reception 段内
    "refund": 0, "complaint": 0, "reception": 0,
    # 窗口元信息(必保): 模型必须知道统计窗口(铁律 6 要求回答开头写明窗口)
    "period": 0, "date": 0, "date_start": 0, "date_end": 0, "generated_at": 0, "range": 0,
    # 次优先: 轻量投影/排行/待办(体积小, 语义独立)
    "complaint_breakdown": 1, "staff_top5": 1, "queue": 1,
    # 口径卡: 体积大(939 字符)且与 glossary 部分重复, 紧张时让位给业务段
    "_spec": 2,
    # 时间序列与挽回明细: 对简报类最不关键
    "hourly_trend": 3, "refund_recovery": 3,
    # 跨日走势(月报/滚动窗口约 4~5k 字符): 体积最大, 最可让位
    "daily_trend": 4,
}
# 未登记段优先级(如后续新增段): 最低, 预算允许时才下发
_UNLISTED_PRIORITY = 5
# 被省略段登记键(随 feed 一起下发, 供模型区分"省略"与"无数据")
_TRIM_MARKER_KEY = "_trimmed_out_sections"
# 登记键自身占用的预算预留(字符): 约 80(键名 + 若干个段名)
_TRIM_MARKER_RESERVE = 120


def _feed_char_limit(limit: int | None = None) -> int:
    """feed 字符预算: 显式入参优先, 否则取 settings.AI_ANALYZE_MAX_CHARS。"""
    if limit is not None:
        return max(1000, int(limit))
    return max(1000, int(getattr(settings, "AI_ANALYZE_MAX_CHARS", 6000) or 6000))


def _plan_feed_sections(feed: dict, limit: int) -> tuple[dict, list[str]]:
    """按段优先级 + 预算决定保留哪些顶层段, 返回 (保留段, 被省略段名列表)。

    - 优先级 0(业务段/窗口元信息)**无条件保留**(数据完整性优先于字符上限);
    - 其余段按优先级顺序尝试加入, 装不下则整段省略并登记(不做段内切字符)。
    - 同优先级内保持 feed 的原始插入顺序(sorted 稳定排序)。
    """
    if not isinstance(feed, dict):
        return {}, []
    kept: dict = {}
    dropped: list[str] = []
    for k, v in sorted(feed.items(), key=lambda kv: _SECTION_PRIORITY.get(kv[0], _UNLISTED_PRIORITY)):
        if _SECTION_PRIORITY.get(k, _UNLISTED_PRIORITY) == 0:
            kept[k] = v  # 必保段: 不参与预算裁减
            continue
        cand = {**kept, k: v}
        if len(json.dumps(cand, ensure_ascii=False, default=str)) + _TRIM_MARKER_RESERVE <= limit:
            kept[k] = v
        else:
            dropped.append(k)
    return kept, dropped


def _dump_feed(kept: dict, dropped: list[str], limit: int) -> str:
    """保留段 + 省略登记 → JSON 文本(登记键仅在确有省略时加入)。"""
    out = dict(kept)
    if dropped:
        out[_TRIM_MARKER_KEY] = dropped
    text = json.dumps(out, ensure_ascii=False, default=str)
    if len(text) > limit:
        # 必保段自身已超预算(业务段完整性优先, 不裁数据) —— 记日志便于观察预算是否需上调
        logger.warning("ai_feed feed 超出预算 %s 字符(必保业务段优先, 未裁剪): 实际 %s", limit, len(text))
    return text


# 恒量段: 不管问句命中哪个 scope 都始终保留, 供模型理解窗口与口径
# (#98 层2): 这些段体积小但语义关键(period/date_start/end 喂窗口,
# _spec 喂口径单位), 截掉会让模型答错窗口或金额单位。
_CONSTANT_FEED_KEYS = ("period", "date", "date_start", "date_end",
                       "generated_at", "_spec", "range", "daily_trend",
                       "hourly_trend", "refund_recovery",
                       # #139: 投诉三源拆分投影, 让所有 scope 切片都保留(避免被切走)
                       "complaint_breakdown",
                       # #139: 周/月报路径补的 staff_top5 也作恒量段保留
                       "staff_top5",
                       # #189: 今日待办/进行中专项/异常事件计数 —— 原先未登记在此, 也不是 scope 名,
                       #   被 _select_feed_sections 永久丢弃(_enrich_with_today_kanban 白算一次 today_queue)
                       "queue")

# 业务段固定输出顺序(#189): 原实现用 `for s in scopes` 遍历 set, 段顺序随
# PYTHONHASHSEED 变化 → 在"字符前缀截断"下同一问句在不同容器启动时被裁的段不同
# (实测 seed=1/7/42 保 refund, seed=3/11 保 reception, seed=2 保 complaint)。
# 改为固定顺序, 保证同输入同输出(可复现)。
_BUSINESS_SECTION_ORDER = ("refund", "complaint", "reception")


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

    #189 (2026-09-24) 两项修正:
      ① 业务段**前置**(恒量段后置): 原先恒量段在前, 一旦按长度裁剪, 业务段必先被切;
      ② 段顺序**确定化**(固定 _BUSINESS_SECTION_ORDER), 不再依赖 set 迭代顺序。
    """
    if scopes is None:
        return feed
    if isinstance(scopes, str):
        scopes = {scopes}
    scopes = set(scopes) - {None, ""}
    if not scopes or not isinstance(feed, dict):
        return feed
    # ① 业务段在前, 固定顺序(确定性)
    out = {s: feed[s] for s in _BUSINESS_SECTION_ORDER if s in scopes and s in feed}
    # 未登记的业务段(如后续新增 scope): 按字典序补在其后, 同样保证确定性
    for s in sorted(x for x in scopes if x not in _BUSINESS_SECTION_ORDER):
        if s in feed and s not in out:
            out[s] = feed[s]
    # ② 恒量段(窗口/口径/投影)在后
    for k in _CONSTANT_FEED_KEYS:
        if k in feed and k not in out:
            out[k] = feed[k]
    return out


def build_messages(db: Session, period: str, date: str | None, question: str,
                   start: str | None = None, end: str | None = None,
                   window_label: str | None = None, window_source: str | None = None,
                   required_dimensions: list | None = None,
                   scope: str | None = None, format: str | None = None,
                   feed_limit: int | None = None,
                   call_mode: str = "live") -> list[dict]:
    """构造发给模型的 messages(三层 Prompt)。

    call_mode(#307): "live"(实时问答, 缺省) / "pregen"(后台预生成)。两模式目标相反:
      - live: 用户在等, 只问一个焦点 → gen_system_live(精简铁律: 只答所问/禁章节标题/
        verdict 直接引用/≤300 字), 并从 feed 裁掉 routes_prev(历史四路前值, 实测几乎
        不被引用, 纯占预算);
      - pregen: 后台无等待压力, 供摘取层定位 → gen_system(H2 五锚点全景结构)。
      system 均 DB 优先(load_effective_prompt), 代码常量兜底。调用方显式传参,
      不用 window_label 字符串匹配做主判据。

    window_label/window_source(工单 #66): 上层(ai.py)把"窗口怎么定出来的"传下来,
    既写进 Prompt 让模型明确当前窗口, 也便于排查 —— 自然语言推断错误时, 从这里就能看出
    模型被喂的是哪个窗口, 而不是靠猜。

    required_dimensions(工单 #152): 模板配置的业务维度清单(关键词数组), 追加到
    business_ctx 要求本次回答必须覆盖这些维度, 缺失维度写"暂无"不得省略。补算/
    深度实时生成与预生成共用, _SYSTEM_PROMPT 铁律不动(#134 H2 锚点纪律)。

    scope/format(工单 #181 专项分桶): 模板显式声明专项域与体裁, 预生成分桶时传入。
      - scope != overview: 强制把该专项段并入 feed 切片(与问句命中 scope 取并集),
        让退款桶只喂 refund 段、投诉桶只喂 complaint 段 —— feed 聚焦不截断,
        根治"单一日报覆盖全维度→feed 超长截断→涉诉/专项答暂无"的错配。
      - format: 注入【体裁要求】, 简报(brief)结论导向不展开逐项, 明细(detail)逐维度展开。

    feed_limit(工单 #189): feed 字符预算覆盖值(缺省走 settings.AI_ANALYZE_MAX_CHARS)。
      预生成按窗口粒度放宽(见 ai_report_worker._FEED_LIMIT_BY_PERIOD: daily 7000 /
      monthly 9500 等), 让业务段与月报 4.2k 的 daily_trend 能同时下发;
      实时问答不传(保持 3500, 用户等 60s)。
    """
    feed = _build_feed(db, period, date, start=start, end=end)
    # #307: live 模式裁 routes_prev(四路前值) —— 实时问句几乎不问四路前值对比,
    #   该块实测只占预算稀释关键段; 预生成保留(周/月报「趋势对比」锚点可能用到)。
    if call_mode != "pregen" and isinstance(feed.get("refund"), dict):
        feed["refund"].pop("routes_prev", None)
    # #98 层2: 按问句 scope 切 feed 段(只留命中业务段+恒量段), 降输入 token。
    # scopes 空集(问句未命中 refund/complaint/reception 关键词)→ 回退全量, 兼容。
    # ⚠️ 2026-09-15 修复: 改用 keywords_to_scopes 取所有命中 scope, 避免多域
    #   问句(如"今日经营总览：接待、退款、投诉一起说说")只取首个 scope 致
    #   其它业务段被切掉, AI 答"未提供"。
    # #181 专项分桶: 模板 scope 显式声明时并入切片(专项桶 feed 聚焦不截断)。
    scopes = keywords_to_scopes(question)
    if scope and scope != "overview":
        scopes = scopes | {scope}
    feed = _select_feed_sections(feed, scopes)
    # #189: 口径卡按 scope 过滤 —— `lead`(专项负责人 vs 行政组长, ~380 字符)只在接待域
    #   需要(glossary 仍会按问句路由注入); 未命中接待域时从 _spec 去掉, 把预算让给业务段。
    #   scopes 为空集 = 回退全量 feed(含接待段) → 保留 lead。
    if isinstance(feed.get("_spec"), dict) and scopes and "reception" not in scopes:
        feed["_spec"] = {k: v for k, v in feed["_spec"].items() if k != "lead"}
    # #189: 按段预算裁剪(整段取舍, 合法 JSON), 并取回被省略段名用于提示模型
    _limit = _feed_char_limit(feed_limit)
    _kept_feed, _dropped_sections = _plan_feed_sections(feed, _limit)
    feed_json = _dump_feed(_kept_feed, _dropped_sections, _limit)
    # 窗口字段(必保段, 一定在 _kept_feed 内)
    feed = _kept_feed
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
    # #189 超预算省略说明: 让模型区分"本次未随数据下发"与"系统无此数据"。
    #   旧实现是字符前缀截断且无任何说明, 模型只能按铁律 1 写"暂无/未提供" →
    #   报告通篇"数据未提供", 用户误判为系统取不到数(实际是被裁掉)。
    if _dropped_sections:
        business_ctx += (
            "\n\n【数据省略说明】本次给定数据中, 段 "
            + "、".join(_dropped_sections)
            + " 因长度预算被省略(数据在系统中存在, 只是未随本次下发); "
            "涉及这些段的内容必须写明\"本次未随数据下发(超预算)\", "
            "禁止写成\"系统无此数据/数据未提供\"; 未列出的段均已完整提供, 不得称其缺失。"
        )
    # #181 体裁要求: 模板声明 format 时注入(简报结论导向, 明细逐维度展开)。
    # 与 _SYSTEM_PROMPT 第 3 条「前台 ≤500 字/后台预生成可详尽」互补: 该条按调用方
    # 区分密度, 本条按模板体裁统一约束(专项桶简报即使后台预生成也保持精简)。
    if format in ("brief", "detail"):
        if format == "brief":
            _fmt_rule = "本次为【简报】(brief): 只输出核心结论与关键数字, 每个维度 ≤2 行, 不展开逐项明细。"
        else:
            _fmt_rule = "本次为【明细】(detail): 按维度逐项展开, 关键处可用表格, 不要只给结论。"
        business_ctx += f"\n\n【体裁要求】{_fmt_rule}"
    # #152 维度覆盖要求(模板配置的 required_dimensions): 本次回答必须覆盖这些维度,
    #   缺失的写"暂无", 不得省略维度(与铁律 1/3 配套, 防"未提供"式过简回答)。
    #   结构约定(源头上保证可校验): 每个维度必须以二级锚点行 **<维度>**：<数据> 输出,
    #   缺失的维度写成 **<维度>**：暂无 —— 校验器按此结构做纯结构检查, 不做语义猜测。
    if required_dimensions:
        # 条目可为字符串或同义词数组(❷ D9); 数组渲染成"甲(或写作乙)", 不用 str()
        # (实测 str(list) 的引号括号形态会困惑模型)。
        _dims = "、".join(str(d) if not isinstance(d, list) else "、".join(str(x) for x in d)
                          for d in required_dimensions if d)
        business_ctx += (
            "\n\n【本次必须覆盖维度】" +
            f"{_dims}(每个维度都要给出数据, 缺失的如实写\"暂无/未提供\", 不得省略该维度)\n" +
            "【维度输出格式】每个维度必须用二级锚点行开头(如 `**四路分布**：A 线上直退 23 笔、" +
            "B 线下 12 笔`); 某维度无数据/缺失时写成 `**<维度>**：暂无`(不得省略该锚点行, " +
            "不得只放在旁述/建议句里代替)"
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
    # #237(B2-2) 实体维度条件注入: 问句点名设备 SN / 排班关键词时, 追加设备行为·充值流量·
    #   设备标签·排班考勤段; 普通经营问答返回空串(零影响)。独立于 feed 预算裁剪, 保证点名信息下发。
    #   窗口取本次 feed 的 date_start/date_end(ISO, 切前 10 位 = 北京日历日)。
    try:
        from app.services.ai_data_supply import build_entity_context
        _win_s = (feed.get("date_start") or "")[:10] or None
        _win_e = (feed.get("date_end") or "")[:10] or None
        entity_ctx = build_entity_context(db, question, start=_win_s, end=_win_e)
    except Exception:  # noqa: BLE001  实体注入是增强项, 失败不阻断主问答
        logger.warning("ai_feed 实体段注入失败(忽略)")
        entity_ctx = ""
    if entity_ctx:
        data_ctx = f"{data_ctx}\n\n{entity_ctx}"
    # #178 提示词版本化: DB 有生效版本 → 用之；无/异常 → 代码常量兜底（铁律）
    # #307 双版拆分: pregen 用 gen_system(H2 五锚点全景), live 用 gen_system_live
    #   (精简铁律)。两码各自版本化, 生产 DB 需各 seed 一条(见 alembic #307 迁移)。
    from app.services.prompt_config_service import load_effective_prompt

    if call_mode == "pregen":
        system = load_effective_prompt(db, "gen_system") or _SYSTEM_PROMPT
    else:
        system = load_effective_prompt(db, "gen_system_live") or _SYSTEM_PROMPT_LIVE
    if focus:
        body = (
            f"{business_ctx}\n\n"
            "【本期关注点 / 分析视角(运营侧重参考, 供你组织回答时侧重; 数据仍以给定数据为准)】\n"
            f"{focus}\n\n{data_ctx}\n\n请回答以下问题：{question}"
        )
    else:
        body = f"{business_ctx}\n\n{data_ctx}\n\n请回答以下问题：{question}"
    # D9(2026-10-12) 输出契约收尾追加(仅预生成模板链路): 锚点行纪律只写在 prompt
    # 中段会被 detail 体裁+大 system 提示词淹没 —— 实测(diag-enum)中段注入后双模型
    # 0/8, 输出退化成无粗体纯文本维度行; 而末尾追加(boost 对照)双模型锚点行遵从度
    # 良好。放 user 消息最末(问题之后)吃近因效应。示例一律 <数据>/<N> 占位: 实测
    # 示例里的具体数字会被模型原文照抄成报告数据(幻觉注入)。仅 required_dimensions
    # 链路(预生成)受影响, 前台实时问答(required=None)不受影响。
    if required_dimensions:
        body += (
            "\n\n【输出契约(最高优先, 与上文冲突时以本条为准)】\n"
            "1. 上面【本次必须覆盖维度】里的每个维度, 必须各有一行以 `**<维度名>**：` "
            "开头的锚点行, 维度关键词原文必须出现在 `**` 标签文字中(如维度含\"四路\"写 "
            "`**四路分布**：<数据>`); 章节标题(##/###)不能替代锚点行。\n"
            "2. 锚点行的展开内容(列表/表格)写在锚点行之后的行里; 解析类维度(趋势/环比/"
            "同比/结论)同样必须各有锚点行(如 `**环比**：<数据>`), 不得只织入叙述。\n"
            "3. 锚点行里的数字必须来自【给定数据】; 严禁把提示词/本契约里的示例措辞或"
            "示例数字写进报告。\n"
            "4. 全文禁止出现 null/None/NaN 字样; 给定数据中为 null 的字段, 写\"暂无\"或省略。"
        )
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

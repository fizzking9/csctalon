"""AI 报告匹配层 (工单 #122)

设计:
- 用户请求 AI 助手时, 优先匹配预生成的公共报告(is_public=True)
- 匹配优先级:
  ① 窗口完全匹配(template.period == win.period AND snapshot_date == win.date)
  ② 窗口语义匹配(question 含模板 question_keywords 之一 AND snapshot_date >= win.date - 1 day)
  ③ 时点容差: 用户 12:25 请求, 允许返回 12:00 同步点的报告(30 分钟颗粒度, 走 created_at 容差)
- 命中: 直接返回 success + result_md + report_id(命中匹配), 不调 LLM
- 未命中: 返回 None, 调用方走原 #116 实时 LLM 路径
- 命中复用统计: matched_count +1 (用 UPDATE ... RETURNING 保证并发安全)

SQL 全部走参数化 text() + bind params(AGENTS.md 铁律)。
"""

import datetime as _dt
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.ai_report import AiReport
from app.models.ai_report_template import AiReportTemplate
from app.services.ai_focus_service import keywords_to_scopes

logger = logging.getLogger(__name__)

# 30 分钟颗粒度容差(用户 12:25 请求, 允许返回 12:00 同步点的报告)
# 实现: snapshot_date 是 date 类型(无时间), 当日生成的报告都视为"12:00 同步点"
# 所以同日匹配即可, 不需要更细粒度容差

# 时区(AGENTS.md 铁律: 时区换算走 Asia/Shanghai)
_TZ_CN = _dt.timezone(_dt.timedelta(hours=8))

# ── #192 (2026-09-24) 命中门槛参数 ─────────────────────────────────────────
# 背景: 命中链路会把"内容为空壳"的快照当有效命中返回 —— 实测生产 ai_reports#731/#678/#632
# (`hit_mode='section'`, 74~88 字) 全文只有"**四路分布**：暂无/未提供 / **涉诉**：暂无",
# 用户问"今日退款情况如何？四路分布和涉诉怎么样？"却拿到无信息回答, 且该"命中"还照常
# 计入 matched_count, 掩盖真实命中质量。
# 判据(三项任一命中即判"不可用"):
#   ① 问句问到的维度缺失 —— "问什么就必须有什么"(如问"四路分布和涉诉怎么样",
#      快照却把四路写成"暂无", 这份就不能用来回答该问句);
#   ② 缺失维度占比 >= _BLOCK_MISSING_RATIO(整体性空壳);
#   ③ 正文短于 _EMPTY_SHELL_CHARS 且有 required_dimensions(整体即空壳)。
# ⚠️ 为何不直接要求"全维度齐全": detail 体裁(周/月报 9 维度)的锚点结构本就不完全对齐
#    校验器, 要求全齐全会把周/月报快照全部拦下 → 命中率塌方、每次问答走 30~50s 实时
#    LLM, 体感更差(见 #192 关联里的依赖说明)。故改用"问到的维度 + 占比"双判据。
_BLOCK_MISSING_RATIO = 0.5
_EMPTY_SHELL_CHARS = 150


def _asked_dims(question: str, template: AiReportTemplate | None) -> list:
    """问句明确问到的维度(该模板 required_dimensions 中关键词出现在问句里的条目)。

    例: 问"今日退款情况如何？四路分布和涉诉怎么样？" + 维度[四路,涉诉,退款原因,挽回]
        → [四路, 涉诉](只有这两个关键词在问句里)。
    """
    if template is None:
        return []
    q = (question or "").lower()
    out: list = []
    for entry in template.required_dimensions or []:
        kws = entry if isinstance(entry, list) else [entry]
        if any(str(k).lower() in q for k in kws if k):
            out.append(entry)
    return out


def _snapshot_unusable(db: Session, report: AiReport,
                       template: AiReportTemplate | None,
                       question: str | None = None) -> tuple[bool, list]:
    """快照是否"不可用"(空壳): 返回 (unusable, missing)。

    - 模板无 required_dimensions / 校验异常 → 可用(不拦, 保持改造前行为)
    - 问句问到的维度有缺失 → 不可用(问什么就必须有什么)
    - 缺失占比 >= _BLOCK_MISSING_RATIO 或 正文 < _EMPTY_SHELL_CHARS → 不可用
    """
    if template is None:
        return False, []
    required = template.required_dimensions or []
    if not required:
        return False, []
    from app.services.ai_completeness_service import check_dimensions

    ck = check_dimensions(db, report, template)
    missing = ck.get("missing") or []
    if ck.get("complete"):
        return False, []
    body = report.answer_md or ""
    asked = _asked_dims(question, template) if question else []
    asked_missing = [d for d in asked if d in missing]
    ratio = len(missing) / len(required)
    unusable = bool(asked_missing) or ratio >= _BLOCK_MISSING_RATIO or len(body) < _EMPTY_SHELL_CHARS
    return unusable, missing


def _record_blocked(db: Session, report_id: int, template_code: str | None,
                    missing: list) -> None:
    """被门槛拦下的快照: 记缺失清单(可统计被拦次数) + 打日志。

    `ai_reports.latest_missing` 非空即代表"最近一次校验发现缺失", 被拦次数可直接
    `SELECT count(*) FROM ai_reports WHERE latest_missing IS NOT NULL` 统计。
    """
    try:
        from app.services.ai_report_service import record_latest_missing

        record_latest_missing(db, report_id, missing)
    except Exception as e:  # noqa: BLE001 记录失败不影响判定
        logger.warning("记录被拦快照缺失维度失败(report_id=%s): %s", report_id, e)
    logger.info("match 门槛拦下空壳快照: report_id=%s template=%s missing=%s",
                report_id, template_code, missing)


def _today_cn() -> _dt.date:
    """当前北京时间日期。"""
    return _dt.datetime.now(_TZ_CN).date()


def _question_matches_keywords(question: str, keywords: list | None) -> bool:
    """问句是否命中模板关键词(任一即命中)。

    keywords 为空或 None 视为不命中(避免空关键词的模板被所有问句命中)。
    """
    if not keywords:
        return False
    q = question.lower()
    for kw in keywords:
        if not kw:
            continue
        if str(kw).lower() in q:
            return True
    return False


def _count_keyword_hits(question: str, keywords: list | None) -> int:
    """问句命中模板关键词的数量(#143 排序用)。

    多 keyword 命中 = 更精确匹配(模板 3 月报 keywords['本月','这个月','月报','上月','同比']
    用户问「本月经营情况总览, 含同比环比...」命中 '本月'+'同比' = 2 个, 优先于单 keyword 命中。
    """
    if not keywords:
        return 0
    q = question.lower()
    n = 0
    for kw in keywords:
        if not kw:
            continue
        if str(kw).lower() in q:
            n += 1
    return n


def _question_format_intent(question: str) -> str | None:
    """#181 问句体裁意图: brief/detail/None(中性, 不参与体裁排序)。

    命中「明细」侧关键词(优先) → detail; 命中「简报」侧 → brief;
    双侧同命中或均未命中 → None(中性, 不约束匹配)。
    """
    q = question or ""
    detail_kws = ("明细", "详细", "具体", "清单", "逐笔", "逐条", "逐项", "完整", "展开", "全部")
    brief_kws = ("简报", "概况", "简单", "简要", "概述", "大概", "概括", "总结", "说说", "速览")
    detail_hit = any(k in q for k in detail_kws)
    brief_hit = any(k in q for k in brief_kws)
    if detail_hit and not brief_hit:
        return "detail"
    if brief_hit and not detail_hit:
        return "brief"
    return None  # 双侧同命中或均未命中 → 中性, 不约束匹配


def _scope_priority(tmpl_scope: str | None, q_scopes: set[str]) -> int:
    """#181 模板 scope 相对问句 scope 的匹配优先级。

    - 问句未命中任何专项(空集)→ overview 模板兜底, 专项模板同分(都可命中)
    - 问句命中专项 → 同专项模板最高(2) > overview 兜底(1) > 其它专项(0)
    """
    scope = (tmpl_scope or "overview").lower()
    if not q_scopes:
        return 1  # 泛问: 所有模板平等(overview 亦同分, 靠 keywords/时间裁决)
    if scope in q_scopes:
        return 2
    if scope == "overview":
        return 1
    return 0


def _window_jaccard(rs, re, qs, qe) -> float:
    """报告内容窗口[rs,re] 与问句窗口[qs,qe] 的天数 Jaccard(交/并), 区间贴合度。

    用途(滑动周/月, 2026-09-23): 当多份区间报告关键词打平时(如问「近7天环比」,
    rolling_7d 命中「近7天」、自然 weekly 命中「环比」各 1 个), 用窗口贴合度裁决。
    - 长度相等且边界对齐 → 1(自然周问自然周报、近7天问 rolling_7d 各自最高);
    - 大窗口套小问句(rolling_30d 去答「近7天」)会因并集被拉大而天然得低分,
      无需为窗口长度写硬规则。
    任一缺边界 → 0.0(单日/实时日报无 start/end, 不参与此维, 回退 period/created 排序)。
    """
    if not (rs and re and qs and qe):
        return 0.0
    try:
        lo, hi = max(rs, qs), min(re, qe)
        inter = (hi - lo).days + 1 if hi >= lo else 0
        union = (max(re, qe) - min(rs, qs)).days + 1
        return inter / union if union > 0 else 0.0
    except TypeError:
        return 0.0


def match_template_report(
    db: Session,
    question: str,
    win_period: str | None,
    win_date: str | None,
    win_start: _dt.date | None = None,
    win_end: _dt.date | None = None,
) -> tuple[AiReport | None, AiReportTemplate | None]:
    """匹配预生成的公共报告。

    匹配优先级:
    1. 强匹配: 问句命中某模板 keywords AND snapshot_period == win_period AND snapshot_date == win_date
       (问句与窗口都对应到同一模板的当日预生成报告)
    2. 语义匹配: 问句命中某模板 keywords AND snapshot_date 当日(允许跨 period)
       (问句对得上, 窗口稍偏仍可复用)
    3. 时点容差: 当日同 period 最新公共报告(无关键词命中也返回)
       (兜底, 让用户至少拿到一份当日报告)

    #151 窗口对齐(根因修复):
    - 单日窗口(start==end 或显式 date)→ 按 snapshot_date 匹配: daily 日报 start/end
      保持 None(#132), 只能靠 snapshot_date 对齐(问「昨天」= snapshot_date 昨日)。
    - 区间窗口(start<end, 如周/月)→ 按报告内容窗口[start,end]与问句窗口重叠匹配:
      weekly/monthly 预生成已落 start/end(#132), 问「上周」不再命中本周周报/今日日报。

    返回 (report, template) 或 (None, None); 调用方走 LLM 兜底。
    命中时 matched_count +1。
    """
    today = _today_cn()

    if win_start and win_end and win_start != win_end:
        # #151 区间窗口: 报告内容窗口与问句窗口重叠才命中(weekly/monthly 有 start/end;
        #   daily 日报 start/end=None 被排除, 避免区间问句命中今日实时日报)
        rows = db.execute(
            text("""SELECT r.id, r.task_id, r.user_id, r.template_id, r.question, r.period,
                      r.snapshot_date, r.snapshot_period, r.is_public,
                      r.answer_md, r.llm_model, r.llm_ms, r.total_ms,
                      r.prompt_tokens, r.completion_tokens, r.total_tokens,
                      r.window_source, r.window_label, r.created_at,
                      r.start AS r_start, r.end AS r_end
               FROM ai_reports r
               WHERE r.is_public = TRUE
                 AND r.start IS NOT NULL AND r.end IS NOT NULL
                 AND r.start <= :q_end AND r.end >= :q_start
               ORDER BY (r.snapshot_period = :period) DESC, r.created_at DESC"""),
            {"q_start": win_start, "q_end": win_end, "period": win_period or ""},
        ).mappings().all()
    elif win_date:
        # 单日(含 start==end 回退): 按 snapshot_date 匹配(日报 start/end=None)
        rows = db.execute(
            text("""SELECT r.id, r.task_id, r.user_id, r.template_id, r.question, r.period,
                      r.snapshot_date, r.snapshot_period, r.is_public,
                      r.answer_md, r.llm_model, r.llm_ms, r.total_ms,
                      r.prompt_tokens, r.completion_tokens, r.total_tokens,
                      r.window_source, r.window_label, r.created_at
               FROM ai_reports r
               WHERE r.is_public = TRUE
                 AND r.snapshot_date = :sdate
               ORDER BY (r.snapshot_period = :period) DESC, r.created_at DESC"""),
            {"sdate": win_date, "period": win_period or ""},
        ).mappings().all()
    else:
        # 无 win_date 时取当日
        rows = db.execute(
            text("""SELECT r.id, r.task_id, r.user_id, r.template_id, r.question, r.period,
                      r.snapshot_date, r.snapshot_period, r.is_public,
                      r.answer_md, r.llm_model, r.llm_ms, r.total_ms,
                      r.prompt_tokens, r.completion_tokens, r.total_tokens,
                      r.window_source, r.window_label, r.created_at
               FROM ai_reports r
               WHERE r.is_public = TRUE
                 AND r.snapshot_date = :today
               ORDER BY (r.snapshot_period = :period) DESC, r.created_at DESC"""),
            {"today": today, "period": win_period or ""},
        ).mappings().all()

    # #143: 候选按「keywords 命中数 DESC + period 匹配 DESC + created_at DESC」综合排序
    #   原 L1 仅按 (snapshot_period=win_period) DESC 排序, 当 eff.period=weekly 误导时
    #   (用户问「本月」被 effective() 强制 period=weekly), 周报排前面 + 周报 keywords
    #   含 '环比' 在用户问句里 → 误命中周报而非月报。
    #   改为多 keyword 命中优先(月报 keywords['本月','这个月','月报','上月','同比']
    #   命中 '本月'+'同比'=2 个, 优先于周报命中 '环比'=1 个)。
    # #181 多维: 在 keywords 命中之后追加 scope 匹配(专项模板优先于 overview 兜底,
    #   根治「今日退款+涉诉」命中整体日报→feed 截断→涉诉答暂无的错配)与 format 体裁匹配。
    q_scopes = keywords_to_scopes(question)
    q_fmt = _question_format_intent(question)
    sorted_rows = []
    for r in rows:
        tmpl = _get_template(db, r.get("template_id"))
        hits = _count_keyword_hits(question, tmpl.question_keywords if tmpl else None)
        # 区间窗口贴合度(滑动周/月裁决): 单日/实时日报无 start/end → 0, 不影响原排序
        win_fit = _window_jaccard(r.get("r_start"), r.get("r_end"), win_start, win_end)
        period_match = (r.get("snapshot_period") == win_period) if win_period else False
        scope_score = _scope_priority(getattr(tmpl, "scope", "overview") if tmpl else None, q_scopes)
        fmt_score = 1 if (
            q_fmt and tmpl and getattr(tmpl, "format", "brief") == q_fmt
        ) else 0
        sorted_rows.append((r, tmpl, hits, win_fit, period_match, scope_score, fmt_score))
    # 排序: 关键词命中数 DESC → 窗口贴合度 DESC → period 匹配 DESC → scope DESC →
    #       format DESC → created_at DESC
    # 窗口贴合度置于 period 匹配前: 时间解析把跨天「近7天」强制成 win_period='weekly' 时,
    # 仍由 Jaccard 让 rolling_7d(0.75) 胜过自然 weekly 短窗; 自然周/月报与其报告完全对齐
    # Jaccard=1, 不受影响。
    sorted_rows.sort(
        key=lambda x: (
            -x[2],
            -x[3],
            0 if x[4] else 1,
            -x[5],
            -x[6],
            -(x[0].get("created_at") or _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)).timestamp(),
        ),
    )

    # 取第一条 keyword 命中数 > 0 的返回
    for r, tmpl, hits, win_fit, period_match, scope_score, fmt_score in sorted_rows:
        if hits > 0 and tmpl:
            row = dict(r)
            # #192 命中门槛: 先判快照是否为空壳(缺维度占比过高/正文过短) —— 是则跳过继续找,
            #   避免返回"**四路分布**：暂无/未提供"这类无信息回答; 全部不可用 → (None,None)
            #   交调用方走实时/深度重算(保留原有兜底, 不出现"无答案")。
            light = _light_report(row)
            unusable, missing = _snapshot_unusable(db, light, tmpl, question)
            if unusable:
                _record_blocked(db, light.id, getattr(tmpl, "code", None), missing)
                continue
            report = _wrap_row(db, row)
            # #128: _wrap_row 内 _bump_matched_count 调 db.commit() → expire_on_commit
            #       把 tmpl 属性标 expired; 脱离 session 前 expunge 才能让调用方
            #       在 _match_db.close() 后安全读 tmpl.code(否则 DetachedInstanceError 500)。
            _code = tmpl.code  # 显式读一次, 确保属性已加载进实例
            db.expunge(tmpl)
            logger.info("match_template_report: 命中 report_id=%s template=%s "
                        "keyword_hits=%d win_fit=%.3f period_match=%s scope_score=%s fmt_score=%s",
                        r["id"], _code, hits, win_fit, period_match, scope_score, fmt_score)
            return report, tmpl

    # 未命中: 返回 None, 调用方走 LLM 兜底
    # (不兜底时点容差, 避免"上月对比"等无关问句命中当日 daily 报告)
    return None, None


def _light_report(row: dict) -> AiReport:
    """SQL row dict → 轻量 AiReport(不 bump matched_count; 命中门槛/候选列表共用)。"""
    r = AiReport()
    for k, v in row.items():
        try:
            setattr(r, k, v)
        except Exception:  # noqa: BLE001  跳过 ORM 不接受的字段
            pass
    return r


def _wrap_row(db: Session, row: Any) -> AiReport:
    """把 SQL row 包装为 AiReport ORM 实例(轻量, 不重新查询)。

    命中匹配后, 调用方需要的字段: answer_md / template_id / id / matched_count +1。
    """
    if isinstance(row, dict):
        # 从 mappings().first() 来的 dict
        r = AiReport()
        for k, v in row.items():
            try:
                setattr(r, k, v)
            except Exception:  # noqa: BLE001  跳过 ORM 不接受的字段
                pass
        # 命中后 matched_count +1(统一走记账入口, 与 L2 一致, 见 bump_hit_accounting)
        bump_hit_accounting(db, r.id)
        return r
    return row


def list_candidates(db: Session, win_period: str | None,
                    win_date: str | None,
                    win_start: _dt.date | None = None,
                    win_end: _dt.date | None = None,
                    question: str | None = None) -> list[AiReport]:
    """#135 L2 语义匹配用: 取当日 is_public=True 报告列表(不 bump matched_count)。

    复用 match_template_report 的窗口对齐逻辑(#151): 区间窗口按报告内容窗口
    [start,end] 重叠匹配, 单日按 snapshot_date, 无窗口取当日; 排序((snapshot_period
    =win_period) DESC, created_at DESC), 但不调 _bump_matched_count。
    返回 list[AiReport](轻量 ORM 包装, 字段与 match_template_report 一致)。
    """
    today = _today_cn()
    if win_start and win_end and win_start != win_end:
        # #151 区间窗口: 报告内容窗口与问句窗口重叠(weekly/monthly 有 start/end)
        rows = db.execute(
            text("""SELECT r.id, r.task_id, r.user_id, r.template_id, r.question, r.period,
                      r.snapshot_date, r.snapshot_period, r.is_public,
                      r.answer_md, r.llm_model, r.llm_ms, r.total_ms,
                      r.prompt_tokens, r.completion_tokens, r.total_tokens,
                      r.window_source, r.window_label, r.created_at,
                      r.start AS r_start, r.end AS r_end
               FROM ai_reports r
               WHERE r.is_public = TRUE
                 AND r.start IS NOT NULL AND r.end IS NOT NULL
                 AND r.start <= :q_end AND r.end >= :q_start
               ORDER BY (r.snapshot_period = :period) DESC, r.created_at DESC"""),
            {"q_start": win_start, "q_end": win_end, "period": win_period or ""},
        ).mappings().all()
    else:
        # 单日/无窗口: 按 snapshot_date(日报 start/end=None, 只能按快照日期对齐)
        sdate = win_date or today
        rows = db.execute(
            text("""SELECT r.id, r.task_id, r.user_id, r.template_id, r.question, r.period,
                      r.snapshot_date, r.snapshot_period, r.is_public,
                      r.answer_md, r.llm_model, r.llm_ms, r.total_ms,
                      r.prompt_tokens, r.completion_tokens, r.total_tokens,
                      r.window_source, r.window_label, r.created_at
               FROM ai_reports r
               WHERE r.is_public = TRUE
                 AND r.snapshot_date = :sdate
               ORDER BY (r.snapshot_period = :period) DESC, r.created_at DESC"""),
            {"sdate": sdate, "period": win_period or ""},
        ).mappings().all()
    out: list[AiReport] = []
    for r in rows:
        light = _light_report(dict(r))
        # #192 候选也过同一道门槛: L2 语义匹配从候选里挑, 若把空壳快照留在候选里,
        #   语义最像的那份(往往正是缺数据的那份)会被选中 → 同样是"命中但无信息"。
        _tmpl = _get_template(db, light.template_id)
        unusable, missing = _snapshot_unusable(db, light, _tmpl, question)
        if unusable:
            _record_blocked(db, light.id, getattr(_tmpl, "code", None), missing)
            continue
        out.append(light)
    return out


def _get_template(db: Session, template_id: int | None) -> AiReportTemplate | None:
    """查询模板(命中匹配时返回, 用于调用方知道是哪个模板生成的)。"""
    if not template_id:
        return None
    return db.query(AiReportTemplate).get(template_id)


def bump_hit_accounting(db: Session, report_id: int) -> None:
    """命中记账唯一入口(#193): matched_count +1。

    ⚠️ 为什么单独提出这一层: 原先只有 L1 命中(`_wrap_row`)会 bump, **L2 语义命中不 bump**
    → `matched_count` 系统性偏低(生产实测 7 天: L1 口径 61 份 vs 用户实际拿到命中 90 次,
    差 32%), 会让"刷新频率 vs 命中量"的决策建立在偏小的命中率上。
    现在 L1/L2 都走这里; 被 #192 门槛拦下的空壳**不计入**(未交付给用户)。
    """
    _bump_matched_count(db, report_id)


def _bump_matched_count(db: Session, report_id: int) -> None:
    """matched_count +1 (并发安全: UPDATE ... RETURNING)。

    失败仅 log, 不影响主流程(命中返回报告仍然有效, 只是统计少一次)。
    """
    try:
        result = db.execute(
            text("""UPDATE ai_reports
                    SET matched_count = matched_count + 1
                    WHERE id = :rid
                    RETURNING matched_count"""),
            {"rid": report_id},
        )
        new_count = result.scalar()
        db.commit()
        logger.info("matched_count bumped: report_id=%s new_count=%s", report_id, new_count)
    except Exception as e:  # noqa: BLE001  统计失败不影响主流程
        db.rollback()
        logger.warning("matched_count bump failed (report_id=%s): %s", report_id, e)

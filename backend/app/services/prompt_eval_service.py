"""AI 提示词评估中心服务 (工单 #173, 设计: docs/design/AI提示词评估中心-20260920.md)

设计说明（Design Rationale）
- 目的：把「执行结果 → 评估分析 → 半自动迭代」的提示词闭环做成生产可见服务：
  两级评估（规则级 = 模板命中/治理规则健康度；提示词段 = 关键词命中/锚点一致性）+
  跨链路关联（生成侧 _SYSTEM_PROMPT 锚点 ↔ 摘取侧 H2_ANCHORS ↔ 报告实际 H2 分布）。
- 逻辑：评估全部是**聚合读**（与 #153 build_health_report 同口径）；建议是**纯规则**产出
  （不调 LLM，避免 27b 冷加载超时），写入走 prompt_iterations 状态机（proposed→confirmed
  →applied/rejected），人工一键确认后服务端应用。
- 口径：hit_mode 分布 / 模板快照 matched_count / 高频 live 兜底问句阈值（live≥2 次）与
  #153 ai_feedback_service 同源；窗口 = 近 N 天北京时间 created_at AT TIME ZONE 'Asia/Shanghai'。
- 取舍：
  ① 不自动改 _SYSTEM_PROMPT/_EXTRACT_SYSTEM 代码常量（提示词铁律）—— 锚点漂移只出
     anchor_guide 落地指引，不提供写入路径；
  ② 新模板(new_template)只记建议不自动建（建模板需人工补 required_dimensions）；
  ③ 模板无 version 列（不为此加列），「版本留痕」由 prompt_iterations 行本身承担
     （old_value→new_value + applied_at/applied_by 即一版账本）。
"""

import asyncio
import datetime as _dt
import json
import logging
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.ai_report_template import AiReportTemplate
from app.models.governance_prompt_iteration import (
    ITER_SCOPE_ANCHOR_GUIDE,
    ITER_SCOPE_HUMAN_OPINION,
    ITER_SCOPE_NEW_TEMPLATE,
    ITER_SCOPE_RULE_ADJUST,
    ITER_SCOPE_TEMPLATE_KEYWORD,
    ITER_SCOPE_TEMPLATE_QUESTION,
    ITER_STATUS_APPLIED,
    ITER_STATUS_CONFIRMED,
    ITER_STATUS_PROPOSED,
    ITER_STATUS_REJECTED,
    OPINION_CATEGORIES,
    PromptIteration,
)
from app.models.governance_rule import GovernanceRule

logger = logging.getLogger(__name__)

_TZ_CN = _dt.timezone(_dt.timedelta(hours=8))

# 建议阈值（与 #153 ai_feedback_service 同源：全树统一口径，不另写一份）
_LIVE_RATIO_WARN = 0.5      # live 兜底占比 ≥50% → 提示词层告警
_REPEAT_LIVE_MIN = 2        # 同问句+同窗口重复 live ≥2 次 → 高频未答好问题
_TOP_N = 8                  # 高频问题/建议 TopN
_FP_RATIO_WARN = 0.5        # 规则误报率 >50% → 建议停用/降级

# ── 跨链路锚点（生成侧从 _SYSTEM_PROMPT 铁律3 提取，不另写一份防漂移）─────────
# 摘取侧锚点常量（ai_extract_service.H2_ANCHORS，同一套 5 锚点）
from app.services.ai_extract_service import H2_ANCHORS  # noqa: E402
from app.services.ai_feed import _SYSTEM_PROMPT as _GEN_SYSTEM_PROMPT  # noqa: E402

# _SYSTEM_PROMPT 铁律3 用反引号包裹锚点: "`## 接待分析` / `## 退款分析` / ..."
_GEN_ANCHOR_RE = re.compile(r"`(## [^`]+)`")
# 报告实际 H2 提取（answer_md 中的行首 ## 标题）
_H2_TITLE_RE = re.compile(r"^##\s+.+$", re.MULTILINE)

# 关键词候选提取用的停用词/分隔（朴素启发式，仅供人工确认参考，不追求分词质量）
_SEP_RE = re.compile(r"[\s，。！？、；：,.!?;:()（）/\\|—\-]+")
_STOP_WORDS = frozenset({
    "的", "了", "是", "吗", "呢", "啊", "如何", "怎么", "怎样", "怎么样",
    "一下", "都", "和", "与", "及", "今天", "今日", "昨日", "情况", "我们", "看看",
})


def _now_cn() -> _dt.datetime:
    return _dt.datetime.now(_TZ_CN)


def _since_ts(days: int) -> str:
    """近 N 天北京时间窗口起点（与 #153 同口径: 当日 00:00 起）。"""
    start = (_now_cn() - _dt.timedelta(days=days - 1)).strftime("%Y-%m-%d")
    return f"{start}T00:00:00+08:00"


def _generation_anchors() -> list[str]:
    """生成侧锚点：从 _SYSTEM_PROMPT 铁律3 提取（铁律改则评估自动跟随）。"""
    return _GEN_ANCHOR_RE.findall(_GEN_SYSTEM_PROMPT)


# ════════════════════════ 评估（聚合读，不落库）════════════════════════

def build_summary(db: Session, days: int = 7) -> dict:
    """评估中心总览（与 #153 build_health_report 同口径的窗口聚合）。"""
    since_ts = _since_ts(days)

    # ① 命中分布（本人私有实时问答行）
    by_mode = {"full": 0, "section": 0, "live": 0, "rule": 0}
    rows = db.execute(
        text("""SELECT hit_mode, COUNT(*) AS c
                FROM ai_reports
                WHERE is_public = FALSE AND hit_mode IS NOT NULL
                  AND created_at >= :since_ts
                GROUP BY hit_mode"""),
        {"since_ts": since_ts},
    ).all()
    for mode, c in rows:
        if mode in by_mode:
            by_mode[mode] = int(c)
    questions = sum(by_mode.values())
    hits = by_mode["full"] + by_mode["section"]
    hit_rate = hits / questions if questions else 0.0
    live_ratio = by_mode["live"] / questions if questions else 0.0

    # ② 模板命中热度（窗口内公共快照 + 被命中复用合计）
    tmpl_rows = db.execute(
        text("""SELECT t.id, t.code, t.name_cn, t.period, t.is_active,
                       COUNT(r.id) AS snapshots,
                       COALESCE(SUM(r.matched_count), 0) AS matched
                FROM ai_report_templates t
                LEFT JOIN ai_reports r
                       ON r.template_id = t.id AND r.is_public = TRUE
                      AND r.created_at >= :since_ts
                GROUP BY t.id, t.code, t.name_cn, t.period, t.is_active
                ORDER BY t.priority, t.id"""),
        {"since_ts": since_ts},
    ).mappings().all()
    templates = [
        {
            "id": int(r["id"]), "code": r["code"], "name_cn": r["name_cn"],
            "period": r["period"], "is_active": bool(r["is_active"]),
            "snapshots": int(r["snapshots"]), "matched": int(r["matched"]),
        }
        for r in tmpl_rows
    ]
    zero_hit = [t for t in templates if t["snapshots"] > 0 and t["matched"] == 0]

    # ③ 治理规则健康度（命中过 + 效果回填）
    rules = build_governance_eval(db)
    rule_warns = [r for r in rules if r["fp_ratio"] is not None and r["fp_ratio"] > _FP_RATIO_WARN]

    # ④ 高频 live 兜底问句（未答好问题）
    top_live = _top_live_questions(db, since_ts)

    return {
        "window": {"days": days, "start": since_ts[:10], "generated_at": _now_cn().strftime("%Y-%m-%d %H:%M:%S")},
        "overall": {"questions": questions, "by_mode": by_mode,
                    "hit_rate": round(hit_rate, 4), "live_ratio": round(live_ratio, 4),
                    "live_ratio_warn": live_ratio >= _LIVE_RATIO_WARN},
        "template_hits": {"total": len(templates), "snapshots": sum(t["snapshots"] for t in templates),
                          "zero_hit_count": len(zero_hit)},
        "rule_health": {"total": len(rules), "warn_count": len(rule_warns)},
        "top_live_qs": top_live,
    }


def build_template_eval(db: Session, days: int = 7) -> list[dict]:
    """模板级评估：命中率 + 关键词 + 缺失维度，按命中率升序（问题模板在前）。"""
    since_ts = _since_ts(days)
    rows = db.execute(
        text("""SELECT t.id, t.code, t.name_cn, t.period, t.detail_level,
                       t.question_keywords, t.question_template, t.is_active,
                       COUNT(r.id) AS snapshots,
                       COALESCE(SUM(r.matched_count), 0) AS matched,
                       COUNT(*) FILTER (WHERE jsonb_typeof(r.latest_missing) = 'array'
                                        AND jsonb_array_length(r.latest_missing) > 0) AS with_missing
                FROM ai_report_templates t
                LEFT JOIN ai_reports r
                       ON r.template_id = t.id AND r.is_public = TRUE
                      AND r.created_at >= :since_ts
                GROUP BY t.id, t.code, t.name_cn, t.period, t.detail_level,
                         t.question_keywords, t.question_template, t.is_active
                ORDER BY t.priority, t.id"""),
        {"since_ts": since_ts},
    ).mappings().all()
    result = []
    for r in rows:
        snapshots = int(r["snapshots"])
        matched = int(r["matched"])
        result.append({
            "id": int(r["id"]), "code": r["code"], "name_cn": r["name_cn"],
            "period": r["period"], "detail_level": r["detail_level"],
            "is_active": bool(r["is_active"]),
            "question_keywords": r["question_keywords"] or [],
            "question_template": r["question_template"] or "",
            "snapshots": snapshots, "matched": matched,
            "hit_rate": round(matched / snapshots, 4) if snapshots else None,
            "with_missing": int(r["with_missing"]),
        })
    # 命中率升序：None（本期未生成）排后，0 命中排最前
    result.sort(key=lambda t: (t["hit_rate"] is None, t["hit_rate"] if t["hit_rate"] is not None else 1.0))
    return result


def build_anchor_check(db: Session, days: int = 7) -> dict:
    """跨链路锚点一致性：生成侧 vs 摘取侧静态比对 + 报告实际 H2 分布（漂移检测）。"""
    gen_anchors = _generation_anchors()
    extract_anchors = list(H2_ANCHORS)
    static_match = (sorted(gen_anchors) == sorted(extract_anchors))

    # 报告实际 H2（窗口内周报/月报公共快照）
    since_ts = _since_ts(days)
    rows = db.execute(
        text("""SELECT answer_md FROM ai_reports
                WHERE is_public = TRUE AND period IN ('weekly', 'monthly')
                  AND created_at >= :since_ts
                  AND answer_md IS NOT NULL"""),
        {"since_ts": since_ts},
    ).scalars().all()
    total_reports = len(rows)
    anchor_hits: dict[str, int] = {a: 0 for a in gen_anchors}
    non_anchor: dict[str, int] = {}
    for md in rows:
        found = set(_H2_TITLE_RE.findall(md))
        for a in gen_anchors:
            if a in found:
                anchor_hits[a] += 1
        for h in found:
            if h not in gen_anchors:
                non_anchor[h] = non_anchor.get(h, 0) + 1

    missing = [
        {"anchor": a, "missing_count": total_reports - c, "total_reports": total_reports}
        for a, c in anchor_hits.items() if total_reports and c < total_reports
    ]
    return {
        "generation_anchors": gen_anchors,
        "extract_anchors": extract_anchors,
        "static_match": static_match,
        "reports_checked": total_reports,
        "missing_anchors": missing,
        "non_anchor_h2": sorted(non_anchor.items(), key=lambda kv: -kv[1])[:_TOP_N],
    }


def build_governance_eval(db: Session) -> list[dict]:
    """治理规则健康度：命中 + 效果回填 + fp_ratio（>阈值预警）。"""
    rules = db.query(GovernanceRule).order_by(GovernanceRule.hit_count.desc(), GovernanceRule.id).all()
    result = []
    for r in rules:
        total_effect = r.effect_executed + r.effect_rejected + r.effect_fp
        fp_ratio = (r.effect_fp / total_effect) if total_effect else None
        result.append({
            "id": r.id, "detector": r.detector, "table_name": r.table_name,
            "target_pattern": r.target_pattern, "verdict": r.verdict,
            "disposition": r.disposition, "enabled": r.enabled,
            "hit_count": r.hit_count,
            "effect_executed": r.effect_executed, "effect_rejected": r.effect_rejected,
            "effect_fp": r.effect_fp,
            "fp_ratio": round(fp_ratio, 4) if fp_ratio is not None else None,
            "fp_warn": fp_ratio is not None and fp_ratio > _FP_RATIO_WARN,
        })
    return result


def _top_live_questions(db: Session, since_ts: str) -> list[dict]:
    """高频未答好问题（同问句+同窗口 live ≥2 次，与 #153 同口径）。"""
    rows = db.execute(
        text("""SELECT question, "start", "end", COUNT(*) AS c
                FROM ai_reports
                WHERE is_public = FALSE AND hit_mode = 'live'
                  AND created_at >= :since_ts
                GROUP BY question, "start", "end"
                HAVING COUNT(*) >= :min_repeat
                ORDER BY c DESC, question
                LIMIT :top"""),
        {"since_ts": since_ts, "min_repeat": _REPEAT_LIVE_MIN, "top": _TOP_N},
    ).mappings().all()
    return [
        {
            "question": r["question"],
            "start": str(r["start"]) if r["start"] else None,
            "end": str(r["end"]) if r["end"] else None,
            "count": int(r["c"]),
        }
        for r in rows
    ]


# ════════════════════════ 半自动建议（纯规则，不调 LLM）════════════════════

def build_suggestions(db: Session, days: int = 7) -> list[dict]:
    """按执行结果产半自动迭代建议（不落库；POST /suggest 落库为 proposed 行）。

    归因规则（设计 §6.2）：
      · 模板快照>0 且 0 命中 → 关键词不贴 → 建议补关键词（候选词从高频 live 问句提取）
      · 同问句+同窗口 live≥2 次 → 覆盖缺 → 建议补关键词或新建模板（record only）
      · 锚点漂移（约定锚点缺失 / 非约定 H2）→ anchor_guide 落地指引（record only）
      · 规则 fp_ratio>0.5 → 建议停用/降级（rule_adjust）
    """
    suggestions: list[dict] = []
    templates = build_template_eval(db, days)
    top_live = _top_live_questions(db, _since_ts(days))
    live_questions = [t["question"] for t in top_live]

    # ① 0 命中模板 → 补关键词（候选词来自高频 live 问句）
    for t in templates:
        if t["snapshots"] > 0 and t["matched"] == 0:
            candidates = _candidate_words(live_questions, t["question_keywords"])
            if candidates:
                suggestions.append({
                    "scope": ITER_SCOPE_TEMPLATE_KEYWORD,
                    "target_id": t["id"], "target_name": t["name_cn"],
                    "suggestion": "向 question_keywords 追加候选词: " + "、".join(candidates),
                    "reason": f"近{days}天生成 {t['snapshots']} 份快照但 0 次被命中（关键词不贴用户问法）",
                    "old_value": "、".join(t["question_keywords"]),
                    "new_value": "、".join(_dedup(t["question_keywords"] + candidates)),
                })

    # ② 高频兜底问句 → 补关键词 / 建模板（new_template 只记录）
    for q in top_live:
        kw_target = _best_template_for_question(templates, q["question"])
        if kw_target:
            candidates = _candidate_words([q["question"]], kw_target["question_keywords"])
            if candidates:
                suggestions.append({
                    "scope": ITER_SCOPE_TEMPLATE_KEYWORD,
                    "target_id": kw_target["id"], "target_name": kw_target["name_cn"],
                    "suggestion": "向 question_keywords 追加候选词: " + "、".join(candidates),
                    "reason": f"问句「{q['question'][:30]}」重复 {q['count']} 次走实时兜底",
                    "old_value": "、".join(kw_target["question_keywords"]),
                    "new_value": "、".join(_dedup(kw_target["question_keywords"] + candidates)),
                })
        else:
            suggestions.append({
                "scope": ITER_SCOPE_NEW_TEMPLATE,
                "target_id": None, "target_name": "（待建模板）",
                "suggestion": f"评估新增模板覆盖问句:「{q['question'][:40]}」（出现 {q['count']} 次）",
                "reason": f"近{days}天无模板命中该问句, 重复 {q['count']} 次走实时兜底",
                "old_value": None, "new_value": None,
            })

    # ③ 锚点漂移 → 落地指引（record only，不提供写入）
    anchor = build_anchor_check(db, days)
    if not anchor["static_match"]:
        suggestions.append({
            "scope": ITER_SCOPE_ANCHOR_GUIDE,
            "target_id": None, "target_name": "提示词锚点",
            "suggestion": "生成侧(_SYSTEM_PROMPT 铁律3)与摘取侧(H2_ANCHORS)锚点不一致, "
                          f"生成={anchor['generation_anchors']} 摘取={anchor['extract_anchors']}",
            "reason": "锚点漂移会导致摘取层按错误锚点切分报告",
            "old_value": None, "new_value": None,
        })
    for m in anchor["missing_anchors"]:
        if m["missing_count"] > 0:
            suggestions.append({
                "scope": ITER_SCOPE_ANCHOR_GUIDE,
                "target_id": None, "target_name": "报告锚点",
                "suggestion": f"约定锚点「{m['anchor']}」在 {m['missing_count']}/{m['total_reports']} 份周/月报缺失",
                "reason": "生成侧未按铁律3 输出全部锚点, 摘取层对应段落将不可用",
                "old_value": None, "new_value": None,
            })
    for h2, c in anchor["non_anchor_h2"]:
        if c > 0:
            suggestions.append({
                "scope": ITER_SCOPE_ANCHOR_GUIDE,
                "target_id": None, "target_name": "报告锚点",
                "suggestion": f"出现 {c} 次非约定 H2 标题「{h2}」",
                "reason": "非约定锚点无法被摘取层 H2 映射识别",
                "old_value": None, "new_value": None,
            })

    # ④ 规则误报率高 → 建议停用/降级
    for r in build_governance_eval(db):
        if r["fp_warn"]:
            suggestions.append({
                "scope": ITER_SCOPE_RULE_ADJUST,
                "target_id": r["id"], "target_name": r["target_pattern"],
                "suggestion": f"误报率 {r['fp_ratio']:.0%} 偏高（误报 {r['effect_fp']} 次）, 建议停用或下调动作级别",
                "reason": "fp_ratio > 50% 说明规则判定与业务实际偏离",
                "old_value": None, "new_value": None,
            })

    return suggestions[:_TOP_N * 3]


# ════════════════════════ LLM 辅助建议（工单 #177，规则+LLM 双通道）═════════════════

_LLM_LEVEL = 1                # llm_scheduler L1（admin 手动触发，不占 L2 实时问答）
_LLM_SOURCE = "prompt_eval_suggest"   # chat_completion 审计 source
_LLM_TIMEOUT_S = 60.0         # LLM 分析超时（27b 冷加载 35s，留足余量）
_LLM_MAX_TOKENS = 1200        # 结构化 JSON 输出 token 上限
_LLM_FALLBACK_OK = True       # LLM 失败/非法 → 回退纯规则路径（工单 #177 验收）

# LLM 输出 schema（与 _validate_llm_suggestions 一一对应）
_LLM_OUTPUT_SCHEMA = """{
  "suggestions": [
    {
      "scope": "template_keyword | template_question",
      "target_id": 1,
      "keywords": ["候选词1", "候选词2"],
      "question_template": "完整新问句(scope=template_question 时必填)",
      "reason": "分析理由(引用评估信号与人工意见)"
    }
  ]
}"""

# LLM 系统提示词：只产「补关键词/改问句」两类可写入建议，禁止越权建议
_LLM_SYSTEM_PROMPT = (
    "你是提示词优化分析师。系统会给你近 N 天的评估证据：模板命中率、高频未答好问句、"
    "人工意见、锚点漂移。请判断提示词哪里不贴用户问法，产出结构化建议。\n"
    "【输出 JSON 结构】\n{output_schema}\n"
    "【铁律】\n"
    "1. 只允许 scope=template_keyword（补关键词）或 template_question（改问句），"
    "   其他类型（新建模板/调规则/改代码常量）一律不产出。\n"
    "2. target_id 必须来自给定模板列表中的真实 id，禁止编造。\n"
    "3. keywords 必须来自给定证据中的真实用户问句/人工意见，长度 2~8 字，"
    "   禁止编造不存在的业务词；不重复已有关键词。\n"
    "4. template_question 必须保留原问句中的 {{占位符}}（如 {{date}} {{period}}），"
    "   只在证据明确指向问句缺陷时才给出。\n"
    "5. reason 必须引用具体证据（命中率/次数/意见原文），禁止泛泛而谈。\n"
    "6. 没有充分证据时不产出该条建议，宁可少给不可乱给。\n"
    "只输出 JSON，禁止任何解释文字或 markdown 代码块标记。"
)


def _confirmed_opinions(db: Session, limit: int = 10) -> list[str]:
    """已采纳(confirmed)人工意见（#177：作为 LLM 分析输入的「业务直觉」信号）。"""
    rows = db.query(PromptIteration).filter(
        PromptIteration.scope == ITER_SCOPE_HUMAN_OPINION,
        PromptIteration.status == ITER_STATUS_CONFIRMED,
    ).order_by(PromptIteration.confirmed_at.desc()).limit(limit).all()
    return [r.suggestion for r in rows]


def _llm_suggest_context(db: Session, days: int) -> str:
    """LLM 分析输入：评估聚合 + 人工意见，压缩为一段文本（token 可控）。"""
    summary = build_summary(db, days)
    templates = build_template_eval(db, days)
    anchor = build_anchor_check(db, days)
    live = _top_live_questions(db, _since_ts(days))
    opinions = _confirmed_opinions(db)

    lines: list[str] = [f"评估窗口: 近{days}天(北京时间)"]
    ov = summary["overall"]
    lines.append(
        f"问答命中: {ov['questions']} 次, 命中率 {ov['hit_rate']:.0%}, "
        f"实时兜底占比 {ov['live_ratio']:.0%}"
    )
    lines.append("模板现状(命中率升序):")
    for t in templates[:8]:
        hit = f"{t['hit_rate']:.0%}" if t["hit_rate"] is not None else "本期未生成"
        lines.append(
            f"- id={t['id']} {t['name_cn']}({t['code']}) 快照{t['snapshots']} "
            f"命中{t['matched']} 命中率{hit} 关键词={t['question_keywords'] or []}"
        )
    if live:
        lines.append("高频未答好问句:")
        for q in live[:5]:
            lines.append(f"- 「{q['question'][:40]}」×{q['count']}次")
    if opinions:
        lines.append("已采纳人工意见:")
        for op in opinions[:5]:
            lines.append(f"- {op[:80]}")
    if not anchor["static_match"] or anchor["missing_anchors"]:
        lines.append(f"锚点漂移: 生成侧={anchor['generation_anchors']}")
        for m in anchor["missing_anchors"][:3]:
            lines.append(f"- 锚点「{m['anchor']}」缺 {m['missing_count']}/{m['total_reports']} 份")
    return "\n".join(lines)


def _validate_llm_suggestions(obj: dict, templates: list[dict]) -> list[dict]:
    """LLM 输出 schema 校验 + 规范化为建议条目（与 _LLM_OUTPUT_SCHEMA 对应）。

    任一非法 → 丢弃该条（宁可少给，不可乱给）；整体无合法条目 → 返回 [] → 调用方回退纯规则。
    """
    arr = obj.get("suggestions") if isinstance(obj, dict) else None
    if not isinstance(arr, list) or not arr:
        return []
    tmpl_by_id = {t["id"]: t for t in templates}
    out: list[dict] = []
    for s in arr:
        if not isinstance(s, dict):
            continue
        scope = s.get("scope")
        if scope not in (ITER_SCOPE_TEMPLATE_KEYWORD, ITER_SCOPE_TEMPLATE_QUESTION):
            continue
        t = tmpl_by_id.get(s.get("target_id"))
        if t is None:
            continue
        reason = str(s.get("reason") or "").strip()
        if not reason:
            continue
        if scope == ITER_SCOPE_TEMPLATE_KEYWORD:
            keywords = _dedup([str(k).strip() for k in (s.get("keywords") or []) if str(k).strip()])
            if not keywords:
                continue
            new_value = "、".join(_dedup((t["question_keywords"] or []) + keywords))
            item = {
                "scope": scope, "target_id": t["id"], "target_name": t["name_cn"],
                "suggestion": "向 question_keywords 追加候选词: " + "、".join(keywords),
                "reason": reason,
                "old_value": "、".join(t["question_keywords"] or []),
                "new_value": new_value,
            }
        else:
            q = str(s.get("question_template") or "").strip()
            if not q:
                continue
            # 保留占位符校验（铁律 4）：新问句必须含旧问句的全部 {{...}} 占位符
            old_q = t.get("question_template") or ""
            old_ph = set(re.findall(r"\{\{.*?\}\}", old_q))
            new_ph = set(re.findall(r"\{\{.*?\}\}", q))
            if old_ph and not old_ph.issubset(new_ph):
                continue
            item = {
                "scope": scope, "target_id": t["id"], "target_name": t["name_cn"],
                "suggestion": "更新 question_template: " + q[:60] + ("…" if len(q) > 60 else ""),
                "reason": reason,
                "old_value": old_q, "new_value": q,
            }
        out.append(item)
    return out


def _parse_llm_json(raw: str) -> dict | None:
    """解析 LLM 输出 JSON（容错 markdown 代码块标记）；非 dict 返回 None。"""
    t = (raw or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def build_llm_suggestions(db: Session, days: int = 7) -> list[dict]:
    """LLM 辅助建议（#177）：依评估信号+人工意见产可写入建议；失败回退空（调用方走纯规则）。

    走 llm_scheduler L1 门闸（admin 触发，不占 L2 实时问答）；transient 降级由 llm_client
    内部处理；整体异常/非法 → 返回 []（不阻断、不脏写）。
    """
    from app.services import llm_client
    from app.services.llm_scheduler import acquire, release

    templates = build_template_eval(db, days)
    context = _llm_suggest_context(db, days)
    system = _LLM_SYSTEM_PROMPT.format(output_schema=_LLM_OUTPUT_SCHEMA)
    user = (f"【评估证据】\n{context}\n"
            f"\n请按系统提示词要求，只输出 JSON，禁止解释文字。")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    async def _run() -> list[dict]:
        handle = await acquire(level=_LLM_LEVEL, name="prompt_eval_suggest")
        try:
            result = await llm_client.chat_completion(
                messages, temperature=0.2, max_tokens=_LLM_MAX_TOKENS,
                timeout_s=_LLM_TIMEOUT_S, db=db, source=_LLM_SOURCE,
            )
        finally:
            await release(handle)
        obj = _parse_llm_json(str(result))
        if obj is None:
            return []
        return _validate_llm_suggestions(obj, templates)

    try:
        return asyncio.run(_run())
    except asyncio.CancelledError:
        logger.info("prompt_eval_suggest 被更高优先级任务抢占, 回退纯规则")
        return []
    except Exception as e:  # noqa: BLE001  LLM 分析失败不阻断建议链路
        logger.warning("prompt_eval_suggest LLM 分析失败, 回退纯规则: %s", e)
        return []


def _candidate_words(questions: list[str], existing: list | None) -> list[str]:
    """从问句提取候选关键词（朴素启发式：标点切分 → 去停用词 → 去已有词）。"""
    existing_set = set(existing or [])
    cand: list[str] = []
    for q in questions:
        for seg in _SEP_RE.split(q):
            seg = seg.strip()
            if not seg or seg in _STOP_WORDS or seg in existing_set:
                continue
            if 2 <= len(seg) <= 8 and not seg.isdigit():
                cand.append(seg)
    return _dedup(cand)[:_TOP_N]


def _dedup(items: list) -> list:
    seen: set[str] = set()
    out = []
    for i in items:
        if i is None:
            continue
        s = str(i).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _best_template_for_question(templates: list[dict], question: str) -> dict | None:
    """从高频 live 问句反查最可能该归属的模板（按现有关键词命中数）。"""
    q = question.lower()
    best = None
    best_hits = 0
    for t in templates:
        hits = sum(1 for kw in (t["question_keywords"] or []) if str(kw).lower() in q)
        if hits > best_hits:
            best_hits = hits
            best = t
    return best


# ════════════════════════ 半自动执行（prompt_iterations 状态机）════════════════════

def propose_iteration(db: Session, user_id: int, item: dict) -> PromptIteration | None:
    """落一条 proposed 建议（同 target + 同 new_value 的未决行去重）。"""
    if not item.get("suggestion"):
        return None
    if item.get("target_id") and item.get("new_value"):
        dup = db.query(PromptIteration).filter(
            PromptIteration.scope == item["scope"],
            PromptIteration.target_id == item["target_id"],
            PromptIteration.new_value == item["new_value"],
            PromptIteration.status == ITER_STATUS_PROPOSED,
        ).first()
        if dup:
            return None
    row = PromptIteration(
        scope=item["scope"],
        target_id=item.get("target_id"),
        target_name=item.get("target_name") or "",
        suggestion=item["suggestion"],
        reason=item.get("reason"),
        old_value=item.get("old_value"),
        new_value=item.get("new_value"),
        status=ITER_STATUS_PROPOSED,
        created_by=user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def propose_opinion(db: Session, user_id: int, *, content: str,
                    category: str = "other", target_name: str | None = None) -> PromptIteration:
    """记录一条人工意见（#174 评估中心人工输入通道）。

    - 用途：对评估/分析结果的异常、不合理判断，或业务逻辑理解的调整与深化意见；
    - 状态机沿用：proposed → confirmed(采纳) / rejected(不采纳)，但**只记录不自动执行**
      （自由文本不可靠，不自动解析、不改模板/规则），采纳与否由人工后续跟进；
    - 分类（reason 字段承载，前端映射中文）：analysis_abnormal / analysis_unreasonable /
      business_deeper / other。
    """
    content = (content or "").strip()
    if not content:
        raise ValueError("意见内容不能为空")
    if category not in OPINION_CATEGORIES:
        raise ValueError(f"分类仅支持: {'/'.join(OPINION_CATEGORIES)}")
    row = PromptIteration(
        scope=ITER_SCOPE_HUMAN_OPINION,
        target_id=None,
        target_name=target_name or "（全局）",
        suggestion=content,
        reason=category,
        status=ITER_STATUS_PROPOSED,
        created_by=user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def confirm_iteration(db: Session, user_id: int, iteration_id: int, approved: bool) -> dict:
    """人工拍板：approved=True → confirmed（可应用）；False → rejected。"""
    row = db.query(PromptIteration).get(iteration_id)
    if not row:
        raise ValueError(f"迭代建议不存在: {iteration_id}")
    if row.status not in (ITER_STATUS_PROPOSED, ITER_STATUS_CONFIRMED):
        raise ValueError(f"当前状态 {row.status} 不可拍板")
    row.status = ITER_STATUS_CONFIRMED if approved else ITER_STATUS_REJECTED
    row.confirmed_by = user_id
    row.confirmed_at = _now_cn()
    db.commit()
    return _iter_dict(row)


def apply_iteration(db: Session, user_id: int, iteration_id: int) -> dict:
    """服务端应用：写模板/规则，回填 result。只允许 APPLICABLE_SCOPES。"""
    from app.models.governance_prompt_iteration import APPLICABLE_SCOPES

    row = db.query(PromptIteration).get(iteration_id)
    if not row:
        raise ValueError(f"迭代建议不存在: {iteration_id}")
    if row.status != ITER_STATUS_CONFIRMED:
        raise ValueError(f"仅已确认的建议可应用（当前 {row.status}）")
    if row.scope not in APPLICABLE_SCOPES:
        raise ValueError(f"scope={row.scope} 为只记录建议（anchor_guide/new_template/human_opinion），不提供写入路径")

    if row.scope == ITER_SCOPE_TEMPLATE_KEYWORD:
        _apply_template_keywords(db, row)
    elif row.scope == ITER_SCOPE_TEMPLATE_QUESTION:
        _apply_template_question(db, row)
    elif row.scope == ITER_SCOPE_RULE_ADJUST:
        _apply_rule_adjust(db, row)

    row.status = ITER_STATUS_APPLIED
    row.applied_by = user_id
    row.applied_at = _now_cn()
    db.commit()
    return _iter_dict(row)


def _apply_template_keywords(db: Session, row: PromptIteration) -> None:
    """补模板 question_keywords（JSONB 数组追加新值，去重）。

    new_value 为「、」分隔的候选词串（建议侧 _candidate_words 产出），
    应用时按分隔符拆成多个词合并，而非当作单条整体。
    """
    tmpl = db.query(AiReportTemplate).get(row.target_id) if row.target_id else None
    if not tmpl:
        raise ValueError(f"模板不存在: {row.target_id}")
    new_words = _split_values(row.new_value)
    if not new_words:
        raise ValueError("关键词新值为空，拒绝应用")
    merged = _dedup((tmpl.question_keywords or []) + new_words)
    tmpl.question_keywords = merged
    row.result = f"success: keywords {len(merged)} 个"


def _split_values(value: str | None) -> list[str]:
    """按「、」/「,」/空格拆分候选词串（供 apply 合并与 diff 展示）。"""
    if not value:
        return []
    out: list[str] = []
    for part in re.split(r"[、,，\s]+", value):
        part = part.strip()
        if part:
            out.append(part)
    return out


def _apply_template_question(db: Session, row: PromptIteration) -> None:
    """更新模板 question_template（新值需人工确认时已含全文）。"""
    tmpl = db.query(AiReportTemplate).get(row.target_id) if row.target_id else None
    if not tmpl:
        raise ValueError(f"模板不存在: {row.target_id}")
    if not row.new_value:
        raise ValueError("question_template 新值为空，拒绝应用")
    tmpl.question_template = row.new_value
    row.result = "success: question_template 已更新"


def _apply_rule_adjust(db: Session, row: PromptIteration) -> None:
    """调整治理规则：本实现支持停用（new_value='disable'），其余动作人工落地。"""
    rule = db.query(GovernanceRule).get(row.target_id) if row.target_id else None
    if not rule:
        raise ValueError(f"规则不存在: {row.target_id}")
    if row.new_value == "disable":
        rule.enabled = False
        row.result = "success: 规则已停用"
    else:
        raise ValueError(f"不支持的规则调整: {row.new_value!r}（本期仅支持 disable）")


def _iter_dict(row: PromptIteration) -> dict:
    return {
        "id": row.id, "scope": row.scope, "target_id": row.target_id,
        "target_name": row.target_name, "suggestion": row.suggestion,
        "reason": row.reason, "old_value": row.old_value, "new_value": row.new_value,
        "status": row.status, "result": row.result,
        "created_by": row.created_by, "confirmed_by": row.confirmed_by,
        "applied_by": row.applied_by, "confirmed_at": row.confirmed_at,
        "applied_at": row.applied_at, "created_at": row.created_at,
    }


def list_iterations(db: Session, status: str | None = None,
                    scope: str | None = None, limit: int = 100) -> list[dict]:
    """迭代建议列表（评估中心「迭代建议抽屉」用）。"""
    q = db.query(PromptIteration)
    if status:
        q = q.filter(PromptIteration.status == status)
    if scope:
        q = q.filter(PromptIteration.scope == scope)
    rows = q.order_by(PromptIteration.created_at.desc()).limit(limit).all()
    return [_iter_dict(r) for r in rows]

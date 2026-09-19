"""AI 提示词健康报告服务 (工单 #153 反馈闭环: 收集→评审→人工迭代)

#152 单向执行链路(分级→快照→校验→补算)缺反馈回路: 每次问答的命中/缺失/兜底
结果没有回看与迭代机制。本服务实现「评审层」——周期聚合 ai_reports 生成
「提示词健康报告」落 system_events, 供人工确认后迭代(提示词/feed 供给
任何改动均为人工落地, 系统只出建议, 绝不自动改 _SYSTEM_PROMPT)。

数据来源(收集层, 均已由 #116/#121/#147/#153 落库):
- ai_reports 私有问答行(is_public=FALSE, hit_mode∈full/section/live/rule):
  命中率 / live 兜底占比 / 高频未答好问题
- ai_reports 公共快照行(is_public=TRUE, template_id):
  matched_count(被命中复用) / latest_missing(#153 新增, standard 校验缺失清单)
- latest_missing IS NULL 的历史快照: 用 check_dimensions 对 answer_md **重算**
  缺失维度(快照评审兜底, 让报告对新旧数据都可用)

边界(与工单一致):
- 不做用户满意度模型(无点踩数据)
- 历史快照不回补生成, 仅评审时按模板维度重算缺失判断
- 建议只给三层方向(模板/提示词/feed), 迭代动作人工拍板

周期接入: cache_warmer.start_warm_loop 空闲循环每分钟调
check_daily_health_report_idle() —— 当日未生成且白天(8-23)生成一次(每日一次);
进程级单飞锁 _report_lock 防并发(空闲循环 + 手动触发可能撞车)。
"""

import logging
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.ai_report_template import AiReportTemplate
from app.services.system_event_service import record_system_event

logger = logging.getLogger(__name__)

_TZ_CN = timezone(timedelta(hours=8))
# 白天 8-23 才生成(与 cache_warmer.DAYTIME_HOURS 同步, 夜间无人看报告省计算)
_DAYTIME_HOURS = set(range(8, 24))

# 进程级单飞锁(防止空闲循环与手动触发并发重复生成)
_report_lock = threading.Lock()

# 建议阈值
_LIVE_RATIO_WARN = 0.5      # live 兜底占比 ≥50% → 提示词层告警
_REPEAT_LIVE_MIN = 2        # 同问句+同窗口重复 live ≥2 次 → 高频未答好问题
_MISSING_TOP = 8            # 缺失维度 TopN
_QUESTIONS_TOP = 8          # 高频问题 TopN


def _now_cn() -> datetime:
    """当前北京时间(AGENTS.md 铁律: 时区换算)。"""
    return datetime.now(_TZ_CN)


def _entry_label(entry: str | list) -> str:
    """维度条目显示标签: 字符串原样; 同义词数组用 / 连接(与 required_dimensions 风格一致)。"""
    if isinstance(entry, list):
        return " / ".join(str(x) for x in entry)
    return str(entry)


def _count_missing_entries(missing_arrays: list) -> dict[str, int]:
    """统计缺失维度条目出现次数(latest_missing 是条目数组, 条目可为 str 或同义词 list)。"""
    counts: dict[str, int] = {}
    for arr in missing_arrays:
        if not arr:
            continue
        for entry in arr:
            label = _entry_label(entry)
            counts[label] = counts.get(label, 0) + 1
    return counts


def _load_backfill_missing(db: Session, since_ts: str,
                           tmpl_by_id: dict[int, AiReportTemplate]) -> dict[str, int]:
    """快照评审兜底: 窗口内未校验(latest_missing 为 SQL NULL 或 JSON null)且模板带
    required_dimensions 的快照, 用 check_dimensions 对历史 answer_md 重算缺失维度
    (反馈闭环对历史快照也可评审)。

    返回 {维度标签: 出现次数}。bounded: 仅窗口内公共快照; 单条失败跳过不阻塞。
    """
    from app.services.ai_completeness_service import check_dimensions

    counts: dict[str, int] = {}
    rows = db.execute(
        text("""SELECT r.id, r.template_id, r.answer_md
                FROM ai_reports r
                WHERE r.is_public = TRUE
                  AND (r.latest_missing IS NULL
                       OR jsonb_typeof(r.latest_missing) = 'null')
                  AND r.template_id IS NOT NULL AND r.created_at >= :since_ts"""),
        {"since_ts": since_ts},
    ).mappings().all()
    for row in rows:
        tmpl = tmpl_by_id.get(row["template_id"])
        if tmpl is None or not (tmpl.required_dimensions or []):
            continue
        try:
            # check_dimensions 仅需 report.answer_md/.id, 用轻量对象避免整行 load
            fake = SimpleNamespace(id=row["id"], answer_md=row["answer_md"] or "")
            ck = check_dimensions(db, fake, tmpl)
        except Exception as e:  # noqa: BLE001  单条重算失败不影响报告
            logger.warning("ai_feedback 快照重算失败(report_id=%s): %s", row["id"], e)
            continue
        for entry in ck["missing"]:
            label = _entry_label(entry)
            counts[label] = counts.get(label, 0) + 1
    return counts


def build_health_report(db: Session, days: int = 7) -> dict:
    """聚合近 days 天 ai_reports 生成提示词健康报告数据(评审层核心)。

    返回:
      window:        {start, end, days, generated_at}
      overall:       {questions, by_mode, hits, hit_rate, live_ratio}
      templates:     [{id, code, name_cn, period, detail_level, has_dims,
                       snapshots, matched, checked_count, with_missing}, ...]
      missing_top:   [{dim, count}, ...]  缺失维度 TopN(含历史快照重算兜底)
      top_live_qs:   [{question, start, end, count}, ...]  高频未答好问题
      suggestions:   [str, ...]  三层建议(模板/提示词/feed)
    """
    now = _now_cn()
    start = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    since_ts = f"{start}T00:00:00+08:00"  # 北京时间当日起(与 ai_report_service 同口径)

    # ── ① 用户问答命中分布(本人私有实时问答行) ──────────────────────────
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

    # ── ② 按模板统计(窗口内公共快照 + 命中复用 + 缺失校验状态) ──────────
    # ⚠️ matched_count 是快照累计值, 此处统计的是「窗口内创建快照」的复用合计,
    #    用于横向比较模板间被命中热度(口径近似, 报告中注明)。
    tmpl_rows = db.execute(
        text("""SELECT t.id, t.code, t.name_cn, t.period, t.detail_level,
                       (t.required_dimensions IS NOT NULL) AS has_dims,
                       COUNT(r.id) AS snapshots,
                       COALESCE(SUM(r.matched_count), 0) AS matched,
                       COUNT(*) FILTER (
                           WHERE jsonb_typeof(r.latest_missing) = 'array'
                       ) AS checked_count,
                       COUNT(*) FILTER (
                           WHERE jsonb_typeof(r.latest_missing) = 'array'
                             AND jsonb_array_length(r.latest_missing) > 0
                       ) AS with_missing
                FROM ai_report_templates t
                LEFT JOIN ai_reports r
                       ON r.template_id = t.id AND r.is_public = TRUE
                      AND r.created_at >= :since_ts
                GROUP BY t.id, t.code, t.name_cn, t.period, t.detail_level, has_dims
                ORDER BY t.priority, t.id"""),
        {"since_ts": since_ts},
    ).mappings().all()
    templates = [
        {
            "id": int(r["id"]), "code": r["code"], "name_cn": r["name_cn"],
            "period": r["period"], "detail_level": r["detail_level"],
            "has_dims": bool(r["has_dims"]), "snapshots": int(r["snapshots"]),
            "matched": int(r["matched"]), "checked_count": int(r["checked_count"]),
            "with_missing": int(r["with_missing"]),
        }
        for r in tmpl_rows
    ]

    # ── ③ 缺失维度 TopN(已校验 latest_missing + 历史快照重算兜底) ─────────
    lm_rows = db.execute(
        text("""SELECT r.latest_missing FROM ai_reports r
                WHERE r.is_public = TRUE
                  AND jsonb_typeof(r.latest_missing) = 'array'
                  AND r.created_at >= :since_ts"""),
        {"since_ts": since_ts},
    ).scalars().all()
    missing_counts = _count_missing_entries(list(lm_rows))
    # 历史快照评审兜底: 未校验(latest_missing 为 SQL NULL 或 JSON null)的快照按模板维度重算
    tmpl_by_id = {t.id: t for t in db.query(AiReportTemplate).all()}
    backfill = _load_backfill_missing(db, since_ts, tmpl_by_id)
    for dim, c in backfill.items():
        missing_counts[dim] = missing_counts.get(dim, 0) + c
    missing_top = [
        {"dim": dim, "count": c}
        for dim, c in sorted(missing_counts.items(), key=lambda kv: -kv[1])[:_MISSING_TOP]
    ]

    # ── ④ 高频未答好问题(同问句+同窗口重复走 live 实时兜底) ──────────────
    # ⚠️ "start"/"end" 是 PostgreSQL 保留字, 必须 "" 引号化(#116 教训)
    q_rows = db.execute(
        text("""SELECT question, "start", "end", COUNT(*) AS c
                FROM ai_reports
                WHERE is_public = FALSE AND hit_mode = 'live'
                  AND created_at >= :since_ts
                GROUP BY question, "start", "end"
                HAVING COUNT(*) >= :min_repeat
                ORDER BY c DESC, question
                LIMIT :top"""),
        {"since_ts": since_ts, "min_repeat": _REPEAT_LIVE_MIN, "top": _QUESTIONS_TOP},
    ).mappings().all()
    top_live_qs = [
        {
            "question": r["question"],
            "start": str(r["start"]) if r["start"] else None,
            "end": str(r["end"]) if r["end"] else None,
            "count": int(r["c"]),
        }
        for r in q_rows
    ]

    # ── ⑤ 三层建议(只出建议, 迭代动作人工拍板) ───────────────────────────
    suggestions: list[str] = []
    # feed 供给: 缺失维度高频 → 直指 _build_feed 数据块缺口
    for item in missing_top[:3]:
        suggestions.append(
            f"【feed 供给】维度「{item['dim']}」近{days}天缺失 {item['count']} 次, "
            "建议人工在 ai_feed._build_feed 补充对应数据块后确认"
        )
    # 模板层: 快照生成但 0 命中 → 关键词路由失灵
    for t in templates:
        if t["snapshots"] > 0 and t["matched"] == 0:
            suggestions.append(
                f"【模板层】「{t['name_cn']}({t['code']})」近{days}天生成 {t['snapshots']} 份快照 "
                "但 0 次被命中, 建议人工检查 question_keywords 是否贴合用户问法"
            )
    # 提示词层: live 兜底占比偏高 / 高频未答好问题
    if questions and live_ratio >= _LIVE_RATIO_WARN:
        suggestions.append(
            f"【提示词层】实时兜底占比 {live_ratio * 100:.1f}% 偏高, "
            "建议人工检查生成侧锚点约定与摘取侧对齐提示词(_SYSTEM_PROMPT/_EXTRACT_SYSTEM)"
        )
    if top_live_qs:
        first = top_live_qs[0]
        q_preview = first["question"][:20]
        suggestions.append(
            f"【模板/提示词层】高频未答好问题「{q_preview}…」近{days}天重复 "
            f"{first['count']} 次走实时兜底, 建议人工评估新增模板或补充关键词"
        )
    if not suggestions:
        suggestions.append("本期无显著问题: 快照供给与用户问答匹配良好, 维持现状")

    return {
        "window": {"start": start, "end": now.strftime("%Y-%m-%d"), "days": days,
                   "generated_at": now.strftime("%Y-%m-%d %H:%M:%S")},
        "overall": {"questions": questions, "by_mode": by_mode,
                    "hits": hits, "hit_rate": round(hit_rate, 4),
                    "live_ratio": round(live_ratio, 4)},
        "templates": templates,
        "missing_top": missing_top,
        "top_live_qs": top_live_qs,
        "suggestions": suggestions,
    }


def _to_markdown(data: dict) -> str:
    """健康报告 markdown(落 system_events.detail, 事务通知中心可见)。"""
    w = data["window"]
    o = data["overall"]
    bm = o["by_mode"]
    lines = [
        f"## AI 提示词健康报告 ({w['generated_at']})",
        "",
        f"**统计窗口**: {w['start']} ~ {w['end']} (近{w['days']}天)",
        "",
        "### 一、用户问答命中分布",
        f"- 总问答 {o['questions']} 次: 命中全量 {bm['full']} / 命中摘取 {bm['section']} "
        f"/ 实时兜底 {bm['live']} / 规则秒回 {bm['rule']}",
        f"- 命中率 {o['hit_rate'] * 100:.1f}% / 实时兜底占比 {o['live_ratio'] * 100:.1f}%",
        "",
        "### 二、模板快照与命中(窗口内)",
    ]
    if data["templates"]:
        lines.append("| 模板 | 快照数 | 被命中 | 已校验 | 含缺失 |")
        lines.append("|---|---|---|---|---|")
        for t in data["templates"]:
            lines.append(
                f"| {t['name_cn']}({t['period']}/{t['detail_level']}) | {t['snapshots']} "
                f"| {t['matched']} | {t['checked_count']} | {t['with_missing']} |"
            )
    else:
        lines.append("(无模板配置)")
    lines += ["", "### 三、缺失维度 TopN"]
    if data["missing_top"]:
        lines.append("| 维度 | 出现次数 |")
        lines.append("|---|---|")
        for item in data["missing_top"]:
            lines.append(f"| {item['dim']} | {item['count']} |")
    else:
        lines.append("(无缺失维度)")
    lines += ["", "### 四、高频未答好问题(重复实时兜底)"]
    if data["top_live_qs"]:
        for q in data["top_live_qs"]:
            win = f"{q['start']}~{q['end']}" if q["start"] else "默认窗口"
            lines.append(f"- 「{q['question'][:50]}」({win}) ×{q['count']} 次")
    else:
        lines.append("(无)")
    lines += ["", "### 五、迭代建议(人工确认后执行, 系统不自动改)", ""]
    lines += [f"- {s}" for s in data["suggestions"]]
    return "\n".join(lines)


def run_daily_health_report(db: Session | None = None) -> dict | None:
    """生成一次「提示词健康报告」并落 system_events(进程级单飞锁)。

    返回报告数据 dict; 被单飞锁挡住(已有报告在生成)返回 None。
    status: 有缺失维度/高频问题/live占比偏高 → partial+warning, 否则 success+info。
    """
    if not _report_lock.acquire(blocking=False):
        logger.info("ai_feedback 健康报告生成中, 本次跳过")
        return None
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        data = build_health_report(db)
        has_issue = bool(data["missing_top"] or data["top_live_qs"]) or (
            data["overall"]["questions"] > 0
            and data["overall"]["live_ratio"] >= _LIVE_RATIO_WARN
        )
        o = data["overall"]
        summary = (
            f"近{data['window']['days']}天: 问答{o['questions']}次 "
            f"命中率{o['hit_rate'] * 100:.1f}% live兜底{o['live_ratio'] * 100:.1f}% "
            f"缺失维度{len(data['missing_top'])}项 高频问题{len(data['top_live_qs'])}条"
        )
        record_system_event(
            db, category="ai", action="ai_feedback_health_report",
            title=f"AI 提示词健康报告 ({data['window']['generated_at']})",
            summary=summary,
            status="partial" if has_issue else "success",
            level="warning" if has_issue else "info",
            detail=_to_markdown(data),
        )
        logger.info(
            "ai_feedback 健康报告已生成: window=%s~%s questions=%s hit_rate=%.1f%% "
            "missing_top=%s top_live_qs=%s",
            data["window"]["start"], data["window"]["end"], o["questions"],
            o["hit_rate"] * 100, len(data["missing_top"]), len(data["top_live_qs"]),
        )
        return data
    except Exception as e:  # noqa: BLE001  报告生成失败不影响业务
        logger.error("ai_feedback 健康报告生成失败(不影响业务): %s", e, exc_info=True)
        return None
    finally:
        if own_db:
            db.close()
        _report_lock.release()


def _daily_report_exists(db: Session, today: str) -> bool:
    """当日(action=ai_feedback_health_report, 北京时间当日)是否已生成过。"""
    try:
        row = db.execute(
            text("""SELECT 1 FROM system_events
                    WHERE action = 'ai_feedback_health_report'
                      AND (created_at AT TIME ZONE 'Asia/Shanghai')::date = :today
                    LIMIT 1"""),
            {"today": today},
        ).first()
        return row is not None
    except Exception as e:  # noqa: BLE001  查询失败按未生成处理(宁可多生成)
        logger.warning("ai_feedback 当日报告存在性查询失败, 按未生成处理: %s", e)
        return False


def check_daily_health_report_idle() -> None:
    """周期入口(挂 cache_warmer 空闲循环, 每分钟调): 当日未生成且白天 → 生成一次。

    每日一次语义: 以 system_events 当日是否存在报告为准; 夜间(0-7 点)不生成,
    白天首轮空闲检查触发后当日不再重复。失败不影响主循环。
    """
    if _now_cn().hour not in _DAYTIME_HOURS:
        return  # 夜间无人看报告, 省计算
    db = SessionLocal()
    try:
        if _daily_report_exists(db, _now_cn().strftime("%Y-%m-%d")):
            return
        run_daily_health_report(db)
    except Exception as e:  # noqa: BLE001  健康报告检查失败不影响业务
        logger.warning("ai_feedback 健康报告检查异常(不影响业务): %s", e)
    finally:
        db.close()

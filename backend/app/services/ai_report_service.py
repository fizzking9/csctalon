"""AI 报告历史持久化服务 (工单 #116)

设计:
- save_report: AI 任务成功后落库; 不阻塞主响应(异步调用, 失败仅 logger.error)
- list_reports: 仅本人可见; admin 可查全部(user_id=None)
- get_report: 单条详情; 本人或 admin 可见, 否则返回 None(由路由层抛 403)
- summary: KPI(总条数 / 30 天条数 / 累计 tokens) + 7 日柱状(本人或 admin 范围)

SQL 全部走参数化 text() + bind params(AGENTS.md 铁律, 禁止 f-string 拼 SQL)。
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.ai_report import AiReport

logger = logging.getLogger(__name__)


def record_latest_missing(db: Session, report_id: int, missing: list) -> None:
    """#153 记录某报告最近一次 standard 维度校验的缺失清单(反馈闭环收集层)。

    missing=[] 表示校验通过(维度齐全); 空数组与 NULL 的区分:
    - []   → 已校验且齐全(聚合口径: 维度覆盖良好)
    - NULL → 从未被 standard 校验(历史快照, 评审时按模板维度重算兜底)

    ⚠️ 直接提交: 校验发生在命中链 standard 分支, 若缺维度走实时补算路径,
    命中会话(_match_db)最终不 commit —— 不在此提交则 missing 永不落库。
    提交后 ORM 对象被 expire, 调用方后续访问属性会 lazy reload(会话仍开),
    PK(id) 不受 expire 影响, 与 #128 命中 bump 的 commit 行为一致。
    失败仅 log, 不阻塞主流程。
    """
    try:
        import json
        db.execute(
            text("UPDATE ai_reports SET latest_missing = :lm WHERE id = :rid"),
            {"lm": json.dumps(missing or [], ensure_ascii=False), "rid": report_id},
        )
        db.commit()
    except Exception as e:  # noqa: BLE001  落库失败不阻塞主流程
        db.rollback()
        logger.warning("record_latest_missing failed (report_id=%s): %s", report_id, e)


def save_report(
    db: Session,
    *,
    task_id: str | None,
    user_id: int,
    question: str,
    period: str | None,
    date: str | None,
    start: str | None,
    end: str | None,
    window_source: str | None,
    window_label: str | None,
    answer_md: str,
    llm_model: str | None,
    llm_ms: int,
    total_ms: int,
    tokens: dict | None,
    # #121 新增预生成报告相关字段(向后兼容, 默认值让既有实时调用零侵入)
    template_id: int | None = None,
    snapshot_date: str | None = None,
    snapshot_period: str | None = None,
    is_public: bool = False,
    hit_mode: str | None = None,
) -> AiReport | None:
    """落库一条 AI 报告。

    tokens: OpenAI usage dict {prompt_tokens, completion_tokens, total_tokens}; None 表示服务端不返
    返回 AiReport 实例; 失败返回 None(调用方决定是否影响主流程, 异步调用时不影响)。

    #121: template_id/snapshot_date/snapshot_period/is_public 来自预生成 worker;
    实时调用默认 None/None/None/False(私有报告), 零侵入兼容。

    #147: hit_mode 标记命中粒度 full/section/live/rule(见 AiReport 字段注释);
    实时 LLM 兜底传 live, 命中路径按返回形态传 full/section, 规则层传 rule。
    """
    try:
        prompt_t = tokens.get("prompt_tokens") if tokens else None
        completion_t = tokens.get("completion_tokens") if tokens else None
        total_t = tokens.get("total_tokens") if tokens else None
        report = AiReport(
            task_id=task_id,
            user_id=user_id,
            question=question,
            period=period,
            date=date,
            start=start,
            end=end,
            window_source=window_source,
            window_label=window_label,
            answer_md=answer_md,
            llm_model=llm_model,
            llm_ms=llm_ms,
            total_ms=total_ms,
            prompt_tokens=prompt_t,
            completion_tokens=completion_t,
            total_tokens=total_t,
            template_id=template_id,
            snapshot_date=snapshot_date,
            snapshot_period=snapshot_period,
            is_public=is_public,
            hit_mode=hit_mode,
        )
        db.add(report)
        db.commit()
        db.refresh(report)
        logger.info("ai_report saved: id=%s task_id=%s user_id=%s llm_model=%s total_ms=%s "
                    "template_id=%s is_public=%s",
                    report.id, task_id, user_id, llm_model, total_ms, template_id, is_public)
        return report
    except Exception as e:  # noqa: BLE001  落库失败不阻塞主响应
        db.rollback()
        logger.error("ai_report save failed (task_id=%s user_id=%s): %s", task_id, user_id, e)
        return None


def _visibility_clause(current_user_id: int, is_admin: bool) -> str:
    """「我的待办 · AI 报告历史」可见性 SQL where 片段。

    #144 (2026-09-17) 修正: 改为 `user_id = :uid AND is_public = FALSE`
      —— 只看「本人 user_id」+「私有实时问答(is_public=FALSE)」。
      原因: #142 改成 `user_id = :uid` 后, admin 用户 tanzk(id=3)看不到任何
      报告(所有预生成报告都是 user_id=1 触发, is_public=TRUE)。但 tanzk 没问过
      实时问题, 「我的待办」为空是**正确语义**(不是 bug)。预生成报告
      (is_public=TRUE)走「AI 与分析 → AI 报告历史」入口(/admin/ai/reports)。

    分工:
      - /ai/reports(我的待办): 本人 user_id + is_public=FALSE(实时问答)
      - /admin/ai/reports(AI 与分析): is_public=TRUE(预生成报告池, 所有 admin 共享)

    返回带 bind param 的字符串(调用方注入 :uid), 避免拼接 SQL(AGENTS.md 铁律)。
    """
    # is_admin 参数保留兼容现有调用方, 但 #144 后语义统一(本人私有)
    _ = is_admin
    return "user_id = :uid AND is_public = FALSE"


def list_reports(
    db: Session,
    current_user_id: int,
    is_admin: bool,
    *,
    start: str | None = None,
    end: str | None = None,
    period: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """本人历史报告列表(admin 可查全部)。

    时间筛选遵循 AGENTS.md 时区换算规范: 入参为纯日期(YYYY-MM-DD),
    转 Asia/Shanghai 当日起止 TIMESTAMPTZ 与 created_at 比较。
    返回 {items, total, page, page_size}
    """
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 100:
        page_size = 20
    where = _visibility_clause(current_user_id, is_admin)
    binds: dict[str, Any] = {"uid": current_user_id}
    if start:
        where += " AND created_at >= :start_ts"
        binds["start_ts"] = f"{start}T00:00:00+08:00"  # 当日起(北京时间)
    if end:
        where += " AND created_at < :end_ts"
        binds["end_ts"] = f"{end}T23:59:59+08:00"  # 当日末(北京时间)
    if period:
        where += " AND period = :period"
        binds["period"] = period
    # 总条数
    total = db.execute(
        text(f"SELECT COUNT(*) FROM ai_reports WHERE {where}"),  # noqa: S608  where 仅含 bind params
        binds,
    ).scalar() or 0
    # 分页列表
    # ⚠️ date/start/end 是 PostgreSQL 保留字, 必须 "" 引号化(否则 SQL 语法错);
    #    bind params 仍走 :name 形式, 不破坏参数化铁律。
    offset = (page - 1) * page_size
    rows = db.execute(
        text(f"""SELECT id, task_id, user_id, question, period, "date", "start", "end",
                  window_source, window_label, llm_model, llm_ms, total_ms,
                  prompt_tokens, completion_tokens, total_tokens, hit_mode, created_at
               FROM ai_reports
               WHERE {where}
               ORDER BY created_at DESC
               LIMIT :lim OFFSET :off"""),
        {**binds, "lim": page_size, "off": offset},
    ).mappings().all()
    items = [dict(r) for r in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def get_report(
    db: Session, report_id: int, current_user_id: int, is_admin: bool
) -> dict | None:
    """单条详情(本人或 admin, 否则返回 None 由路由层抛 403)。"""
    where = _visibility_clause(current_user_id, is_admin)
    # ⚠️ date/start/end 是 PostgreSQL 保留字, 必须 "" 引号化(见 list_reports 同款注释)
    row = db.execute(
        text(f"""SELECT id, task_id, user_id, question, period, "date", "start", "end",
                  window_source, window_label, answer_md, llm_model, llm_ms, total_ms,
                  prompt_tokens, completion_tokens, total_tokens, hit_mode, created_at
               FROM ai_reports
               WHERE id = :rid AND {where}"""),
        {"rid": report_id, "uid": current_user_id},
    ).mappings().first()
    return dict(row) if row else None


def summary(db: Session, current_user_id: int, is_admin: bool) -> dict:
    """KPI + 7 日柱状(本人范围)。

    返回:
      total: 总条数
      last_30d: 近 30 天条数
      total_tokens_sum: 累计 token(可能为 NULL, 取 COALESCE 0)
      daily_7d: [{date: "YYYY-MM-DD", count: N}, ...] 近 7 天(含今日, 按日升序)
    """
    where = _visibility_clause(current_user_id, is_admin)
    binds: dict[str, Any] = {"uid": current_user_id}
    # KPI 单行聚合
    kpi = db.execute(
        text(f"""SELECT
                  COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days') AS last_30d,
                  COALESCE(SUM(total_tokens), 0) AS total_tokens_sum
                FROM ai_reports WHERE {where}"""),
        binds,
    ).mappings().first()
    # 7 日柱状(按北京时间聚合, 时区换算遵守 AGENTS.md 铁律)
    rows = db.execute(
        text(f"""SELECT
                  (created_at AT TIME ZONE 'Asia/Shanghai')::date AS d,
                  COUNT(*) AS c
                FROM ai_reports
                WHERE {where}
                  AND created_at >= NOW() - INTERVAL '7 days'
                GROUP BY d
                ORDER BY d"""),
        binds,
    ).mappings().all()
    # 补全 7 天(含今日, 缺日填 0)
    today = datetime.now().strftime("%Y-%m-%d")
    daily_map = {str(r["d"]): int(r["c"]) for r in rows}
    daily_7d = []
    for i in range(6, -1, -1):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        daily_7d.append({"date": day, "count": daily_map.get(day, 0)})
    return {
        "total": int(kpi["total"] or 0) if kpi else 0,
        "last_30d": int(kpi["last_30d"] or 0) if kpi else 0,
        "total_tokens_sum": int(kpi["total_tokens_sum"] or 0) if kpi else 0,
        "daily_7d": daily_7d,
        "today": today,
    }

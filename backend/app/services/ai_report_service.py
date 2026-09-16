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
) -> AiReport | None:
    """落库一条 AI 报告。

    tokens: OpenAI usage dict {prompt_tokens, completion_tokens, total_tokens}; None 表示服务端不返
    返回 AiReport 实例; 失败返回 None(调用方决定是否影响主流程, 异步调用时不影响)。

    #121: template_id/snapshot_date/snapshot_period/is_public 来自预生成 worker;
    实时调用默认 None/None/None/False(私有报告), 零侵入兼容。
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
    """本人可见 + admin 全可见 + 公共报告所有人可见的 SQL where 片段。

    #122: is_public=True 的报告(预生成)绕过 user_id 隔离, 所有人可见。
    返回带 bind param 的字符串(调用方注入 :uid), 避免拼接 SQL(AGENTS.md 铁律)。
    """
    if is_admin:
        return "1=1"  # admin 无 user_id 过滤, 但分页/排序仍走 user_id+created_at 索引
    # 普通用户: 自己的私有报告 OR 任何公共报告
    return "(user_id = :uid OR is_public = TRUE)"


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
                  prompt_tokens, completion_tokens, total_tokens, created_at
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
                  prompt_tokens, completion_tokens, total_tokens, created_at
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

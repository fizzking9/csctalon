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

logger = logging.getLogger(__name__)

# 30 分钟颗粒度容差(用户 12:25 请求, 允许返回 12:00 同步点的报告)
# 实现: snapshot_date 是 date 类型(无时间), 当日生成的报告都视为"12:00 同步点"
# 所以同日匹配即可, 不需要更细粒度容差

# 时区(AGENTS.md 铁律: 时区换算走 Asia/Shanghai)
_TZ_CN = _dt.timezone(_dt.timedelta(hours=8))


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


def match_template_report(
    db: Session,
    question: str,
    win_period: str | None,
    win_date: str | None,
) -> tuple[AiReport | None, AiReportTemplate | None]:
    """匹配预生成的公共报告。

    匹配优先级:
    1. 强匹配: 问句命中某模板 keywords AND snapshot_period == win_period AND snapshot_date == win_date
       (问句与窗口都对应到同一模板的当日预生成报告)
    2. 语义匹配: 问句命中某模板 keywords AND snapshot_date 当日(允许跨 period)
       (问句对得上, 窗口稍偏仍可复用)
    3. 时点容差: 当日同 period 最新公共报告(无关键词命中也返回)
       (兜底, 让用户至少拿到一份当日报告)

    返回 (report, template) 或 (None, None); 调用方走 LLM 兜底。
    命中时 matched_count +1。
    """
    today = _today_cn()

    # 1+2 合并: 取当日 is_public=True 报告 + 其模板 keywords 做语义匹配
    # 先优先窗口完全(period 匹配), 再降级语义匹配(任意 period 当日)
    if win_date:
        # 优先 period 匹配的当日报告
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
    sorted_rows = []
    for r in rows:
        tmpl = _get_template(db, r.get("template_id"))
        hits = _count_keyword_hits(question, tmpl.question_keywords if tmpl else None)
        period_match = (r.get("snapshot_period") == win_period) if win_period else False
        sorted_rows.append((r, tmpl, hits, period_match))
    # 排序: 命中数 DESC → period 匹配 DESC → created_at DESC
    sorted_rows.sort(
        key=lambda x: (-x[2], 0 if x[3] else 1, -(x[0].get("created_at") or _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)).timestamp()),
    )

    # 取第一条 keyword 命中数 > 0 的返回
    for r, tmpl, hits, period_match in sorted_rows:
        if hits > 0 and tmpl:
            report = _wrap_row(db, dict(r))
            # #128: _wrap_row 内 _bump_matched_count 调 db.commit() → expire_on_commit
            #       把 tmpl 属性标 expired; 脱离 session 前 expunge 才能让调用方
            #       在 _match_db.close() 后安全读 tmpl.code(否则 DetachedInstanceError 500)。
            _code = tmpl.code  # 显式读一次, 确保属性已加载进实例
            db.expunge(tmpl)
            logger.info("match_template_report: 命中 report_id=%s template=%s "
                        "keyword_hits=%d period_match=%s",
                        r["id"], _code, hits, period_match)
            return report, tmpl

    # 未命中: 返回 None, 调用方走 LLM 兜底
    # (不兜底时点容差, 避免"上月对比"等无关问句命中当日 daily 报告)
    return None, None


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
        # 命中后 matched_count +1
        _bump_matched_count(db, r.id)
        return r
    return row


def list_candidates(db: Session, win_period: str | None,
                    win_date: str | None) -> list[AiReport]:
    """#135 L2 语义匹配用: 取当日 is_public=True 报告列表(不 bump matched_count)。

    复用 match_template_report 的 SQL 与排序((snapshot_period=win_period) DESC,
    created_at DESC), 但不调 _bump_matched_count(L2 命中后由调用方决定是否 bump)。
    返回 list[AiReport](轻量 ORM 包装, 字段与 match_template_report 一致)。
    """
    today = _today_cn()
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
        a = AiReport()
        for k, v in dict(r).items():
            try:
                setattr(a, k, v)
            except Exception:  # noqa: BLE001  跳过 ORM 不接受的字段
                pass
        out.append(a)
    return out


def _get_template(db: Session, template_id: int | None) -> AiReportTemplate | None:
    """查询模板(命中匹配时返回, 用于调用方知道是哪个模板生成的)。"""
    if not template_id:
        return None
    return db.query(AiReportTemplate).get(template_id)


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

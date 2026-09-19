"""AI 报告补算器 (工单 #152)

设计(A-20260919-001 §六): 补算策略 = **同窗口完整快照优先**(用户拍板) →
无完整快照则由调用方(ai.py)走实时 27b 路径。

- find_complete_snapshot(db, win, template): 按 matcher 三态窗口定义(#151)查同窗口
  is_public=True 快照, 对每份调 check_dimensions(维度清单用模板配置), 返回第一份
  complete 的 (report, template); 无 → (None, None)。
- recompute(db, question, win, template): 找到完整快照 → extract_section 秒回
  (source=snapshot_reuse); 找不到 → 返回 {source: "needs_live", ...}, 由 ai.py
  走 _runner 实时路径(带 required_dimensions 注入)。

v1 实现说明(与架构文档 §6.2 的差异, 属实现细节不改变框架语义):
  §6.2 的 live_llm 分支由 ai.py 的 _runner 承担 —— 实时 27b 生成统一复用任务管理器
  (先回 task_id 再轮询的前端体验), 避免在 /ai/analyze 内嵌 30-50s 阻塞调用超前端
  60s 全局 axios timeout。补算器只做"同窗口完整快照优先"的同步部分。
"""

import datetime as _dt
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.ai_report import AiReport
from app.models.ai_report_template import AiReportTemplate
from app.services.ai_completeness_service import check_dimensions
from app.services.ai_extract_service import extract_section

logger = logging.getLogger(__name__)

# 时区(AGENTS.md 铁律: 时区换算走 Asia/Shanghai)
_TZ_CN = _dt.timezone(_dt.timedelta(hours=8))


def _today_cn() -> _dt.date:
    """当前北京时间日期。"""
    return _dt.datetime.now(_TZ_CN).date()


def _row_to_report(row: dict) -> AiReport:
    """SQL row → 轻量 AiReport ORM 实例(同 matcher._wrap_row, 不 bump matched_count)。"""
    r = AiReport()
    for k, v in row.items():
        try:
            setattr(r, k, v)
        except Exception:  # noqa: BLE001  跳过 ORM 不接受的字段
            pass
    return r


def _same_window_rows(db: Session, win: dict) -> list[dict]:
    """按 #151 三态窗口定义查同窗口 is_public=True 快照(created_at DESC)。"""
    win_start = win.get("start")
    win_end = win.get("end")
    win_date = win.get("date")
    today = _today_cn()
    if win_start and win_end and win_start != win_end:
        return db.execute(
            text("""SELECT r.id, r.task_id, r.user_id, r.template_id, r.question, r.period,
                      r.snapshot_date, r.snapshot_period, r.is_public,
                      r.answer_md, r.llm_model, r.llm_ms, r.total_ms,
                      r.prompt_tokens, r.completion_tokens, r.total_tokens,
                      r.window_source, r.window_label, r.created_at
               FROM ai_reports r
               WHERE r.is_public = TRUE
                 AND r.start IS NOT NULL AND r.end IS NOT NULL
                 AND r.start <= :q_end AND r.end >= :q_start
               ORDER BY (r.snapshot_period = :period) DESC, r.created_at DESC"""),
            {"q_start": win_start, "q_end": win_end, "period": win.get("period") or ""},
        ).mappings().all()
    sdate = win_date or today
    return db.execute(
        text("""SELECT r.id, r.task_id, r.user_id, r.template_id, r.question, r.period,
                  r.snapshot_date, r.snapshot_period, r.is_public,
                  r.answer_md, r.llm_model, r.llm_ms, r.total_ms,
                  r.prompt_tokens, r.completion_tokens, r.total_tokens,
                  r.window_source, r.window_label, r.created_at
           FROM ai_reports r
           WHERE r.is_public = TRUE
             AND r.snapshot_date = :sdate
           ORDER BY (r.snapshot_period = :period) DESC, r.created_at DESC"""),
        {"sdate": sdate, "period": win.get("period") or ""},
    ).mappings().all()


def _get_template(db: Session, template_id: int | None) -> AiReportTemplate | None:
    if not template_id:
        return None
    return db.query(AiReportTemplate).get(template_id)


def find_complete_snapshot(db: Session, win: dict,
                           template: AiReportTemplate | None) -> tuple[AiReport | None, AiReportTemplate | None]:
    """找同窗口第一份维度完整的公共快照(#152 拍板: 同窗口快照优先)。

    窗口定义与 matcher 三态一致(#151): 区间按报告内容窗口[start,end]重叠 /
    单日按 snapshot_date / 无窗口取当日。按 created_at DESC 遍历, 用模板配置的
    required_dimensions 做完整性校验(模板 None → 直接返回第一份, 不校验)。

    返回 (report, template_for_extract) 或 (None, None)。
    """
    try:
        rows = _same_window_rows(db, win)
    except Exception as e:  # noqa: BLE001  查库失败不阻塞主流程
        logger.warning("find_complete_snapshot 查库失败: %s", e)
        return None, None
    for row in rows:
        report = _row_to_report(dict(row))
        tmpl = _get_template(db, report.template_id) or template
        if tmpl is None:
            # 无模板配置维度 → 不做完整性校验, 直接复用最新快照
            logger.info("find_complete_snapshot: report_id=%s 无模板维度, 直接复用", report.id)
            return report, None
        ck = check_dimensions(db, report, tmpl)
        if ck["complete"]:
            logger.info("find_complete_snapshot: 命中完整快照 report_id=%s template=%s",
                        report.id, tmpl.code)
            return report, tmpl
        logger.info("find_complete_snapshot: report_id=%s 维度缺失(%s), 继续找",
                    report.id, ck["missing"])
    return None, None


async def recompute(db: Session, question: str, win: dict,
                    template: AiReportTemplate | None) -> dict:
    """补算: 同窗口完整快照优先 → 无则交回调用方走实时 27b。

    参数:
      db: Session
      question: 用户问句(extract_section 摘取用)
      win: {"period", "date", "start", "end", "window_label", "window_source"}
      template: 命中的模板(required_dimensions 来源)

    返回:
      {
        "result_md": str,
        "source": "snapshot_reuse" | "needs_live",
        "confidence": float,
        "report_id": int | None,
      }
    """
    report, tmpl = find_complete_snapshot(db, win, template)
    if report is not None and report.answer_md:
        ext = await extract_section(db, question, report, tmpl)
        if ext.get("result_md"):
            return {"result_md": ext["result_md"], "source": "snapshot_reuse",
                    "confidence": ext.get("confidence", 0.7), "report_id": report.id}
    # 无完整快照: 由 ai.py 走 _runner 实时路径(带 required_dimensions 注入)
    logger.info("recompute: 无同窗口完整快照 → needs_live (win=%s)", win)
    return {"result_md": "", "source": "needs_live", "confidence": 0.0, "report_id": None}

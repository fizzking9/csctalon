"""AI 报告评价服务 (采集 + 周期汇总, #174 演进)

- **采集**：用户对报告/对话结果打分(1~5)+可选意见，落 ai_report_feedbacks。
  只采集，不做实时逐条分析（设计 §1.4 非目标）。
- **周期汇总**：prompt_eval.build_summary 调用 _feedback_aggregate 按窗口
  汇总 rating 分布 + 低分原因共性（供迭代建议归因）。
"""

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.ai_report_feedback import AiReportFeedback

logger = logging.getLogger(__name__)


RATING_MIN = 1
RATING_MAX = 5
_LOW_RATING_MAX = 2          # ≤2 视为"低分/未解决"，进入归因
_TOP = 5                     # 低分报告 / 意见 TopN


def create_feedback(db: Session, report_id: int, user_id: int,
                    rating: int, opinion: str | None = None) -> dict:
    """采集一条评价（done：不实时分析）。rating 校验 1~5，越界 400。"""
    if not (RATING_MIN <= rating <= RATING_MAX):
        raise ValueError(f"rating 需在 {RATING_MIN}~{RATING_MAX} 之间")
    row = AiReportFeedback(
        report_id=report_id,
        user_id=user_id,
        rating=rating,
        opinion=(opinion or "").strip() or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _feedback_dict(row)


def _feedback_dict(r: AiReportFeedback) -> dict:
    return {
        "id": r.id, "report_id": r.report_id, "user_id": r.user_id,
        "rating": r.rating, "opinion": r.opinion,
        "created_at": r.created_at,
    }


def list_feedbacks(db: Session, report_id: int) -> list[dict]:
    """某报告的全部评价（详情页展示用，时间正序）。"""
    rows = db.query(AiReportFeedback) \
        .filter(AiReportFeedback.report_id == report_id) \
        .order_by(AiReportFeedback.created_at.asc()).all()
    return [_feedback_dict(r) for r in rows]


def has_feedback_for(db: Session, report_id: int, user_id: int) -> bool:
    """当前用户是否已评过该报告（前端用于切换"已评价"态）。"""
    return db.query(AiReportFeedback.id).filter(
        AiReportFeedback.report_id == report_id,
        AiReportFeedback.user_id == user_id,
    ).first() is not None


# ═══════════════════ 周期汇总（评估中心调用, 非实时） ═══════════════════

def _feedback_aggregate(db: Session, since_ts: str) -> dict:
    """窗口内评价汇总（供 build_summary 并入总览）。

    返回:
      count:      评价条数
      avg:        平均分
      low_count:  低分(≤2)条数
      low_ratio:  低分占比
      low_reports:[{id, rating, question, opinion}] 低分报告 TopN（未解决/不准原因）
      top_opinions:[str, ...] 高频意见关键词（朴素切分, 供人工确认, 非自动改）
    """
    rows = db.execute(
        text("""SELECT f.rating, f.opinion, f.report_id, r.question
                FROM ai_report_feedbacks f
                LEFT JOIN ai_reports r ON r.id = f.report_id
                WHERE f.created_at >= :since_ts"""),
        {"since_ts": since_ts},
    ).mappings().all()
    if not rows:
        return {"count": 0, "avg": None, "low_count": 0, "low_ratio": None,
                "low_reports": [], "top_opinions": []}
    ratings = [int(r["rating"]) for r in rows]
    avg = sum(ratings) / len(ratings)
    low = [r for r in rows if int(r["rating"]) <= _LOW_RATING_MAX]
    low_sorted = sorted(low, key=lambda r: int(r["rating"]))[:_TOP]
    low_reports = [
        {
            "id": int(r["report_id"]), "rating": int(r["rating"]),
            "question": (r["question"] or "")[:60],
            "opinion": (r["opinion"] or "")[:120],
        }
        for r in low_sorted
    ]
    # 朴素高频意见：切词统计（停用词过滤），仅供人工确认参考
    opinions = [r["opinion"] for r in rows if r["opinion"]]
    top_opinions = _top_opinion_keywords(opinions)
    return {
        "count": len(ratings), "avg": round(avg, 2),
        "low_count": len(low), "low_ratio": round(len(low) / len(ratings), 4),
        "low_reports": low_reports, "top_opinions": top_opinions,
    }


_STOP = frozenset({
    "的", "了", "是", "和", "与", "都", "也", "很", "不", "但", "这", "那",
    "报告", "希望", "建议", "觉得", "有点", "问题", "没有", "一个", "一些",
    "啊", "吗", "呢", "看", "图", "吗", "今日", "今天", "应该", "好像",
})
_SEP = "[\\s，。！？、；：,.!?;:()（）/\\|—\\-]+"


def _top_opinion_keywords(opinions: list[str], top: int = _TOP) -> list[str]:
    """朴素切词统计高频词（不追求分词质量，仅供人工确认参考）。"""
    import re

    counts: dict[str, int] = {}
    for _op in opinions:
        for w in re.split(_SEP, _op):
            w = w.strip()
            if not w or len(w) < 2 or w in _STOP:
                continue
            counts[w] = counts.get(w, 0) + 1
    return [w for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:top]]

# -*- coding: utf-8 -*-
"""AI 报告评价服务测试 (#174 演进: 采集→周期汇总)。

覆盖:
- create_feedback: rating 1~5 合法落库 / 越界抛 ValueError
- _feedback_aggregate: 窗口汇总 avg/low_ratio/low_reports/top_opinions
- _top_opinion_keywords: 朴素高频词（停用词过滤）
"""

import pytest
from sqlalchemy import text

from app.services.ai_report_feedback_service import (
    _feedback_aggregate,
    _top_opinion_keywords,
    create_feedback,
    list_feedbacks,
)


def _clean(db):
    # 不能先 rollback：create_feedback 已 commit，这里直接 DELETE 并 commit 生效
    db.execute(text("DELETE FROM ai_report_feedbacks"))
    # 残留的评价可能关联 probe 报告, 一并清理本模块造的 report
    db.execute(text("DELETE FROM ai_reports WHERE question LIKE '[fb-test]%'"))
    db.commit()


@pytest.fixture(autouse=True)
def _cleanup(db):
    yield
    _clean(db)


def _mk_probe_report(db) -> int:
    """造一条评价关联的报告, 返回 id。"""
    cur = db.execute(text(
        "INSERT INTO ai_reports (user_id, question, answer_md, llm_ms, total_ms, is_public) "
        "VALUES (1, '[fb-test] 评价目标报告', 'ok', 100, 200, false) RETURNING id"
    ))
    rid = cur.fetchone()[0]
    db.commit()
    return rid


def test_create_feedback_invalid_rating(db):
    with pytest.raises(ValueError):
        create_feedback(db, 1, 1, rating=0, opinion="越界")
    with pytest.raises(ValueError):
        create_feedback(db, 1, 1, rating=6, opinion="越界")


def test_create_feedback_and_aggregate(db):
    rid = _mk_probe_report(db)
    # 测试库仅 user 1 存在（与其它模块 conftest 同源），同一用户可多次评(无唯一约束)
    create_feedback(db, rid, 1, rating=5, opinion="很准确数据透明明了 (严谨)")
    create_feedback(db, rid, 1, rating=2, opinion="缺少退款明细 (不准)")

    since = "2000-01-01T00:00:00+08:00"
    agg = _feedback_aggregate(db, since)
    assert agg["count"] == 2
    assert agg["avg"] == pytest.approx(3.5)
    assert agg["low_count"] == 1
    assert agg["low_ratio"] == pytest.approx(0.5)
    # 低分报告归因含问题问句
    assert len(agg["low_reports"]) == 1
    assert agg["low_reports"][0]["rating"] == 2
    assert "评价目标报告" in agg["low_reports"][0]["question"]
    # 高频意见词（朴素切词按分隔符整段保留；含"退款/明细"子串即命中）
    assert any(("退款" in w or "明细" in w) for w in agg["top_opinions"])

    assert len(list_feedbacks(db, rid)) == 2


def test_aggregate_empty(db):
    since = "2000-01-01T00:00:00+08:00"
    agg = _feedback_aggregate(db, since)
    assert agg["count"] == 0
    assert agg["avg"] is None
    assert agg["low_reports"] == []


def test_top_opinion_keywords_filters_stopwords():
    out = _top_opinion_keywords(
        ["退款明细缺失，希望补充", "退款口径与台账不一致", "的了的报告"],
        top=10,
    )
    # 朴素切词按分隔符整段保留；"退款"作为子串出现在结果片段中
    assert out and any("退款" in w for w in out)
    # 停用词整词('的'/'了'/'报告')不作为独立词项出现
    assert "的" not in out and "了" not in out and "报告" not in out

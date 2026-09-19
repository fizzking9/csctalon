# -*- coding: utf-8 -*-
"""AI 提示词健康报告服务测试 (工单 #153 反馈闭环: 收集→评审→人工迭代)。

覆盖:
- record_latest_missing: 落库 latest_missing(空数组=齐全; 数组=缺失清单)
- build_health_report:
  * 总体命中分布(hit_rate / live_ratio)
  * 按模板统计(snapshots / matched / with_missing)
  * 缺失维度 TopN(含历史快照重算兜底)
  * 高频未答好问题(同问句+同窗口重复 live)
  * 三层建议(feed 供给 / 模板层 / 提示词层)
- _entry_label / _count_missing_entries(同义词数组维度条目)
- _to_markdown 报告结构
"""

import pytest
from sqlalchemy import text

from app.models.ai_report import AiReport
from app.models.ai_report_template import AiReportTemplate
from app.services.ai_feedback_service import (
    _count_missing_entries,
    _entry_label,
    _to_markdown,
    build_health_report,
)
from app.services.ai_report_service import record_latest_missing


def _add_report(db, *, user_id=1, question="q", answer_md="md", hit_mode=None,
                is_public=False, template_id=None, matched_count=0,
                latest_missing=None, start=None, end=None) -> AiReport:
    """插入一条 ai_reports(测试会话内, 由 fixture 回滚清理)。"""
    r = AiReport(
        task_id=None, user_id=user_id, question=question,
        period="daily", date=None, start=start, end=end,
        window_source="period", window_label="今日(默认窗口)",
        answer_md=answer_md, llm_model="test-model", llm_ms=1, total_ms=1,
        is_public=is_public, template_id=template_id,
        matched_count=matched_count, hit_mode=hit_mode, latest_missing=latest_missing,
    )
    db.add(r)
    return r


def _add_tmpl(db, *, code, name_cn="测试模板", required_dimensions=None) -> AiReportTemplate:
    t = AiReportTemplate(
        code=code, name_cn=name_cn, question_template="今日数据如何?",
        period="daily", frequency="daily", detail_level="standard",
        required_dimensions=required_dimensions,
    )
    db.add(t)
    return t


# ── record_latest_missing 落库 ─────────────────────────────
def test_record_latest_missing(_engine):
    """缺失清单落库; 空数组=校验通过(齐全)。需独立会话(该函数直接 commit)。"""
    from sqlalchemy.orm import Session

    s = Session(_engine)
    rid = None
    try:
        r = AiReport(user_id=1, question="record_latest_missing 测试",
                     answer_md="md", llm_ms=1, total_ms=1)
        s.add(r)
        s.commit()
        rid = r.id
        record_latest_missing(s, rid, ["四路", "涉诉"])
        got = s.execute(
            text("SELECT latest_missing FROM ai_reports WHERE id = :id"), {"id": rid}
        ).scalar()
        assert got == ["四路", "涉诉"]
        record_latest_missing(s, rid, [])
        got2 = s.execute(
            text("SELECT latest_missing FROM ai_reports WHERE id = :id"), {"id": rid}
        ).scalar()
        assert got2 == []
    finally:
        if rid:
            s.execute(text("DELETE FROM ai_reports WHERE id = :id"), {"id": rid})
            s.commit()
        s.close()


# ── build_health_report 总览/模板/缺失维度/高频问题 ──────────
def test_health_report_overall_and_templates(db):
    # ⚠️ 先取基线: 其它测试文件(如 test_ai_assistant)会残留已提交的私有问答行,
    #    健康报告聚合全量窗口数据, 断言用「基线 + 本次新增」差值而非绝对数
    baseline = build_health_report(db, days=7)["overall"]
    tmpl = _add_tmpl(db, code="fb_overall", required_dimensions=["四路", "涉诉"])
    db.flush()  # 先落模板拿到 id, 供后续报告引用 template_id
    # 3 条私有问答: 1 命中全量 + 2 次同问句 live(高频未答好问题)
    _add_report(db, question="退款如何", answer_md="**四路**：A 1 笔", hit_mode="full")
    _add_report(db, question="今天投诉", answer_md="md-1", hit_mode="live")
    _add_report(db, question="今天投诉", answer_md="md-2", hit_mode="live")
    # 1 条公共快照: 校验缺失 → latest_missing=["四路"], 被命中 3 次
    _add_report(db, question="快照", answer_md="**四路**：暂无",
                is_public=True, template_id=tmpl.id, matched_count=3,
                latest_missing=["四路"])
    db.flush()

    out = build_health_report(db, days=7)
    o = out["overall"]
    b = baseline
    assert o["questions"] == b["questions"] + 3
    assert o["by_mode"]["full"] == b["by_mode"]["full"] + 1
    assert o["by_mode"]["live"] == b["by_mode"]["live"] + 2
    q_total = o["questions"]
    assert o["hit_rate"] == pytest.approx(round((b["hits"] + 1) / q_total, 4))
    assert o["live_ratio"] == pytest.approx(round((b["by_mode"]["live"] + 2) / q_total, 4))
    # 模板统计
    t = next(x for x in out["templates"] if x["code"] == "fb_overall")
    assert t["snapshots"] == 1 and t["matched"] == 3
    assert t["checked_count"] == 1 and t["with_missing"] == 1
    # 缺失维度 TopN
    dims = {x["dim"]: x["count"] for x in out["missing_top"]}
    assert dims.get("四路") == 1
    # 高频未答好问题(同问句+同窗口)
    assert any(x["question"] == "今天投诉" and x["count"] == 2
               for x in out["top_live_qs"])
    # 三层建议: feed(缺失维度) + 提示词(live 占比高) 均出现
    assert any("feed 供给" in s for s in out["suggestions"])
    assert any("提示词层" in s for s in out["suggestions"])


# ── 历史快照评审兜底(latest_missing IS NULL → 重算) ─────────
def test_backfill_recompute_for_null_latest_missing(db):
    tmpl = _add_tmpl(db, code="fb_backfill", required_dimensions=["四路", "涉诉"])
    db.flush()  # 先落模板拿到 id, 供后续报告引用 template_id
    # 快照 latest_missing IS NULL, answer_md 缺「四路」(写"暂无")但「涉诉」有数据
    _add_report(db, question="快照", answer_md="**四路**：暂无\n**涉诉**：2 笔",
                is_public=True, template_id=tmpl.id)
    db.flush()

    out = build_health_report(db, days=7)
    dims = {x["dim"]: x["count"] for x in out["missing_top"]}
    assert dims.get("四路") == 1   # 历史快照重算兜底生效
    assert "涉诉" not in dims      # 已覆盖的维度不计入缺失


# ── 维度条目规范化(同义词数组) ─────────────────────────────
def test_entry_label_synonym_list():
    assert _entry_label("四路") == "四路"
    assert _entry_label(["环比", "同比"]) == "环比 / 同比"


def test_count_missing_entries():
    # 同义词条目在 latest_missing 中是嵌套数组(单条目), 归一化为 "环比 / 同比"
    counts = _count_missing_entries([["四路", "涉诉"], ["四路"], [["环比", "同比"]]])
    assert counts == {"四路": 2, "涉诉": 1, "环比 / 同比": 1}


# ── markdown 报告结构 ─────────────────────────────────────
def test_to_markdown_contains_sections():
    data = {
        "window": {"start": "2026-09-13", "end": "2026-09-19", "days": 7,
                   "generated_at": "2026-09-19 10:00:00"},
        "overall": {"questions": 3, "by_mode": {"full": 1, "section": 0,
                                                "live": 2, "rule": 0},
                    "hits": 1, "hit_rate": 1 / 3, "live_ratio": 2 / 3},
        "templates": [],
        "missing_top": [{"dim": "四路", "count": 2}],
        "top_live_qs": [{"question": "今天投诉", "start": None, "end": None, "count": 2}],
        "suggestions": ["【feed 供给】维度「四路」近7天缺失 2 次, 建议人工在 ai_feed._build_feed 补充"],
    }
    md = _to_markdown(data)
    assert "## AI 提示词健康报告" in md
    assert "### 三、缺失维度 TopN" in md
    assert "### 五、迭代建议" in md
    assert "四路" in md and "今天投诉" in md

# -*- coding: utf-8 -*-
"""AI 提示词评估中心服务测试 (工单 #173)。

覆盖:
- build_summary: 命中分布 / 命中率 / live 占比（基线差值断言防残留污染）
- build_template_eval: 模板命中率 + 0 命中识别
- build_anchor_check: 生成侧↔摘取侧静态比对 + 构造报告 H2 漂移检测
- build_suggestions: 0命中→补关键词 / fp_ratio>0.5→建议停用 / 高频兜底→建议
- prompt_iterations 状态机: propose→confirm→apply / reject；apply 后模板 keywords 变更
"""

import pytest
from sqlalchemy import text

from app.models.ai_report import AiReport
from app.models.ai_report_template import AiReportTemplate
from app.models.governance_rule import GovernanceRule
from app.services.prompt_eval_service import (
    _generation_anchors,
    apply_iteration,
    build_anchor_check,
    build_governance_eval,
    build_suggestions,
    build_summary,
    build_template_eval,
    confirm_iteration,
    propose_iteration,
)


def _clean_iterations(db):
    """清掉上一轮运行残留的已提交行（propose/confirm/apply 会 commit，绕开事务回滚）。

    prompt_iterations 是本测试文件引入的新表，只可能被本文件污染；it_keywords 模板同理。
    """
    db.execute(text("DELETE FROM prompt_iterations"))
    db.execute(text("DELETE FROM ai_report_templates WHERE code = 'it_keywords'"))
    db.commit()


def _add_tmpl(db, *, code, name_cn="测试模板", keywords=None) -> AiReportTemplate:
    t = AiReportTemplate(
        code=code, name_cn=name_cn, question_template="今日数据如何?",
        question_keywords=keywords or ["今日", "整体"],
        period="daily", frequency="daily", detail_level="standard",
    )
    db.add(t)
    return t


def _add_report(db, *, question="q", hit_mode=None, is_public=False,
                template_id=None, matched_count=0, period="daily",
                answer_md="## 接待分析\n接待 10 人次\n## 退款分析\n退款 5 笔\n") -> AiReport:
    r = AiReport(
        task_id=None, user_id=1, question=question,
        period=period, date=None, start=None, end=None,
        window_source="period", window_label="今日(默认窗口)",
        answer_md=answer_md, llm_model="test-model", llm_ms=1, total_ms=1,
        is_public=is_public, template_id=template_id,
        matched_count=matched_count, hit_mode=hit_mode, latest_missing=None,
    )
    db.add(r)
    return r


def _add_rule(db, *, effect_executed=0, effect_rejected=0, effect_fp=0) -> GovernanceRule:
    r = GovernanceRule(
        detector="unique_index_dup", table_name="t", target_pattern="uq_t_x",
        verdict="real_issue", reason="测试规则", disposition="report_only",
    )
    r.effect_executed = effect_executed
    r.effect_rejected = effect_rejected
    r.effect_fp = effect_fp
    db.add(r)
    return r


# ── build_summary 总览 ────────────────────────────────────
def test_summary_hit_distribution(db):
    # 基线差值断言：其它测试文件(如 test_ai_assistant)会残留已提交的私有问答行,
    #    总览聚合全量窗口数据，用「基线 + 本次新增」差值而非绝对数（同 test_ai_feedback_service）
    baseline = build_summary(db, days=7)["overall"]
    _add_report(db, question="今天怎么样", hit_mode="full")
    _add_report(db, question="今天怎么样", hit_mode="full")
    _add_report(db, question="明天怎么样", hit_mode="live")
    db.flush()

    ov = build_summary(db, days=7)["overall"]
    assert ov["questions"] == baseline["questions"] + 3
    assert ov["by_mode"]["full"] == baseline["by_mode"]["full"] + 2
    assert ov["by_mode"]["live"] == baseline["by_mode"]["live"] + 1
    q_total = ov["questions"]
    assert ov["hit_rate"] == pytest.approx(round((baseline["by_mode"]["full"] + 2) / q_total, 4))
    assert ov["live_ratio"] == pytest.approx(round((baseline["by_mode"]["live"] + 1) / q_total, 4))


def test_summary_live_ratio_warn(db):
    # live 占比 ≥50% → 告警（与 #153 同阈值）。清窗后窗口内仅剩本用例数据，保证确定性。
    db.execute(text("DELETE FROM ai_reports WHERE is_public = FALSE"))
    db.commit()
    _add_report(db, question="a", hit_mode="live")
    _add_report(db, question="b", hit_mode="full")
    db.flush()
    ov = build_summary(db, days=7)["overall"]
    assert ov["questions"] == 2
    assert ov["live_ratio_warn"] is True


# ── build_template_eval 模板评估 ──────────────────────────
def test_template_eval_zero_hit_first(db):
    t1 = _add_tmpl(db, code="te_hot")
    t2 = _add_tmpl(db, code="te_cold", name_cn="冷模板")
    db.flush()
    _add_report(db, is_public=True, template_id=t1.id, matched_count=5)
    _add_report(db, is_public=True, template_id=t2.id, matched_count=0)
    db.flush()

    rows = build_template_eval(db, days=7)
    by_code = {r["code"]: r for r in rows}
    assert by_code["te_hot"]["snapshots"] == 1 and by_code["te_hot"]["matched"] == 5
    assert by_code["te_hot"]["hit_rate"] == pytest.approx(5.0)
    assert by_code["te_cold"]["matched"] == 0 and by_code["te_cold"]["hit_rate"] == 0
    # 0 命中排最前
    assert rows[0]["code"] == "te_cold"


# ── build_anchor_check 跨链路锚点 ─────────────────────────
def test_anchor_check_static_match(db):
    out = build_anchor_check(db, days=7)
    # 生成侧(_SYSTEM_PROMPT 铁律3 提取)与摘取侧(H2_ANCHORS)当前一致
    assert out["static_match"] is True
    assert set(out["generation_anchors"]) == set(out["extract_anchors"])
    assert "## 退款分析" in out["generation_anchors"]


def test_anchor_check_drift_detected(db):
    # 构造一份缺锚点的周报 → missing_anchors 应点名缺失锚点
    _add_report(db, is_public=True, period="weekly",
                answer_md="## 接待分析\nx\n## 趋势对比\ny\n## 结论建议\nz\n")
    db.flush()
    out = build_anchor_check(db, days=7)
    assert out["reports_checked"] == 1
    miss = {m["anchor"] for m in out["missing_anchors"]}
    assert "## 退款分析" in miss and "## 投诉分析" in miss
    # 非约定 H2 不应误报（该报告未含非约定标题）
    assert not out["non_anchor_h2"]


def test_generation_anchors_extracted():
    anchors = _generation_anchors()
    assert len(anchors) == 5
    assert all(a.startswith("## ") for a in anchors)


# ── build_suggestions 归因建议 ────────────────────────────
def test_suggestions_zero_hit_template(db):
    t = _add_tmpl(db, code="sg_zero", keywords=["今日"])
    db.flush()
    _add_report(db, is_public=True, template_id=t.id, matched_count=0)
    # 问句带空格分隔 → _candidate_words 能切出 2-8 字候选词（"这个月的投诉"、"复盘"）
    _add_report(db, question="这个月的投诉 复盘", hit_mode="live")
    _add_report(db, question="这个月的投诉 复盘", hit_mode="live")
    db.flush()

    items = build_suggestions(db, days=7)
    kw = [i for i in items if i["scope"] == "template_keyword"]
    # 0 命中模板 → 建议补关键词（候选来自高频 live 问句）
    assert any(i["target_id"] == t.id for i in kw)
    # 高频兜底问句 → 建议（补词或新建模板）
    assert any("投诉" in i["suggestion"] for i in items)


def test_suggestions_rule_fp_warn(db):
    _add_rule(db, effect_executed=1, effect_rejected=0, effect_fp=3)
    db.flush()
    items = build_suggestions(db, days=7)
    adj = [i for i in items if i["scope"] == "rule_adjust"]
    assert adj and "误报率" in adj[0]["suggestion"]


def test_governance_eval_fp_ratio(db):
    _add_rule(db, effect_executed=1, effect_fp=1, effect_rejected=0)
    db.flush()
    rows = build_governance_eval(db)
    assert len(rows) == 1
    assert rows[0]["fp_ratio"] == pytest.approx(0.5)
    assert rows[0]["fp_warn"] is False  # 0.5 不 > 0.5


# ── prompt_iterations 状态机 ──────────────────────────────
def test_iteration_state_machine_apply_keywords(db):
    _clean_iterations(db)  # 上轮运行的 commit 残留会撞唯一约束/去重
    t = _add_tmpl(db, code="it_keywords", keywords=["今日", "整体"])
    db.flush()
    item = {
        "scope": "template_keyword", "target_id": t.id, "target_name": t.name_cn,
        "suggestion": "追加候选词: 复盘、这个月", "reason": "0 命中",
        "old_value": "今日、整体", "new_value": "复盘、这个月",
    }
    row = propose_iteration(db, user_id=1, item=item)
    assert row is not None and row.status == "proposed"

    confirmed = confirm_iteration(db, 1, row.id, approved=True)
    assert confirmed["status"] == "confirmed"

    applied = apply_iteration(db, 1, row.id)
    assert applied["status"] == "applied"
    db.refresh(t)
    assert set(t.question_keywords) >= {"今日", "整体", "复盘", "这个月"}


def test_iteration_confirm_reject(db):
    _clean_iterations(db)
    item = {
        "scope": "new_template", "target_id": None, "target_name": "（待建模板）",
        "suggestion": "建议新增模板", "reason": "高频兜底",
        "old_value": None, "new_value": None,
    }
    row = propose_iteration(db, user_id=1, item=item)
    rejected = confirm_iteration(db, 1, row.id, approved=False)
    assert rejected["status"] == "rejected"


def test_iteration_apply_record_only_forbidden(db):
    _clean_iterations(db)
    # anchor_guide 为只记录类，confirmed 后不得 apply（提示词铁律）
    item = {
        "scope": "anchor_guide", "target_id": None, "target_name": "提示词锚点",
        "suggestion": "锚点不一致", "reason": "漂移",
        "old_value": None, "new_value": None,
    }
    row = propose_iteration(db, user_id=1, item=item)
    confirm_iteration(db, 1, row.id, approved=True)
    with pytest.raises(ValueError, match="只记录"):
        apply_iteration(db, 1, row.id)


def test_iteration_dedup_proposed(db):
    _clean_iterations(db)  # 上轮运行的 commit 残留会命中去重 → 误判为 None
    item = {
        "scope": "rule_adjust", "target_id": 1, "target_name": "t",
        "suggestion": "停用", "reason": "误报率高",
        "old_value": None, "new_value": "disable",
    }
    assert propose_iteration(db, user_id=1, item=item) is not None
    assert propose_iteration(db, user_id=1, item=item) is None  # 同 target+new 去重

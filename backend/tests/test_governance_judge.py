# -*- coding: utf-8 -*-
"""治理研判层测试（工单 #164）

覆盖 `governance_judge_service`（设计 §五）：
  - mock LLM 下产出结构化建议并回填 findings（验收 1）
  - 非法输出重试 1 次，两次仍非法不落脏数据（验收 2）
  - 规则命中时 **LLM 未被调用**（断言 call count==0，验收 3）
  - dismiss 沉淀规则后，下次同款 finding 命中规则不再调 LLM（验收 4/7）
  - 不可逆动作（delete_dups）必须 requires_human_confirm=true（设计 §六 红线）

策略：monkeypatch llm_client.chat_completion（假 LLM），记录调用次数；
service 用 commit=False（留在 db 事务内），结束时 db fixture 回滚，无需显式清理。
"""

from __future__ import annotations

import datetime as _dt
import json

import pytest
from sqlalchemy.orm import Session

from app.models.governance_finding import (
    FINDING_STATUS_IGNORED,
    FINDING_STATUS_JUDGED,
    FINDING_STATUS_OPEN,
    VERDICT_ALLOWED_BY_BUSINESS,
    VERDICT_REAL_ISSUE,
    VERDICT_UNCERTAIN,
    GovernanceFinding,
)
from app.models.governance_prompt import GovernancePrompt
from app.models.governance_rule import (
    DISPOSITION_DELETE_DUPS,
    DISPOSITION_REPORT_ONLY,
    GovernanceRule,
)
from app.models.system_event import SystemEvent
from app.services import llm_client
from app.services.governance_judge_service import (
    JudgeError,
    build_messages,
    dismiss_finding,
    judge_findings,
    match_rule,
    validate_resp,
)

_TZ = _dt.timezone(_dt.timedelta(hours=8))
_NOW = _dt.datetime(2026, 9, 20, 3, 0, tzinfo=_TZ)


def _mk_finding(db: Session, *, table: str = "refund_wide", target: str = "created_at_raw",
                detector: str = "row_count_jump", metrics: dict | None = None,
                status: str = FINDING_STATUS_OPEN) -> GovernanceFinding:
    f = GovernanceFinding(
        detector=detector,
        table_name=table,
        target=target,
        severity_raw="warn",
        metrics=metrics or {"cur_rows": 4330, "prev_rows": 2613, "growth_ratio": 1.66},
        samples=[{"date": "2026-09-20", "count": 4330}],
        repro_sql="SELECT ... ;",
        detected_at=_NOW,
        status=status,
    )
    db.add(f)
    db.flush()
    return f


def _judgment(finding_id: int, *, verdict: str = VERDICT_REAL_ISSUE,
              action: str = DISPOSITION_REPORT_ONLY,
              rhc: bool = False, conf: float = 0.9) -> dict:
    return {
        "finding_id": finding_id,
        "verdict": verdict,
        "reason": "依据去重规范，该列非空可判定同一实体，属保护缺口",
        "suggestion": {
            "action": action,
            "params": {},
            "impact": f"涉及表 {action} 相关行",
            "risk": "low",
            "requires_human_confirm": rhc,
        },
        "confidence": conf,
    }


@pytest.fixture()
def fake_llm(monkeypatch):
    """替换 llm_client.chat_completion 为可控假 LLM，记录调用与参数。"""
    calls: list[dict] = []
    state: dict = {"out": None, "seq": [], "raise_call": False}

    async def _fake(messages, **kwargs):
        calls.append({"messages": messages, "kwargs": kwargs})
        if state["raise_call"]:
            raise RuntimeError("mock LLM 连接失败")
        if state["seq"]:
            return json.dumps(state["seq"].pop(0), ensure_ascii=False)
        return json.dumps(state["out"], ensure_ascii=False)

    monkeypatch.setattr(llm_client, "chat_completion", _fake)
    return {"calls": calls, "state": state}


class TestRuleMatch:
    def test_exact_and_wildcard_target(self, db):
        r = GovernanceRule(detector="row_count_jump", table_name="refund_wide",
                           target_pattern="created_at_raw", verdict=VERDICT_ALLOWED_BY_BUSINESS,
                           reason="x", disposition=DISPOSITION_REPORT_ONLY, enabled=True)
        f = _mk_finding(db)
        assert match_rule(r, f) is True
        r2 = GovernanceRule(detector="row_count_jump", table_name="*",
                            target_pattern="*", verdict=VERDICT_ALLOWED_BY_BUSINESS,
                            reason="x", disposition=DISPOSITION_REPORT_ONLY, enabled=True)
        assert match_rule(r2, f) is True

    def test_metrics_condition(self, db):
        f = _mk_finding(db, metrics={"growth_ratio": 1.66})
        r = GovernanceRule(detector="row_count_jump", table_name="refund_wide",
                           target_pattern="*", match_metrics={"growth_ratio": {"gt": 1.5}},
                           verdict=VERDICT_ALLOWED_BY_BUSINESS, reason="x",
                           disposition=DISPOSITION_REPORT_ONLY, enabled=True)
        assert match_rule(r, f) is True
        r2 = GovernanceRule(detector="row_count_jump", table_name="refund_wide",
                            target_pattern="*", match_metrics={"growth_ratio": {"gt": 2.0}},
                            verdict=VERDICT_ALLOWED_BY_BUSINESS, reason="x",
                            disposition=DISPOSITION_REPORT_ONLY, enabled=True)
        assert match_rule(r2, f) is False


class TestValidateResp:
    def test_valid_batch(self):
        ok, errs = validate_resp({"judgments": [_judgment(1), _judgment(2)]}, {1, 2})
        assert ok and not errs

    def test_invalid_verdict(self):
        j = _judgment(1)
        j["verdict"] = "definitely_issue"
        ok, errs = validate_resp({"judgments": [j]}, {1})
        assert not ok and any("verdict" in e for e in errs)

    def test_delete_dups_must_human_confirm(self):
        j = _judgment(1, action=DISPOSITION_DELETE_DUPS, rhc=False)
        ok, errs = validate_resp({"judgments": [j]}, {1})
        assert not ok and any("requires_human_confirm" in e for e in errs)

    def test_missing_finding_rejected(self):
        ok, errs = validate_resp({"judgments": [_judgment(1)]}, {1, 2})
        assert not ok and any("遗漏" in e for e in errs)

    def test_batch_split_by_batch_size(self, db, fake_llm):
        """超过 `_BATCH_SIZE` 的待研判项要**切批**。

        #169 生产实测：10 条批 267s 全部 ReadTimeout → 该批保持 open，下一轮仍是同样 10 条
        → **研判永不收敛（livelock）**；批降到 3（≈75s）后每批都能在超时内完成。
        """
        from app.services.governance_judge_service import _BATCH_SIZE

        n = _BATCH_SIZE + 1
        ids = [_mk_finding(db, target=f"col_{i}").id for i in range(n)]
        fake_llm["state"]["seq"] = [
            {"judgments": [_judgment(i) for i in ids[:_BATCH_SIZE]]},
            {"judgments": [_judgment(i) for i in ids[_BATCH_SIZE:]]},
        ]
        res = judge_findings(db, commit=False)
        assert res["judged"] == n and res["unjudged_ids"] == []
        assert len(fake_llm["calls"]) == 2, f"{n} 条应按 {_BATCH_SIZE} 切 2 批"
        # 第一批的 user 消息里只含 _BATCH_SIZE 条 finding（切批真的生效）
        first_payload = fake_llm["calls"][0]["messages"][1]["content"]
        assert first_payload.count('"finding_id"') == _BATCH_SIZE

    def test_bad_confidence(self):
        j = _judgment(1, conf=1.5)
        ok, errs = validate_resp({"judgments": [j]}, {1})
        assert not ok and any("confidence" in e for e in errs)


class TestJudgeFlow:
    def test_mock_llm_produces_structured_suggestion(self, db, fake_llm):
        """验收 1：mock LLM 下产出结构化建议并回填 findings。"""
        f = _mk_finding(db)
        fake_llm["state"]["out"] = {"judgments": [_judgment(f.id)]}
        res = judge_findings(db, commit=False)

        assert res["total_open"] == 1 and res["by_ai"] == 1 and res["by_rule"] == 0
        db.flush()
        db.expire_all()
        f2 = db.get(GovernanceFinding, f.id)
        assert f2.status == FINDING_STATUS_JUDGED
        assert f2.verdict == VERDICT_REAL_ISSUE
        assert f2.suggestion["action"] == DISPOSITION_REPORT_ONLY
        assert f2.confidence == 0.9
        assert f2.rule_id is None
        assert len(fake_llm["calls"]) == 1
        # 提示词注入了规范与表说明
        msgs = fake_llm["calls"][0]["messages"]
        assert "去重键红线" in msgs[0]["content"]

    def test_invalid_output_retries_once_then_succeeds(self, db, fake_llm):
        """验收 2：非法输出重试 1 次，第 2 次合法则成功。"""
        f = _mk_finding(db)
        bad = _judgment(f.id)
        bad["verdict"] = "junk"
        fake_llm["state"]["seq"] = [{"judgments": [bad]}, {"judgments": [_judgment(f.id)]}]
        res = judge_findings(db, commit=False)

        assert res["by_ai"] == 1
        assert len(fake_llm["calls"]) == 2
        # 第 2 次调用带了纠错提示
        assert "上次输出无效" in fake_llm["calls"][1]["messages"][-1]["content"]

    def test_invalid_output_twice_keeps_open(self, db, fake_llm):
        """验收 2：两次仍非法 → 不落脏数据，finding 保持 open。"""
        f = _mk_finding(db)
        bad = _judgment(f.id)
        bad["verdict"] = "junk"
        fake_llm["state"]["out"] = {"judgments": [bad]}
        res = judge_findings(db, commit=False)

        assert res["by_ai"] == 0
        assert res["unjudged_ids"] == [f.id]
        db.flush()
        db.expire_all()
        assert db.get(GovernanceFinding, f.id).status == FINDING_STATUS_OPEN
        assert len(fake_llm["calls"]) == 2  # 重试 1 次 = 共 2 次

    def test_rule_shortcut_skips_llm(self, db, fake_llm):
        """验收 3：规则命中 → LLM 未被调用（call count==0）。"""
        f = _mk_finding(db)
        db.add(GovernanceRule(
            detector=f.detector, table_name=f.table_name, target_pattern=f.target,
            verdict=VERDICT_ALLOWED_BY_BUSINESS, reason="现有规则判定",
            disposition=DISPOSITION_REPORT_ONLY, hit_count=0, enabled=True,
        ))
        db.flush()
        fake_llm["state"]["raise_call"] = True  # 若误调 LLM 会抛错
        res = judge_findings(db, commit=False)

        assert res["by_rule"] == 1 and res["by_ai"] == 0
        assert res["llm_called"] is False
        assert len(fake_llm["calls"]) == 0  # 关键断言：未调用
        db.flush()
        db.expire_all()
        f2 = db.get(GovernanceFinding, f.id)
        assert f2.status == FINDING_STATUS_JUDGED and f2.verdict == VERDICT_ALLOWED_BY_BUSINESS
        assert f2.rule_id is not None
        rule = db.get(GovernanceRule, f2.rule_id)
        assert rule.hit_count == 1

    def test_dismiss_sinks_rule_and_next_hits_it(self, db, fake_llm):
        """验收 4/7：dismiss 沉淀规则；下次同款 finding 命中规则不再调 LLM。"""
        # 第一次：人工 dismiss → 写 governance_rules
        f1 = _mk_finding(db)
        out = dismiss_finding(db, f1.id, reason="人工判定为业务允许", commit=False)
        assert out["created"] is True
        assert f1.status == FINDING_STATUS_IGNORED
        db.flush()
        rule = db.get(GovernanceRule, out["rule_id"])
        # 沉淀的规则：判定「业务允许」且**只读**（dismiss 不附带任何自动改数据授权）
        assert rule.verdict == VERDICT_ALLOWED_BY_BUSINESS
        assert rule.disposition == DISPOSITION_REPORT_ONLY
        assert rule.auto_level == "L0_readonly"
        assert rule.source_finding_id == f1.id
        assert rule.enabled is True

        # 第二次：同款新 finding（探测器重新扫出）→ 规则命中，LLM 不被调用
        f2 = _mk_finding(db, status=FINDING_STATUS_OPEN)
        fake_llm["state"]["raise_call"] = True
        res = judge_findings(db, commit=False)

        assert res["by_rule"] == 1
        assert len(fake_llm["calls"]) == 0
        db.flush()
        db.expire_all()
        f2b = db.get(GovernanceFinding, f2.id)
        assert f2b.status == FINDING_STATUS_JUDGED
        assert f2b.verdict == VERDICT_ALLOWED_BY_BUSINESS
        assert f2b.rule_id == rule.id
        assert db.get(GovernanceRule, rule.id).hit_count == 1

    def test_judge_and_sink_are_audited(self, db, fake_llm):
        """留痕：研判记 governance_judge（含 llm_called/by_rule），dismiss 记 governance_rule_sink。"""
        f = _mk_finding(db)
        fake_llm["state"]["out"] = {"judgments": [_judgment(f.id, verdict=VERDICT_UNCERTAIN)]}
        judge_findings(db, commit=False)
        dismiss_finding(db, f.id, commit=False)
        db.flush()

        evs = db.query(SystemEvent).filter(
            SystemEvent.category == "ai_governance",
            SystemEvent.action.in_(["governance_judge", "governance_rule_sink"]),
        ).all()
        actions = {e.action for e in evs}
        assert actions == {"governance_judge", "governance_rule_sink"}
        judge_ev = next(e for e in evs if e.action == "governance_judge")
        assert '"llm_called": true' in (judge_ev.detail or "")
        assert "by_rule" in (judge_ev.detail or "")

    def test_dismiss_twice_raises(self, db):
        f = _mk_finding(db)
        dismiss_finding(db, f.id, commit=False)
        with pytest.raises(JudgeError):
            dismiss_finding(db, f.id, commit=False)


class TestPromptConfig:
    """提示词落 DB 可迭代（工单 #164 处理动作第 1 条）。"""

    def test_db_prompt_overrides_default(self, db):
        f = _mk_finding(db)
        db.add(GovernancePrompt(code="judge_system", name_cn="研判·系统提示",
                                content="【DB版系统提示】{spec_rules}", version=2, is_active=True))
        db.flush()
        msgs = build_messages(db, [f])
        assert msgs[0]["content"].startswith("【DB版系统提示】")
        assert "去重键红线" in msgs[0]["content"]  # 规范段由代码注入，仍必须带上

    def test_inactive_prompt_falls_back_to_default(self, db):
        f = _mk_finding(db)
        db.add(GovernancePrompt(code="judge_system", name_cn="研判·系统提示",
                                content="【不该生效】", version=1, is_active=False))
        db.flush()
        msgs = build_messages(db, [f])
        assert "数据治理分析师" in msgs[0]["content"]

    def test_user_prompt_carries_evidence_and_table_notes(self, db):
        f = _mk_finding(db)
        msgs = build_messages(db, [f])
        user = msgs[1]["content"]
        assert "finding_id" in user and str(f.id) in user
        assert "repro_sql" in user and "samples" in user
        assert f.table_name in user  # 表说明段


class TestSuggestionGuard:
    def test_delete_dups_normalized_to_human_confirm(self):
        """第二道闸：即使 LLM 漏标，delete_dups 落库也强制 requires_human_confirm=true。"""
        from app.services.governance_judge_service import _normalize_suggestion

        out = _normalize_suggestion({
            "action": DISPOSITION_DELETE_DUPS, "params": {}, "impact": "删 100 行",
            "risk": "high", "requires_human_confirm": False,
        })
        assert out["requires_human_confirm"] is True

    def test_allowed_by_business_never_mark_ignored(self):
        """#171 红线兜底：业务允许 + mark_ignored → 降级 report_only（不改经营口径）。"""
        from app.services.governance_judge_service import _normalize_suggestion

        out = _normalize_suggestion({
            "action": "mark_ignored", "params": {"x": 1}, "impact": "排除若干行",
            "risk": "low", "requires_human_confirm": False,
        }, verdict=VERDICT_ALLOWED_BY_BUSINESS)
        assert out["action"] == "report_only"
        assert out["params"] == {}
        assert "降级" in (out.get("note") or "")
        # 其它 verdict 不受影响（真问题的 L1 标记仍可用）
        keep = _normalize_suggestion({
            "action": "mark_ignored", "params": {}, "impact": "x", "risk": "low",
            "requires_human_confirm": False,
        }, verdict=VERDICT_REAL_ISSUE)
        assert keep["action"] == "mark_ignored"


class TestAllowedByBusinessRedline:
    """#171：LLM 硬给 mark_ignored 也必须被降级（落库前第二道闸）。"""

    def test_llm_mark_ignored_downgraded_on_persist(self, db, fake_llm):
        f = _mk_finding(db)
        j = _judgment(f.id, verdict=VERDICT_ALLOWED_BY_BUSINESS, action="mark_ignored")
        fake_llm["state"]["out"] = {"judgments": [j]}
        judge_findings(db, commit=False)
        db.flush()
        db.expire_all()
        f2 = db.get(GovernanceFinding, f.id)
        assert f2.verdict == VERDICT_ALLOWED_BY_BUSINESS
        assert f2.suggestion["action"] == "report_only", "业务允许不得标 ignored"
        assert "note" in f2.suggestion

    def test_prompt_contains_redline(self, db):
        """系统提示词（默认路径）必须含该铁律。"""
        f = _mk_finding(db)
        msgs = build_messages(db, [f])
        assert "allowed_by_business" in msgs[0]["content"]
        assert "排除出经营统计" in msgs[0]["content"]

# -*- coding: utf-8 -*-
"""AI 数据接入助手·起草服务测试（工单 #156）

对应设计: docs/design/AI数据接入助手设计-20260919.md §五（AI 起草服务）。

为什么要这组测试:
  起草服务是「AI 起草 → 规则校验 → 人工确认」链路的第一环 —— LLM 输出不可信，
  服务必须保证: ① 脱敏后才进 LLM（真实业务数据不出界）；② schema 校验不过/LLM
  失败整体重试 1 次，仍失败不落脏数据；③ 起草后自动跑校验器，validator_report
  与 system_event（含 provider/耗时/fail 数）留痕；④ 提示词可经 DB 迭代。

组织方式:
  - monkeypatch llm_client.chat_completion（假 LLM），全链路真跑（含 llm_scheduler
    L1 门闸、校验器、落库、system_event），只替换 LLM 输出这一个不可控环节；
  - 每个用例用唯一 code + 结尾清理（service 内 commit 已落库，需显式清理防污染
    共享测试库 csctalon_test —— #126 教训的延续）。
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import delete, select

from app.models.data_source_spec import DataSourceSpec
from app.models.onboarding_prompt import OnboardingPrompt
from app.models.system_event import SystemEvent
from app.services import llm_client
from app.services.onboarding_draft_service import (
    DraftError,
    build_messages,
    build_sample_summary,
    build_spec_rules,
    draft_spec,
    sanitize_samples,
    sanitize_value,
    validate_llm_output,
)


def _unique_code() -> str:
    return f"t_draft_{uuid.uuid4().hex[:10]}"


def _llm_output(code: str | None = None, **overrides) -> dict:
    """合法的 LLM 输出基线（全字段齐全，过 validator 全部规则）。"""
    out = {
        "spec": {
            "code": code or _unique_code(),
            "name_cn": "订单·接入测试台账",  # 避开真实 registry 投影「订单·支付台账」(R3)
            "category": "交易与售后",
            "sort_order": 10,
            "time_field": "create_date",
            "sn_field": "device_sn",
            "pk_field": "order_no",
            "pk_strategy": "primary_key",
            "sync_source": "bms",
            "description": "BMS 支付管理订单台账",
        },
        "fields": [
            {"field_name": "order_no", "name_cn": "订单号", "data_type": "string",
             "semantic_role": "dimension", "agg_func": None, "is_filterable": True,
             "is_pk": True, "enum_values": None, "time_zone_note": None,
             "confidence": 0.98, "evidence": "列名 order_no 唯一标识订单"},
            {"field_name": "device_sn", "name_cn": "设备SN", "data_type": "string",
             "semantic_role": "sn", "agg_func": None, "is_filterable": True,
             "is_pk": False, "enum_values": None, "time_zone_note": None,
             "confidence": 0.97, "evidence": "列名含 sn 设备序列号"},
            {"field_name": "create_date", "name_cn": "创建时间", "data_type": "timestamp",
             "semantic_role": "time", "agg_func": None, "is_filterable": True,
             "is_pk": False, "enum_values": None, "time_zone_note": "Asia/Shanghai",
             "confidence": 0.95, "evidence": "列名含 create 时间戳"},
            {"field_name": "amount", "name_cn": "金额", "data_type": "numeric",
             "semantic_role": "amount", "agg_func": "sum", "is_filterable": False,
             "is_pk": False, "enum_values": None, "time_zone_note": None,
             "unit": "分", "confidence": 0.96, "evidence": "列名含 amount 且样例为两位小数"},
        ],
        "dedup_recommendation": {
            "strategy": "primary_key",
            "fields": ["order_no"],
            "rationale": "订单号唯一，参照去重规范主键策略",
            "requires_human_confirm": True,
        },
        "risks": ["create_date 可能非真实业务时间，需人工确认"],
        "unknown_fields": [],
        "overall_confidence": 0.92,
    }
    out.update(overrides)
    return out


def _samples(n: int = 3) -> list[dict]:
    return [
        {"order_no": f"BMS20260919{i:03d}", "device_sn": f"SN{i:08d}",
         "combo_name": "流量套餐A", "create_date": "2026-09-19 10:00:00",
         "amount": 12800}
        for i in range(n)
    ]


def _cleanup(db, *codes: str) -> None:
    """清理本次用例落库的草稿与 ai_governance 事件（service 内 commit 已落库）。"""
    for code in codes:
        db.execute(delete(DataSourceSpec).where(DataSourceSpec.code == code))
    db.execute(delete(SystemEvent).where(SystemEvent.category == "ai_governance"))
    db.commit()


def _latest_event(db) -> SystemEvent:
    return db.execute(
        select(SystemEvent).where(SystemEvent.category == "ai_governance")
        .order_by(SystemEvent.id.desc()).limit(1)
    ).scalars().first()


# ─────────────────────────────────────────────────────────────────────
# 脱敏与样例摘要
# ─────────────────────────────────────────────────────────────────────
class TestSanitize:
    def test_pii_masked_and_long_string_truncated(self):
        assert sanitize_value("13812345678") == "138****0000"
        assert sanitize_value("abc@example.com") == "email@***.com"
        assert sanitize_value("110101199001011234") == "******"
        assert sanitize_value(12800) == 12800  # 数值保留（金额语义推断需要）
        assert sanitize_value("长" * 100) == "长" * 60 + "…"

    def test_samples_capped_to_50_rows(self):
        rows = [{"order_no": f"B{i:03d}", "phone": "13812345678"} for i in range(60)]
        sanitized = sanitize_samples(rows)
        assert len(sanitized) == 50
        assert all("138****0000" in str(r["phone"]) for r in sanitized)

    def test_sample_summary_shape(self):
        summary = build_sample_summary([{"order_no": "B1", "amount": 100}])
        assert summary["row_count"] == 1
        assert summary["columns"][0]["name"] == "order_no"
        assert summary["columns"][0]["sample_values"] == ["B1"]


# ─────────────────────────────────────────────────────────────────────
# 提示词构造（规范注入 + DB 可迭代）
# ─────────────────────────────────────────────────────────────────────
class TestPrompt:
    def test_spec_rules_injects_code_single_source(self):
        rules = build_spec_rules()
        # 规范段来自代码常量（单源镜像），不含占位符残留
        assert "前缀限:" in rules and "字段语义角色枚举" in rules
        assert "requires_human_confirm" in rules

    def test_build_messages_injects_rules_and_output_schema(self, db):
        msgs = build_messages(db, table_hint="bms_order", business_note="台账",
                              sanitized=_samples())
        assert msgs[0]["role"] == "system"
        assert "数据源接入助手" in msgs[0]["content"]
        assert "前缀限:" in msgs[0]["content"]          # 规范注入
        assert '"semantic_role"' in msgs[0]["content"]  # 输出结构注入
        assert "bms_order" in msgs[1]["content"]
        assert '"order_no": "BMS20260919' in msgs[1]["content"]  # 样例注入

    def test_db_prompt_override_used(self, db):
        db.add(OnboardingPrompt(
            code="draft_system", name_cn="自定义", version=2, is_active=True,
            content="自定义起草提示词\n{spec_rules}\n{output_schema}",
        ))
        db.commit()
        try:
            msgs = build_messages(db, table_hint=None, business_note=None, sanitized=_samples())
            assert "自定义起草提示词" in msgs[0]["content"]
            assert "前缀限:" in msgs[0]["content"]  # 占位符仍被格式化注入
        finally:
            # 按内容清理，避免污染共享测试库并影响后续用例（只删本次插入的）
            db.execute(delete(OnboardingPrompt).where(
                OnboardingPrompt.content.contains("自定义起草提示词")))
            db.commit()

    def test_disabled_db_prompt_falls_back(self, db):
        db.add(OnboardingPrompt(
            code="draft_system", name_cn="禁用", version=1, is_active=False,
            content="不应被使用",
        ))
        db.commit()
        try:
            msgs = build_messages(db, table_hint=None, business_note=None, sanitized=_samples())
            assert "不应被使用" not in msgs[0]["content"]
            assert "数据源接入助手" in msgs[0]["content"]  # 回退模块默认
        finally:
            db.execute(delete(OnboardingPrompt).where(
                OnboardingPrompt.content.contains("不应被使用")))
            db.commit()


# ─────────────────────────────────────────────────────────────────────
# LLM 输出 schema 校验
# ─────────────────────────────────────────────────────────────────────
class TestSchemaValidation:
    def test_valid_output_passes(self):
        ok, errs = validate_llm_output(_llm_output())
        assert ok, errs

    def test_missing_spec_key(self):
        out = _llm_output()
        del out["spec"]["name_cn"]
        ok, errs = validate_llm_output(out)
        assert not ok and any("spec.name_cn" in e for e in errs)

    def test_invalid_role(self):
        out = _llm_output()
        out["fields"][0]["semantic_role"] = "monetary"
        ok, errs = validate_llm_output(out)
        assert not ok and any("semantic_role" in e for e in errs)

    def test_dedup_redline_must_be_human_confirm(self):
        out = _llm_output()
        out["dedup_recommendation"]["requires_human_confirm"] = False
        ok, errs = validate_llm_output(out)
        assert not ok and any("requires_human_confirm" in e for e in errs)


# ─────────────────────────────────────────────────────────────────────
# 起草全链路（mock LLM + L1 门闸 + 校验器 + 落库 + 留痕）
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture()
def fake_llm(monkeypatch):
    """替换 llm_client.chat_completion 为可控假 LLM，记录调用参数。"""
    calls: list[dict] = []
    state = {"output": None, "raise_on": 0}

    async def _fake(messages, **kwargs):
        calls.append({"messages": messages, "kwargs": kwargs})
        if state["raise_on"] and len(calls) <= state["raise_on"]:
            raise RuntimeError("mock LLM 连接失败")
        return json.dumps(state["output"], ensure_ascii=False)

    monkeypatch.setattr(llm_client, "chat_completion", _fake)
    return {"calls": calls, "state": state}


class TestDraftFlow:
    def test_happy_path_persists_draft_and_events(self, db, fake_llm):
        code = _unique_code()
        fake_llm["state"]["output"] = _llm_output(code)
        result = draft_spec(db, table_hint="bms_pay_tbl", business_note="台账",
                            samples=_samples(), created_by=1)
        try:
            assert result["status"] == "draft"
            assert result["validator_report"]["fail"] == []
            spec = db.get(DataSourceSpec, result["spec_id"])
            assert spec.code == code
            assert spec.table_name == "bms_pay_tbl"
            assert spec.status == "draft"
            assert len(spec.field_specs) == 4
            assert spec.dedup_recommendation["requires_human_confirm"] is True
            assert spec.created_by == 1
            assert spec.sample_summary["row_count"] == 3
            ev = _latest_event(db)
            assert ev.action == "onboarding_draft"
            assert ev.status == "success"
            assert "duration_ms" in ev.detail and '"fail_count": 0' in ev.detail
        finally:
            _cleanup(db, code)

    def test_validator_fail_kept_in_report_and_event_partial(self, db, fake_llm):
        code = _unique_code()
        out = _llm_output(code)
        out["spec"]["category"] = "未登记分组"  # R6 fail
        fake_llm["state"]["output"] = out
        result = draft_spec(db, samples=_samples())
        try:
            assert any(i["rule"] == "R6" for i in result["validator_report"]["fail"])
            spec = db.get(DataSourceSpec, result["spec_id"])
            assert spec.status == "draft"  # fail 不阻塞落草稿（确认时才必须清零）
            ev = _latest_event(db)
            assert ev.status == "partial"  # 有 fail → partial 留痕
            assert '"fail_count": 1' in ev.detail
        finally:
            _cleanup(db, code)

    def test_invalid_output_retries_once_then_succeeds(self, db, monkeypatch):
        code = _unique_code()
        calls: list[dict] = []
        invalid = _llm_output(code)
        invalid["spec"]["pk_strategy"] = "bogus"  # 第 1 次非法输出
        outputs = iter([invalid, _llm_output(code)])

        async def _fake(messages, **kwargs):
            calls.append({"messages": messages, "kwargs": kwargs})
            return json.dumps(next(outputs), ensure_ascii=False)

        monkeypatch.setattr(llm_client, "chat_completion", _fake)
        result = draft_spec(db, samples=_samples(), created_by=1)
        try:
            assert result["status"] == "draft"
            assert result["retried"] is True
            assert len(calls) == 2
            # 第 2 次调用追加了纠错提示
            assert "上次输出无效" in calls[1]["messages"][-1]["content"]
            # 走默认实时链（provider_code=None），不降级 122b
            assert calls[1]["kwargs"]["provider_code"] is None
            assert calls[1]["kwargs"]["timeout_s"] == 60.0
        finally:
            _cleanup(db, code)

    def test_invalid_output_twice_raises_no_draft(self, db, fake_llm):
        code = _unique_code()
        out = _llm_output(code)
        out["spec"]["category"] = "x"  # 但这里只破坏 schema 层（category 缺失更直接）
        out["spec"].pop("category", None)
        fake_llm["state"]["output"] = out  # 始终非法（category 缺失）
        with pytest.raises(DraftError):
            draft_spec(db, samples=_samples())
        # 不落草稿 + 失败事件留痕
        assert db.execute(
            select(DataSourceSpec.id).where(DataSourceSpec.code == code)
        ).scalar() is None
        ev = _latest_event(db)
        assert ev.status == "failed"
        _cleanup(db, code)

    def test_commit_failure_still_writes_failed_event(self, db, fake_llm, monkeypatch):
        """#175: commit 抛未预期异常使 session 进入需回滚态时，failed 事件仍须落库。

        历史缺陷：except Exception 分支写 record_system_event 前未 db.rollback()，
        SQLAlchemy 在 commit 异常后禁止任何后续 flush，导致 failed 事件二次抛错丢失。
        修复后：异常分支先 rollback() 再写事件，failed 事件在回滚态下也能真实入库。
        """
        code = _unique_code()
        fake_llm["state"]["output"] = _llm_output(code)

        real_commit = db.commit
        first_commit = True

        def _flaky_commit():
            # _draft_core 内落库前会多次 flush/commit（含 _release_stale_code 预检），
            # 仅在最终落草稿的 commit 抛一次错，模拟 flush/commit 失败后的回滚态。
            # 注: 用 ValueError 而非 RuntimeError —— draft_spec wrapper 会把 RuntimeError
            # 当作「已在事件循环」的 fallback 信号而重跑，导致断言不到抛错。
            nonlocal first_commit
            if first_commit:
                first_commit = False
                db.flush()  # 让 session 建立待写状态后抛错，进入需回滚态
                raise ValueError("模拟 commit 失败（flush 后回滚态）")
            return real_commit()

        monkeypatch.setattr(db, "commit", _flaky_commit)
        with pytest.raises(ValueError):
            draft_spec(db, samples=_samples())
        monkeypatch.setattr(db, "commit", real_commit)

        # 修复后：session 回滚态下 record_system_event 仍能把 failed 事件写进去
        spec_id = db.execute(
            select(DataSourceSpec.id).where(DataSourceSpec.code == code)
        ).scalar()
        assert spec_id is None or db.get(DataSourceSpec, spec_id).status != "draft"
        ev = _latest_event(db)
        assert ev is not None and ev.status == "failed"
        _cleanup(db, code)

    def test_llm_call_failure_retries_once(self, db, fake_llm):
        code = _unique_code()
        fake_llm["state"]["output"] = _llm_output(code)
        fake_llm["state"]["raise_on"] = 1  # 第 1 次抛错，第 2 次成功
        result = draft_spec(db, samples=_samples())
        try:
            assert result["status"] == "draft"
            assert len(fake_llm["calls"]) == 2
            assert result["retried"] is True
        finally:
            _cleanup(db, code)

    def test_llm_fails_twice_raises(self, db, fake_llm):
        fake_llm["state"]["output"] = _llm_output()
        fake_llm["state"]["raise_on"] = 2  # 两次都抛
        with pytest.raises(DraftError):
            draft_spec(db, samples=_samples())
        _cleanup(db)  # 失败事件留痕，清理防污染

    def test_duplicate_code_raises(self, db, fake_llm):
        code = _unique_code()
        fake_llm["state"]["output"] = _llm_output(code)
        draft_spec(db, samples=_samples(), created_by=1)
        try:
            # 第二次同 code → 拒绝（先处置旧草稿或调整 code）
            with pytest.raises(DraftError, match="已存在同 code"):
                draft_spec(db, samples=_samples())
        finally:
            _cleanup(db, code)

    def test_empty_samples_raises(self, db):
        with pytest.raises(DraftError, match="samples 不能为空"):
            draft_spec(db, samples=[])
        _cleanup(db)  # 输入校验失败也留痕失败事件，清理

    def test_non_dict_row_raises(self, db):
        with pytest.raises(DraftError, match="每行必须是 JSON 对象"):
            draft_spec(db, samples=[{"a": 1}, "not-a-dict"])
        _cleanup(db)

# -*- coding: utf-8 -*-
"""check_llm_talon_drift 巡检判定单测 —— #300。

用构造的 overview/DB 行锁定四类漂移, 场景全部来自 2026-10-12 真实事件:
  - 122b 下线仍在 expert 链 → E2
  - 9b structured_output=fail 却在 assistant → E3
  - coder-30b(MoE) 在 specialist → E4
  - 链 provider 被禁用 → E1
另含全绿基线, 防止规则演进误伤当前正确配置。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.check_llm_talon_drift import _code_model_mismatch, _norm_model, evaluate


def _item(model: str, *, duty=True, preload=True, healthy=True, tested=None, display=None):
    return {
        "model": model,
        "display": display or model.split(":")[0],
        "tested": tested if tested is not None else {"structured_output": "pass",
                                                     "reasoning_multistep": "pass"},
        "duty": ({"duty": "node-x", "preload": preload, "duty_healthy": healthy}
                 if duty else None),
    }


def _prov(code, model, enabled=True, is_cloud=False, base_url="http://g/v1"):
    return {"code": code, "model": model, "enabled": enabled,
            "is_cloud": is_cloud, "base_url": base_url}


def _mem(role, code, enabled=True, order=1):
    return {"role_key": role, "provider_code": code, "sort_order": order, "enabled": enabled}


def test_clean_baseline():
    """当前正确配置: 120b/14b/27b/coder(仅 assistant) 全 pass 且值班 → 0 ERROR"""
    providers = [
        _prov("p-120b", "gpt-oss:120b"),
        _prov("p-14b", "qwen2.5:14b"),
        _prov("p-27b", "qwen3.8:27b"),
        _prov("p-coder", "qwen3-coder:30b"),
        _prov("p-7b", "qwen2.5:7b"),
    ]
    members = [
        _mem("assistant", "p-coder", order=1), _mem("assistant", "p-7b", order=2),
        _mem("specialist", "p-27b", order=1), _mem("specialist", "p-14b", order=2),
        _mem("expert", "p-120b", order=1), _mem("expert", "p-14b", order=2),
        _mem("governance", "p-27b", order=1), _mem("governance", "p-14b", order=2),
    ]
    overview = {"items": [_prov_item for _prov_item in [
        _item("gpt-oss:120b"), _item("qwen2.5:14b"), _item("qwen3.8:27b"),
        _item("qwen3-coder:30b"),
        _item("qwen2.5:7b", tested={"structured_output": "pass",
                                    "reasoning_multistep": "fail"}),
    ]]}
    r = evaluate(providers, members, overview)
    assert r["errors"] == [], r["errors"]
    # 7b reasoning fail 在 assistant 是非必需能力 → W2, 不升级
    assert any("p-7b" in w and "W2" in w for w in r["warnings"])


def test_e2_removed_model_122b():
    """122b 从 talon 下线仍在 expert 链 → E2（真实事故场景）"""
    providers = [_prov("p-120b", "gpt-oss:120b"), _prov("p-122b", "qwen3.5:122b")]
    members = [_mem("expert", "p-120b", order=1), _mem("expert", "p-122b", order=2)]
    overview = {"items": [_item("gpt-oss:120b")]}  # 无 122b
    r = evaluate(providers, members, overview)
    assert any("E2" in e and "122b" in e for e in r["errors"]), r["errors"]


def test_e3_assistant_structured_output_fail():
    """9b structured_output=fail 在 assistant → E3（真实事故场景）"""
    providers = [_prov("p-9b", "qwen3.5:9b")]
    members = [_mem("assistant", "p-9b")]
    overview = {"items": [_item("qwen3.5:9b", tested={
        "structured_output": "fail", "reasoning_multistep": "fail"})]}
    r = evaluate(providers, members, overview)
    assert any("E3" in e and "structured_output" in e for e in r["errors"]), r["errors"]


def test_e4_numeric_unsafe_coder_in_specialist():
    """coder-30b(MoE 数值缺陷) 在 specialist → E4（守卫测试同一条锁）"""
    providers = [_prov("p-coder", "qwen3-coder:30b")]
    members = [_mem("specialist", "p-coder")]
    overview = {"items": [_item("qwen3-coder:30b")]}
    r = evaluate(providers, members, overview)
    assert any("E4" in e for e in r["errors"]), r["errors"]
    # 对照: 同一个 coder 在 assistant 不判 E4（不涉数值）
    r2 = evaluate(providers, [_mem("assistant", "p-coder")], overview)
    assert not any("E4" in e for e in r2["errors"])


def test_e1_disabled_provider():
    providers = [_prov("p-x", "qwen2.5:14b", enabled=False)]
    r = evaluate(providers, [_mem("governance", "p-x")], {"items": []})
    assert any("E1" in e for e in r["errors"])


def test_w1_cold_backup_and_w4_code_mismatch():
    """冷备(preload=false) → W1; code 名实不符(macmini-7b 实跑 9b) → W4"""
    providers = [_prov("talon-macmini-qwen2.5-7b", "qwen3.5:9b")]
    members = [_mem("assistant", "talon-macmini-qwen2.5-7b")]
    overview = {"items": [_item("qwen3.5:9b", preload=False)]}
    r = evaluate(providers, members, overview)
    assert any("W1" in w and "冷备" in w for w in r["warnings"]), r["warnings"]
    assert any("W4" in w for w in r["warnings"]), r["warnings"]
    # 纯函数单测: 名实不符启发式
    assert _code_model_mismatch("talon-macmini-qwen2.5-7b", "qwen3.5:9b")
    assert not _code_model_mismatch("talon-gmk-gpt-oss-120b", "gpt-oss:120b")
    assert _norm_model("glm-4.7-flash:latest") == "glm-4.7-flash"


def test_w3_cloud_no_tested_not_error():
    """云端模型 tested={} → W3 提示, 不判能力 ERROR（无数据不等于失败）"""
    providers = [_prov("ds", "deepseek-flash", is_cloud=True)]
    members = [_mem("expert", "ds")]
    overview = {"items": [_item("deepseek-flash", tested={})]}
    r = evaluate(providers, members, overview)
    assert not any("E3" in e for e in r["errors"])
    assert any("W3" in w for w in r["warnings"])

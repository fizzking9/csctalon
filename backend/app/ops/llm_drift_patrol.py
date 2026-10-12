# -*- coding: utf-8 -*-
"""LLM 档位链 × talon 值班/实测能力 漂移巡检（只读）—— #300。

容器内运行（天然 prod 环境, 只读 SELECT + 免密 GET, 无需 --allow-real-db）:

    docker exec csctalon-backend python -m app.ops.llm_drift_patrol
    docker exec csctalon-backend python -m app.ops.llm_drift_patrol --json

## 为什么存在

2026-10-12 talon 值班换防, csctalon 的档位链暴露出三类漂移, 全靠人工
拉 /v1/models 才发现:
  1. 122b 已从 talon 下线, csctalon expert 降级位仍指它（降级即 404/路由错乱）;
  2. 9b 实测 structured_output=fail, 却在 assistant 链（分类打标依赖结构化输出）;
  3. provider code 与底层 model 名不符（macmini-7b 实跑 qwen3.5:9b）。

talon 现已提供免密聚合端点 GET /v1/public/overview（模型清单 + 值班节点 +
14 项实测能力, 公开端点只读不含 IP/密钥）, 本模块把它与 DB 的
llm_providers / llm_role_members 逐日比对, 让漂移在 cron 日志里暴露,
而不是在某次降级静默算错后复盘。

为什么放在 app/ops 而不是 backend/scripts/: 发布流程 --git 模式会把
scripts/ 当开发杂质清理（见 docker-compose.yml 头部纯净性注释）,
只有 app/ 随镜像发布。

## 检查项

ERROR（退出码 1, 必须处理）:
  E1 档位链成员 provider 不存在或 enabled=false（降级直接落空）
  E2 provider.model 不在 talon 模型清单（已下线/改名, 调用会 404）
  E3 链成员该档位必需的实测能力 = fail（assistant/specialist 要 structured_output;
     expert/governance 要 reasoning_multistep; tested 为空不判 E3, 转 W3）
  E4 涉数值档位（specialist/expert）命中 NUMERIC_UNSAFE（MoE 静默算错,
     与 tests/test_model_selection_guard.py 同一条锁）

WARN（仅提示, 不影响退出码）:
  W1 链成员不在值班表 / 值班但冷备(preload=false) / 值班节点不健康
  W2 有非必需能力实测 fail（能力退化的早期信号）
  W3 talon 无该模型实测数据（如云端节点 tested={}）
  W4 provider code 隐含的模型族/尺寸与 model 字段不符（如 *-qwen2.5-7b 实跑 9b）

退出码: 0=无 ERROR（可有 WARN） · 1=存在漂移 ERROR · 2=拉取 talon/DB 失败
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request

# ── 判定常量（与 tests/test_model_selection_guard.py 同源知识）──────────────
# talon 侧模型名（model id, 非 csctalon provider code）
NUMERIC_UNSAFE_MODELS = {"qwen3-coder:30b"}  # MoE: 数值聚合 3/4(偏差2万)、相对时间 1/5
NUMERIC_SENSITIVE_TIERS = ("specialist", "expert")
# 档位必需的实测能力键（talon overview tested.* 取值 pass/fail/缺省）
TIER_REQUIRED_CAPS = {
    "assistant": ("structured_output",),       # 分类打标/语义匹配要结构化输出
    "specialist": ("structured_output",),      # 结构化输出/治理研判
    "expert": ("reasoning_multistep",),        # 长文复杂推理
    "governance": ("reasoning_multistep",),    # 矛盾检测/多步语义研判
}
_OVERVIEW_TIMEOUT_S = 10
_FALLBACK_TALON_BASE = "http://10.147.17.22:4080"


def _norm_model(model: str | None) -> str:
    """归一模型名: 去 :latest tag 噪声并小写。glm-4.7-flash:latest → glm-4.7-flash"""
    if not model:
        return ""
    m = model.strip().lower()
    if m.endswith(":latest"):
        m = m[: -len(":latest")]
    return m


def _index_overview(overview: dict) -> dict[str, dict]:
    """talon overview → {归一模型名: item}。display 名也建索引（裸名/:latest 合并）。"""
    idx: dict[str, dict] = {}
    for item in overview.get("items", []):
        for key in (item.get("model"), item.get("display")):
            if key:
                idx.setdefault(_norm_model(key), item)
    return idx


_FAMILY_RE = re.compile(r"(qwen[\w.]*|gpt-oss|deepseek[\w.-]*|glm[\w.-]*)", re.I)
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b", re.I)


def _code_model_mismatch(code: str, model: str) -> bool | None:
    """W4 启发式: provider code 隐含的模型族或参数尺寸与 model 字段不一致。

    例: code=...-macmini-qwen2.5-7b, model=qwen3.5:9b → 族不同(qwen2.5 vs qwen3.5)
        或尺寸不同(7b vs 9b) → True。任一侧无法解析返回 None(不判)。
    """
    cf, mf = _FAMILY_RE.search(code or ""), _FAMILY_RE.search(model or "")
    cs, ms = _SIZE_RE.search(code or ""), _SIZE_RE.search(model or "")
    if not (cf and mf) and not (cs and ms):
        return None
    fam_diff = bool(cf and mf) and cf.group(1).lower() != mf.group(1).lower()
    size_diff = bool(cs and ms) and cs.group(1) != ms.group(1)
    return fam_diff or size_diff


def evaluate(providers: list[dict], members: list[dict], overview: dict) -> dict:
    """纯函数: 输入 DB 行 + talon overview, 输出 {errors, warnings, chains}。

    providers: [{code, model, enabled, is_cloud, base_url}]
    members:   [{role_key, provider_code, sort_order, enabled}]
    """
    talon = _index_overview(overview)
    prov_by_code = {p["code"]: p for p in providers}
    errors: list[str] = []
    warnings: list[str] = []
    chains: dict[str, list[str]] = {}

    for m in members:
        if not m.get("enabled", True):
            continue
        tier, code = m["role_key"], m["provider_code"]
        chains.setdefault(tier, []).append(code)

        # E1: provider 缺失/禁用
        p = prov_by_code.get(code)
        if p is None:
            errors.append(f"[E1] {tier} 链含不存在的 provider: {code}")
            continue
        if not p.get("enabled"):
            errors.append(f"[E1] {tier} 链 provider 已禁用: {code}")
            continue

        model = p.get("model") or ""
        nmodel = _norm_model(model)
        item = talon.get(nmodel)

        # E2: talon 清单无此模型（已下线/改名 → 路由 404）
        if item is None:
            errors.append(f"[E2] {tier} ← {code} 的模型 {model!r} 不在 talon 清单（已下线/改名?）")
            continue

        tested = item.get("tested") or {}
        duty = item.get("duty")

        # W1: 值班/热驻留状态
        if not duty:
            warnings.append(f"[W1] {tier} ← {code}({model}) 不在 talon 值班表（冷加载或退役路径）")
        else:
            if not duty.get("preload"):
                warnings.append(f"[W1] {tier} ← {code}({model}) 值班但冷备(preload=false), 首调冷加载")
            if not duty.get("duty_healthy"):
                warnings.append(f"[W1] {tier} ← {code}({model}) 值班节点 duty_healthy=false")

        # W3: 无实测数据（云端节点常见）
        if not tested:
            warnings.append(f"[W3] {tier} ← {code}({model}) talon 无实测数据, 能力闸口跳过")
        else:
            # E3: 该档必需能力 fail
            for cap in TIER_REQUIRED_CAPS.get(tier, ()):
                if tested.get(cap) == "fail":
                    errors.append(
                        f"[E3] {tier} ← {code}({model}) 必需能力 {cap}=fail（talon 实测）"
                    )
            # W2: 其它能力 fail
            for cap, verdict in tested.items():
                if verdict == "fail" and cap not in TIER_REQUIRED_CAPS.get(tier, ()):
                    warnings.append(f"[W2] {tier} ← {code}({model}) 非必需能力 {cap}=fail")

        # E4: 涉数值档位命中 MoE 数值不安全模型
        if tier in NUMERIC_SENSITIVE_TIERS and nmodel in NUMERIC_UNSAFE_MODELS:
            errors.append(
                f"[E4] {tier} ← {code}({model}) 命中 NUMERIC_UNSAFE: "
                f"该 MoE 数值聚合实测 3/4、相对时间 1/5, 涉数值档降级会静默算错"
            )

        # W4: code/model 名实不符
        if _code_model_mismatch(code, model):
            warnings.append(f"[W4] {tier} ← provider code {code} 与底层 model {model} 族/尺寸不符")

    return {"errors": errors, "warnings": warnings, "chains": chains}


def fetch_overview(base_url: str) -> dict:
    """拉 talon 免密总览。失败抛异常（调用方转退出码 2）。"""
    url = base_url.rstrip("/") + "/v1/public/overview"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_OVERVIEW_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _load_rows() -> tuple[list[dict], list[dict], str]:
    """从容器环境 DB 读 provider/成员, 返回 (providers, members, talon_base)。"""
    from sqlalchemy import text

    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        providers = [
            {"code": r[0], "model": r[1], "enabled": r[2],
             "is_cloud": r[3], "base_url": r[4]}
            for r in db.execute(text(
                "select code, model, enabled, is_cloud, base_url "
                "from llm_providers")).all()
        ]
        members = [
            {"role_key": r[0], "provider_code": r[1], "sort_order": r[2], "enabled": r[3]}
            for r in db.execute(text(
                "select role_key, provider_code, sort_order, enabled "
                "from llm_role_members order by role_key, sort_order")).all()
        ]
    finally:
        db.close()

    base = next((p["base_url"] for p in providers if p.get("base_url")), _FALLBACK_TALON_BASE)
    base = re.sub(r"/v1/?$", "", base)
    return providers, members, base


def run_patrol(as_json: bool = False) -> int:
    """供 __main__ 与未来管理 API 复用, 返回退出码。"""
    try:
        providers, members, base = _load_rows()
    except Exception as e:  # noqa: BLE001
        print(f"[FATAL] 读取 llm 配置失败: {e}", file=sys.stderr)
        return 2

    try:
        overview = fetch_overview(base)
    except Exception as e:  # noqa: BLE001 巡检自身故障 ≠ 漂移
        print(f"[FATAL] 拉取 talon overview 失败({base}): {e}", file=sys.stderr)
        return 2

    report = evaluate(providers, members, overview)
    report["talon_service"] = overview.get("service")
    report["talon_model_count"] = overview.get("count")

    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"talon={report['talon_service']} 模型数={report['talon_model_count']}")
        for tier in sorted(report["chains"]):
            print(f"  {tier:11s}: {' → '.join(report['chains'][tier])}")
        for w in report["warnings"]:
            print("  " + w)
        if report["errors"]:
            print(f"漂移 {len(report['errors'])} 项:")
            for e in report["errors"]:
                print("  " + e)
            print(f"结论: FAIL（{len(report['errors'])} ERROR / {len(report['warnings'])} WARN）")
        else:
            print(f"结论: PASS（0 ERROR / {len(report['warnings'])} WARN）")

    return 1 if report["errors"] else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="LLM 档位链 × talon 值班/实测 漂移巡检（只读）#300")
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = ap.parse_args(argv)
    return run_patrol(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())

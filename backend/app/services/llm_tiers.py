"""LLM 能力档位（角色）—— 单一来源（SSoT）

背景（2026-09-21）
==================
csctalon 原先把 talon 的内部 provider_code **硬编码在各业务服务里**
（如 `_EXTRACT_PROVIDER_CODE = "talon-gmk-qwen2.5-7b"`），导致三个问题：

1. **换模型必须改业务代码** —— 已实际发生：`ai_semantic_matcher` 用 7b
   判语义匹配，实测 **5/5 全部返回 confidence=0.0**（永远不命中），
   修它必须改 `ai_semantic_matcher.py` 的常量。
2. **模型能力知识散落** —— 没有任何一处能回答"这类任务该用哪个模型"。
3. **每接一个新业务系统都要重写一遍选型** —— 无复用。

本模块把「**业务任务 → 能力档位 → 模型链**」收敛为一处声明。

档位设计（按**任务能力需求**，与 `scheduler.level` 的「紧迫性」正交）
=====================================================================
| 档位 | 定位 | 典型任务 | 取舍 |
|---|---|---|---|
| **助理** assistant | 速度优先 | 分类打标 / 语义匹配 / 摘取排版 / 短摘要 | 非数值、无需深度推理 |
| **专员** specialist | 平衡 | 实时问答 / 报告生成 / 结构化输出 / 治理研判 | 需要准确（尤其数值） |
| **专家** expert | 质量优先 | 长文深度分析 / 跨数据关联 / 复杂推理 / 预生成 | 最复杂的活 |

> ⚠️ **命名冲突提示**：本项目已有两个不同维度的「层级」概念，勿混用：
> - `llm_scheduler.level`（L0/L1/L2）= **任务紧迫性**（后台预生成 / admin / 用户实时）
> - `llm_providers.priority`          = **降级顺序**（数值越小越先尝试）
> - 本模块的 **档位**                 = **任务能力需求**（该找哪个模型）
> 三者正交。

实测依据
=========
见 talon 仓库 `docs/reports/模型能力矩阵与任务分流-20260920.md`（2026-09-20 实测）：

| 模型 | 架构 | decode | 非数值任务 | 数值聚合 | 相对时间解析 |
|---|---|---|---|---|---|
| `qwen3-coder:30b` | MoE 激活 3B | **78 t/s** | ✅ 全对 | ❌ **3/4** | ❌ 1/5 |
| `qwen3.8:27b` | Dense 27B | 26 t/s | ✅ | ✅ **4/4** | ⚠️ 4/5 |
| `qwen3.5:122b` | MoE 激活 8 | 30 t/s | ✅ | ✅ **4/4** | — |
| `qwen2.5:7b` | Dense 7B | 46 t/s | ⚠️ **语义匹配 5/5 全错** | — | — |

**关键结论：MoE 模型有系统性数值缺陷（三轮复现同一错误），数值/财务类任务必须用 Dense。**

兜底机制
=========
每档的 `primary` → `secondary` 之后，**自动接 priority 升序的常规链**
（含云端 `deepseek-v4-flash` 作出网兜底），由 `llm_provider_service.get_provider_chain`
实现。本模块只负责「首选/备选」的声明，**不重写降级逻辑**。

调整档位只需改本文件（或用环境变量覆盖），**不动业务代码**。
"""

import logging
import os

from app.core.config import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# 云端兜底（出网）—— 受 LLM_CLOUD_POLICY 管控
# ─────────────────────────────────────────────────────────────────────
# ⚠️ 2026-09-21 重要变更: 本项**不再无条件加入链尾**。
#
# 原实现把它硬编码进所有档位链，导致本地模型 transient 失败时（实测 27b 曾 30% 超时）
# **静默把 prompt 发往 api.deepseek.com**，而 prompt 含真实客服工单数据 ——
# 与「数据不出内网」的核心定位冲突，且无开关、无审计标记。
#
# 现改为受 settings.LLM_CLOUD_POLICY 管控:
#   never         → 云端从所有链中移除（数据绝对不出域）
#   explicit_only → 仅当调用方显式 include_cloud=True 时才加入（默认）
#   auto          → 保留在链尾（改造前的旧行为，需管理员显式选择）
CLOUD_FALLBACK = "deepseek-v4-flash"

# 出域策略取值（与 settings.LLM_CLOUD_POLICY 对应）
CLOUD_POLICY_NEVER = "never"
CLOUD_POLICY_EXPLICIT = "explicit_only"
CLOUD_POLICY_AUTO = "auto"
_ALLOWED_CLOUD_POLICIES = (CLOUD_POLICY_NEVER, CLOUD_POLICY_EXPLICIT, CLOUD_POLICY_AUTO)


def cloud_policy() -> str:
    """读全局出域策略（非法值回退 explicit_only，保守优先）。"""
    v = (getattr(settings, "LLM_CLOUD_POLICY", "") or "").strip().lower()
    if v not in _ALLOWED_CLOUD_POLICIES:
        if v:
            logger.warning("LLM_CLOUD_POLICY=%r 非法, 回退 explicit_only", v)
        return CLOUD_POLICY_EXPLICIT
    return v


def cloud_allowed(*, explicit: bool = False) -> bool:
    """当前是否允许把云端 provider 加入调用链。

    参数:
      explicit: 调用方是否**显式要求**出域（如用户确认"用云端加速"）。
    """
    policy = cloud_policy()
    if policy == CLOUD_POLICY_NEVER:
        return False
    if policy == CLOUD_POLICY_AUTO:
        return True
    # explicit_only: 只有显式请求才允许
    return bool(explicit)

# ─────────────────────────────────────────────────────────────────────
# 档位定义（单一来源）
# ─────────────────────────────────────────────────────────────────────
# 字段: primary(首选) / secondary(备选) / desc(用途) / rationale(选型依据，实测)
TIERS: dict[str, dict] = {
    "assistant": {
        "primary": "talon-gmk-qwen3-coder-30b",
        "secondary": "talon-gmk-qwen2.5-7b",
        "desc": "助理 —— 速度优先：分类打标 / 语义匹配 / 摘取排版 / 短摘要",
        "rationale": (
            "实测 coder-30b 在语义匹配 6/6 全对、摘取排版数字全对，且 decode 78 t/s（快 3x）。"
            "备选 7b 仅作降级兜底 —— 注意它实测语义匹配 5/5 全错，"
            "故**不可**作为该类任务的首选（原 #145 选型即因此失效）。"
        ),
    },
    "specialist": {
        "primary": "talon-gmk-qwen3.8-27b",
        "secondary": "talon-gmk-qwen3-coder-30b",
        "desc": "专员 —— 平衡：实时问答 / 报告生成 / 结构化输出 / 治理研判",
        "rationale": (
            "实测 27b 数值聚合 4/4、语义 0.95（最稳），Dense 架构无 MoE 的算术缺陷。"
            "备选 coder-30b 快 3x，可承接非数值的降级场景。"
        ),
    },
    "expert": {
        "primary": "talon-gmk-qwen3.5-122b",
        "secondary": "talon-gmk-qwen3.8-27b",
        "desc": "专家 —— 质量优先：长文深度分析 / 跨数据关联 / 复杂推理 / 预生成",
        "rationale": (
            "实测 122b 数值 4/4、prefill 331 t/s（集群最快），是长文的唯一选择。"
            "备选 27b 数值同样 4/4，作为 122b 不可用时的降级。"
        ),
    },
}

# 允许的环境变量覆盖（格式: 档位=provider_code，逗号分隔）
# 例: LLM_TIER_PRIMARY_OVERRIDE=assistant=talon-gmk-qwen2.5-7b
_OVERRIDE_ENV = "LLM_TIER_PRIMARY_OVERRIDE"


def _load_overrides() -> dict[str, str]:
    """读取环境变量覆盖（运维可在不发布代码的情况下临时调整首选）。"""
    raw = os.getenv(_OVERRIDE_ENV, "").strip()
    if not raw:
        return {}
    out: dict[str, str] = {}
    for pair in raw.split(","):
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        k, v = k.strip(), v.strip()
        if k in TIERS and v:
            out[k] = v
    return out


def resolve_tier(tier: str, *, include_cloud: bool = False) -> list[str]:
    """把档位名解析为 provider_code 链（首选 → 备选 → 云端兜底）。

    返回的顺序即尝试顺序；调用方把它交给 `chat_completion(provider_code=[...])`，
    其后会自动接 priority 升序的常规链（见 llm_provider_service.get_provider_chain）。

    参数:
      tier:          "assistant" / "specialist" / "expert"
      include_cloud: 调用方是否**显式要求**可出域。是否真正加入云端仍取决于
                     `settings.LLM_CLOUD_POLICY`（never 时任何情况下都不加）。
                     见 `cloud_allowed()`。

    返回:
      provider_code 列表；档位名非法时返回 []（调用方将走默认链，不抛异常）。
    """
    spec = TIERS.get(tier)
    if not spec:
        logger.warning("未知 LLM 档位=%s, 回退默认链（可选: %s）", tier, list(TIERS))
        return []

    override = _load_overrides().get(tier)
    if override:
        logger.info("LLM 档位 %s 首选被环境变量覆盖: %s", tier, override)

    chain = [override or spec["primary"]]
    if spec.get("secondary"):
        chain.append(spec["secondary"])

    # 云端兜底: 受出域策略管控（不再无条件加入）
    if CLOUD_FALLBACK and CLOUD_FALLBACK not in chain:
        if cloud_allowed(explicit=include_cloud):
            chain.append(CLOUD_FALLBACK)
        else:
            logger.info(
                "档位 %s 未加入云端兜底 %s（策略=%s, 显式请求=%s）",
                tier, CLOUD_FALLBACK, cloud_policy(), include_cloud)
    return chain


def describe() -> dict[str, dict]:
    """返回档位全貌（供管理界面/诊断展示）。"""
    out = {}
    for name, spec in TIERS.items():
        out[name] = {
            "desc": spec["desc"],
            "primary": spec["primary"],
            "secondary": spec.get("secondary"),
            "cloud_fallback": CLOUD_FALLBACK,
            "rationale": spec["rationale"],
            "chain": resolve_tier(name),
        }
    return out

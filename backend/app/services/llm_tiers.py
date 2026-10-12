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

# ── 策略读取缓存（2026-09-21）────────────────────────────────────────
# `cloud_policy()` 在每次 LLM 调用链构造时都会被调用，不宜每次都查库。
# 用短 TTL 进程缓存；`update_cloud_policy()` 会主动失效。
_POLICY_CACHE_TTL_S = 30.0
_policy_cache: dict = {"at": 0.0, "value": None}


def _read_policy_from_db() -> str | None:
    """从 DB 单行表读策略；无行/异常返回 None（调用方回退 .env）。"""
    import time

    from app.core.database import SessionLocal

    now = time.time()
    if _policy_cache["value"] is not None and (now - _policy_cache["at"]) < _POLICY_CACHE_TTL_S:
        return _policy_cache["value"]

    db = None
    try:
        from app.models.llm_cloud_policy import SINGLETON_ID, LlmCloudPolicy
        db = SessionLocal()
        row = db.query(LlmCloudPolicy).filter(LlmCloudPolicy.id == SINGLETON_ID).first()
        val = (row.policy or "").strip().lower() if row else None
        _policy_cache["at"] = now
        _policy_cache["value"] = val          # None 也缓存，避免每调用都查空表
        return val
    except Exception as e:  # noqa: BLE001  查库失败不阻塞业务，回退 .env
        logger.debug("读 DB 出域策略失败(%s), 回退 .env", e)
        return None
    finally:
        if db is not None:
            db.close()


def invalidate_policy_cache() -> None:
    """策略变更后调用（管理界面保存时）。"""
    _policy_cache["at"] = 0.0
    _policy_cache["value"] = None


def cloud_policy() -> str:
    """读全局出域策略。

    优先级（2026-09-21）:
      1. DB 单行表 `llm_cloud_policy`（管理员界面可改，无需重启）
      2. `settings.LLM_CLOUD_POLICY`（.env 兜底；DB 无行时）
      3. 常量默认 explicit_only（保守）

    非法值一律回退 explicit_only（保守优先：不放行，也不过度禁止）。
    """
    v = _read_policy_from_db()
    if v is None:
        v = (getattr(settings, "LLM_CLOUD_POLICY", "") or "").strip().lower()
    if v not in _ALLOWED_CLOUD_POLICIES:
        if v:
            logger.warning("LLM 出域策略=%r 非法, 回退 explicit_only", v)
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
        # ⚠️ 2026-10-12 二轮对齐(#300, 依据 talon /v1/public/overview 实测):
        #   备选由 122b 改为 qwen2.5:14b。
        #   - 122b 已从 talon 下线(capabilities 接口都路由到了 120b);
        #   - 一度用过的 coder-30b(MoE)有实测数值缺陷, 无条件降级链无法甄别任务类型,
        #     会静默产出数字错误的结构化结果(见下 2026-09-21 判例);
        #   - 14b 为 Dense 架构(无 MoE 算术缺陷), talon 实测 14/14 全过(含
        #     strict_instruction_en_math/structured_output), 且热驻 mac-mini,
        #     降级零冷启动; 27b 在 talon 侧已转冷备(preload=false), 首调冷加载,
        #     降级到热的 14b 对实时问答反而更快。
        "secondary": "talon-macmini-qwen2.5-14b",
        "desc": "专员 —— 平衡：实时问答 / 报告生成 / 结构化输出 / 治理研判",
        "rationale": (
            "实测 27b 数值聚合 4/4、语义 0.95（最稳），Dense 架构无 MoE 的算术缺陷; "
            "2026-10-12 起 27b 在 talon 转冷备。备选 14b（Dense, talon 实测 14/14, "
            "热驻 mac-mini）：MoE 的 coder-30b 数值聚合实测 3/4、相对时间解析 1/5，"
            "而本档用途含结构化输出/治理研判 → 降级会静默算错, 不可用。"
        ),
    },
    "expert": {
        # ⚠️ 2026-10-12 值班切换 (#300): primary 由 122b 改为 gpt-oss:120b。
        #   背景: 395(gmk-evo) 值班模型已切 120b（122b preload=false 转冷备）；
        #   实测 decode 38.8 t/s（122b 18~24），数值聚合 4/4 全对（无 MoE 算术缺陷）。
        # ⚠️ 2026-10-12 二轮: 122b 已从 talon 模型清单彻底下线, secondary 改为
        #   qwen2.5:14b（Dense, talon 实测 14/14, 热驻 mac-mini, 零冷启动降级）；
        #   27b 保留链末（冷备, 数值权威, 前两级全挂时兜底）。
        #   ⚠️ 待办: gen_system 报告生成考场尚未对 120b 复测（122b 曾 3/7 幻觉数字），
        #   参照数据积累后重新定档（见 #300）。
        "primary": "talon-gmk-gpt-oss-120b",
        "secondary": "talon-macmini-qwen2.5-14b",
        "desc": "专家 —— 质量优先：长文深度分析 / 跨数据关联 / 复杂推理 / 预生成",
        "rationale": (
            "120b 为 395 值班大模型（MXFP4 MoE 激活 5.1B，38.8 t/s），2026-10-12 talon "
            "实测 14/14 全过（含多步推理/数学指令）；备选 14b（Dense 14/14 热驻 mac-mini），"
            "链末 27b（冷备但数值聚合 4/4 权威）。"
            "⚠️ 2026-09-27 gen_system 考场: 122b 套生成侧铁律做经营报告生成仅 3/7"
            "（weekly 伪造逐日工单明细、13 处无据数字），不如 mini-14b 的 5/7 —— "
            "长文分析能力强 ≠ 遵守 gen_system 数字铁律。预生成模板若 llm_provider_code "
            "指向本档，建议换 mini-14b（慢但稳，适合夜间批）或配套服务端数字白名单校验兜底；"
            "120b 的 gen_system 考场复测待做（#300）。"
        ),
    },
    "governance": {
        "primary": "talon-gmk-qwen3.8-27b",
        # ⚠️ 2026-10-12 二轮(#300): 备选由 7b 改为 14b。
        #   talon 实测 7b reasoning_multistep=fail, 且历史实测语义匹配 5/5 全错
        #   （矛盾检测/误分类识别正是治理任务的核心）; 14b Dense 14/14 且热驻。
        "secondary": "talon-macmini-qwen2.5-14b",
        "desc": "治理 —— 数据质检 / 语义探测 / 采集后分类（不出域）",
        "rationale": (
            "治理任务需要语义理解（矛盾检测、误分类识别），27b 实测语义 0.95 最稳"
            "（2026-10-12 起 talon 冷备, 治理为后台批处理, 冷加载可接受）。"
            "备选 14b（Dense 14/14 热驻）; 7b 多步推理 fail 且语义匹配 5/5 全错, 移出。"
            "角色级 allow_cloud=false：治理数据含真实客服退款记录，绝对不出域。"
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


def resolve_tier(tier: str, *, include_cloud: bool = False,
                 db=None) -> list[str]:
    """把档位名解析为 provider_code 链（首选 → 备选 → 云端兜底）。

    返回的顺序即尝试顺序；调用方把它交给 `chat_completion(provider_code=[...])`，
    其后会自动接 priority 升序的常规链（见 llm_provider_service.get_provider_chain）。

    参数:
      tier:          "assistant" / "specialist" / "expert"
      include_cloud: 调用方是否**显式要求**可出域。是否真正加入云端仍取决于
                     `settings.LLM_CLOUD_POLICY`（never 时任何情况下都不加），
                     以及角色级 `allow_cloud`（2026-09-21 新增，双重管控）。
      db:            可选 Session。传入时**优先读 DB 的角色配置**（管理员可在界面调整）；
                     未传或 DB 无配置时回退本文件的常量（保证首次部署即生效）。

    返回:
      provider_code 列表；档位名非法时返回 []（调用方将走默认链，不抛异常）。
    """
    # ── 优先读 DB（2026-09-21 B2）──
    if db is not None:
        try:
            from_db = _resolve_from_db(db, tier)
            if from_db is not None:
                chain, role_allow_cloud = from_db
                if not chain:
                    logger.warning("DB 中档位 %s 无启用成员, 回退源码常量", tier)
                else:
                    return _append_cloud(chain, tier, include_cloud,
                                         role_allow_cloud=role_allow_cloud)
        except Exception as e:  # noqa: BLE001  查库失败不阻塞业务, 回退常量
            logger.warning("读取 DB 档位配置失败(%s), 回退源码常量", e)

    # ── 回退: 源码常量（首次部署即生效）──
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
    return _append_cloud(chain, tier, include_cloud, role_allow_cloud=None)


def _resolve_from_db(db, tier: str) -> tuple[list[str], bool] | None:
    """从 DB 读角色配置，返回 (provider_code 链, 角色是否允许出域)。

    返回 None 表示 DB 中无该角色配置（调用方回退源码常量）。
    角色 `enabled=false` 时同样返回 None（视为"未配置"，回退默认链）。

    ⚠️ 2026-09-21 改为读**关联表** `llm_role_members`（支持一模型多角色）。
      原用 `llm_providers.role_scope` 单值列，部署后即被实测证伪 ——
      同一模型需同时服务多个角色时（如某模型既是 A 档首选又是 B 档备选），
      单值列下相互覆盖，导致降级到实测已知会错的模型。
      旧列保留为**兜底**（新表无该角色记录时才读），一个版本后删除。
    """
    from app.models.llm_provider import LlmProvider
    from app.models.llm_role_config import LlmRoleConfig
    from app.models.llm_role_member import LlmRoleMember

    role = db.query(LlmRoleConfig).filter(
        LlmRoleConfig.role_key == tier).first()
    if role is None or not role.enabled:
        return None

    # ① 优先: 关联表（多对多，正确支持一模型多角色）
    members = db.query(LlmRoleMember).filter(
        LlmRoleMember.role_key == tier,
        LlmRoleMember.enabled.is_(True),
    ).order_by(LlmRoleMember.sort_order).all()

    chain: list[str] = []
    if members:
        # 只保留 enabled + healthy 的 provider（成员表存的是 code 快照）
        codes = [m.provider_code for m in members]
        alive = {
            p.code for p in db.query(LlmProvider).filter(
                LlmProvider.code.in_(codes),
                LlmProvider.enabled.is_(True),
                LlmProvider.healthy.is_(True),
            ).all()
        }
        chain = [c for c in codes if c in alive]
        if not chain:
            logger.warning("档位 %s 关联表成员均不可用(禁用/不健康), 尝试旧列兜底", tier)

    # ② 兜底: 旧单值列（兼容迁移前配置，一个版本后删除）
    if not chain:
        rows = db.query(LlmProvider).filter(
            LlmProvider.role_scope == tier,
            LlmProvider.enabled.is_(True),
            LlmProvider.healthy.is_(True),
        ).order_by(LlmProvider.role_order, LlmProvider.priority).all()
        chain = [r.code for r in rows]

    return chain, bool(role.allow_cloud)


def _append_cloud(chain: list[str], tier: str, include_cloud: bool,
                  *, role_allow_cloud: bool | None) -> list[str]:
    """按策略决定是否把云端兜底追加到链尾。

    双重管控（2026-09-21）:
      1. 全局 settings.LLM_CLOUD_POLICY（never 时任何情况都不加）
      2. 角色级 allow_cloud（DB 显式 false 时不加；None=未配置, 不阻拦）

    任一为"不允许"即不加 —— 数据边界取交集，保守优先。
    """
    if not CLOUD_FALLBACK or CLOUD_FALLBACK in chain:
        return chain

    if not cloud_allowed(explicit=include_cloud):
        logger.info("档位 %s 未加入云端兜底（全局策略=%s, 显式请求=%s）",
                    tier, cloud_policy(), include_cloud)
        return chain

    # 角色级闸门: DB 显式配置 false 时阻拦（None=未配置则不阻拦）
    if role_allow_cloud is False:
        logger.info("档位 %s 未加入云端兜底（角色级 allow_cloud=false）", tier)
        return chain

    chain.append(CLOUD_FALLBACK)
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

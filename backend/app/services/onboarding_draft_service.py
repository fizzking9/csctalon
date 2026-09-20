"""AI 数据接入助手·起草服务（工单 #156，异步编排 #157）

设计（对应 docs/design/AI数据接入助手设计-20260919.md §五）：
- 输入：样例（≤50 行，脱敏）+ 可选业务提示（table_hint / business_note）。
- 流程：脱敏样例 → 构造提示词（DB 可迭代，v1 seed 于 onboarding_prompts，
  取不到/禁用回退模块级默认）→ chat_completion 结构化 JSON → schema 校验
  （非法整体重试 1 次）→ 校验器 validate_spec → 落 data_source_specs
  (status=draft) + validator_report → record_system_event 留痕
  （category=ai_governance，含 provider/耗时/校验 fail 数）。
- 调度：经 llm_scheduler L1 级别异步执行（admin 手动触发级，不占用户问答）；
  走默认实时链（provider_code=None → 27b），超时 60s，失败整体重试 1 次
  后返回错误，不降级 122b（#131 语义：122b 仅预生成）。
- 原则（用户拍板）：AI 只当「建议者与初稿起草者」，产出全是配置（spec 行），
  不产生解析器/分类器代码 —— 这是「机制而非硬编码」的落点。

#157 异步编排（对应设计 §七「异步起草 202 + 轮询」）：
- `create_draft_placeholder` 预建占位行（status=drafting, code=NULL）→ 路由立即
  返回 202+id（LLM 未跑完时还不知道 AI 建议的 code，故 code 必须可空）；
- `start_draft_task` 在事件循环里起后台任务，任务自开 db session（不依赖请求生命周期）；
- 完成后 `_draft_core` 更新同一行（status=draft + AI 产物）；失败/被抢占
  `_mark_failed` 置 status=failed + error_message，避免行永久停在 drafting。

脱敏规则（对样例值逐值处理，避免真实业务数据进 LLM）：
- 手机号 / 邮箱 / 18 位证件号 → 掩码；字符串 > 60 字符截断（保留头部语义特征）；
- 数值/布尔原样保留（金额语义推断需要数字特征，样例为「分」粒度）。

提示词配置化（#153 反馈闭环思路）：提示词正文存 onboarding_prompts 表，
修改不需要发版；规范原文段（命名前缀/分组/字段角色/去重红线/金额分/时区）
由本服务按代码侧常量 build_spec_rules() 生成注入 —— 不落库，避免双份定义漂移。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.data_source_spec import (
    SPEC_STATUS_DRAFT,
    SPEC_STATUS_DRAFTING,
    SPEC_STATUS_FAILED,
    SPEC_STATUS_REJECTED,
    DataSourceSpec,
)
from app.models.llm_provider import LlmCallLog
from app.models.onboarding_prompt import OnboardingPrompt
from app.services import llm_client
from app.services.data_source_registry import CATEGORY_ORDER
from app.services.onboarding_validator import (
    _ALLOWED_PREFIXES,
    VALID_SEMANTIC_ROLES,
    build_registry_snapshot,
    validate_spec,
)
from app.services.system_event_service import record_system_event

logger = logging.getLogger(__name__)

# ── 起草参数（对应设计 §5.3：超时 60s；样例 ≤50 行）────────────────────
_MAX_SAMPLE_ROWS = 50
_MAX_SAMPLE_VALUE_CHARS = 60  # 字符串样例值截断长度（脱敏）
_MAX_TOKENS = 1500           # 结构化 JSON 输出 token 上限
_TIMEOUT_S = 60.0            # LLM 调用超时（秒）
_SOURCE = "onboarding_draft"  # chat_completion 审计 source + system_event 溯源

# 字段语义角色枚举与去重策略（对齐 validator / 设计 §3.2）
_VALID_PK_STRATEGIES = {"primary_key", "none", "upsert"}
_REQUIRED_SPEC_KEYS = ("code", "name_cn", "category", "sort_order", "pk_strategy")

# 手机号 / 邮箱 / 18 位证件号（脱敏掩码；金额数值不掩码，语义推断需要数字特征）。
# ⚠️ 顺序：长/最特异优先 —— 证件号须先于手机号，否则手机号正则会在证件号里
# 先匹配出 11 位子串（如 110101199001011234 → 19900101123）破坏整体掩码。
_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\d{17}[\dXx]"), "******"),
    (re.compile(r"1[3-9]\d{9}"), "138****0000"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "email@***.com"),
]

# 输出 JSON 结构说明（注入系统提示词；amount 字段必须带 unit=分，R13 红线）
_OUTPUT_SCHEMA = """{
  "spec": {
    "code": "英文蛇形 code，如 bms_order",
    "name_cn": "前缀·中文名，如 订单·支付台账",
    "category": "分组，须命中项目分组枚举",
    "sort_order": 10,
    "time_field": "时间列名或 null",
    "sn_field": "SN 列名或 null",
    "pk_field": "主键列名或 null",
    "pk_strategy": "primary_key | none | upsert",
    "sync_source": "来源标识或 null",
    "description": "业务描述"
  },
  "fields": [
    {
      "field_name": "列名",
      "name_cn": "中文名",
      "data_type": "string | numeric | timestamp | boolean | enum",
      "semantic_role": "dimension | measure | amount | time | person | sn | category",
      "agg_func": "sum | count | avg | none，非聚合列给 null",
      "is_filterable": true,
      "is_pk": false,
      "enum_values": ["可选值列表"] 或 null,
      "time_zone_note": "Asia/Shanghai" 或 null（time 角色必填）,
      "unit": "仅 amount 角色必填，值恒为「分」",
      "confidence": "0.0-1.0，拿不准给 <0.7",
      "evidence": "判定依据，如 列名含 amount 且样例为两位小数正数"
    }
  ],
  "dedup_recommendation": {
    "strategy": "primary_key | none | upsert",
    "fields": ["去重字段"],
    "rationale": "依据（引用去重规范条款）",
    "requires_human_confirm": true
  },
  "risks": ["风险提示"],
  "unknown_fields": ["无法确定语义的列名"],
  "overall_confidence": 0.0-1.0
}"""

# 模块级默认提示词（DB 行取不到/被禁用时的兜底；与 seed 内容镜像）
_DEFAULT_SYSTEM_PROMPT = (
    "你是「数据源接入助手」。用户会给你一份业务数据样例（已脱敏，含表头与若干行数据）"
    "与业务提示，请把它翻译成符合本项目规范的「数据源接入声明」。\n"
    "\n【项目规范原文（必须逐条遵守）】\n{spec_rules}\n"
    "\n【输出 JSON 结构】\n{output_schema}\n"
    "\n【铁律】\n"
    "1. 只做语义推断，不做业务断言（禁止编造「这是对账基准」之类结论）。\n"
    "2. 金额列一律判 semantic_role 为 amount 且 unit 为「分」；拿不准时 confidence 给小于 0.7 而非硬猜。\n"
    "3. code 用英文蛇形命名（如 bms_order）；name_cn 用中文业务词且必须带域前缀（前缀·中文名）。\n"
    "4. 时间列判 semantic_role 为 time 并给出 time_zone_note（默认 Asia/Shanghai）；SN 列判 sn。\n"
    "5. dedup_recommendation 必须给出依据并置 requires_human_confirm 为 true（去重键是红线，AI 永不自决）。\n"
    "6. 拿不准语义的列进 unknown_fields，不进 field_specs 硬猜。"
)
_DEFAULT_USER_PROMPT = (
    "【业务提示】\n"
    "- 表名/来源提示：{table_hint}\n"
    "- 业务背景：{business_note}\n"
    "\n【样例数据（已脱敏，共 {row_count} 行）】\n{samples}\n"
    "\n请按系统提示词要求，只输出 JSON 接入声明，禁止任何解释文字或 markdown 代码块标记。"
)


class DraftError(Exception):
    """起草失败（输入非法 / LLM 失败 / schema 校验不过 / code 冲突）。"""


# ─────────────────────────────────────────────────────────────────────
# 脱敏与样例摘要
# ─────────────────────────────────────────────────────────────────────
def sanitize_value(value: Any) -> Any:
    """单值脱敏：PII 掩码 + 长字符串截断；数值/布尔原样保留。"""
    if value is None or isinstance(value, (int, float, bool)):
        return value
    s = str(value)
    for pattern, repl in _PII_PATTERNS:
        s = pattern.sub(repl, s)
    if len(s) > _MAX_SAMPLE_VALUE_CHARS:
        s = s[: _MAX_SAMPLE_VALUE_CHARS] + "…"
    return s


def sanitize_samples(samples: list[dict], *, max_rows: int = _MAX_SAMPLE_ROWS) -> list[dict]:
    """样例整体脱敏（≤max_rows 行，逐值脱敏）。"""
    return [
        {k: sanitize_value(v) for k, v in (row or {}).items()}
        for row in samples[:max_rows]
    ]


def build_sample_summary(sanitized: list[dict], *, sample_values_per_col: int = 5) -> dict:
    """样例摘要（落库审计 + 前端展示）：列名/类型/行数/每列前 N 个样例值（脱敏）。"""
    if not sanitized:
        return {"row_count": 0, "columns": []}
    columns: dict[str, list] = {}
    for row in sanitized:
        for k, v in row.items():
            columns.setdefault(k, []).append(v)
    return {
        "row_count": len(sanitized),
        "columns": [
            {
                "name": k,
                "sample_type": type(vals[0]).__name__ if vals else "unknown",
                "sample_values": vals[:sample_values_per_col],
            }
            for k, vals in columns.items()
        ],
    }


# ─────────────────────────────────────────────────────────────────────
# 提示词构造（DB 可迭代 + 代码常量注入规范段）
# ─────────────────────────────────────────────────────────────────────
def build_spec_rules() -> str:
    """规范原文注入段：由代码侧常量生成（命名/去重/字段角色/金额/时区）。

    不在提示词里硬编码第二份规范定义 —— 与 onboarding_validator 常量、
    data_source_registry.CATEGORY_ORDER 单源镜像，新增前缀/分组只需改代码常量。
    """
    prefixes = "、".join(_ALLOWED_PREFIXES)
    categories = "、".join(CATEGORY_ORDER)
    roles = "、".join(sorted(VALID_SEMANTIC_ROLES))
    return (
        "1) 命名（数据源命名与分组规范）: name_cn 必须「前缀·中文名」结构，"
        f"前缀限: {prefixes}；name_cn 全树唯一且不得含 code 原文；"
        f"分组须命中: {categories}。\n"
        "2) 去重键红线（数据采集合并去重机制说明）: 去重键错=重复入库；"
        "dedup_recommendation 必须给出依据并置 requires_human_confirm=true，"
        "AI 永不自决去重键。\n"
        f"3) 字段语义角色枚举: {roles}。金额一律 amount 且单位「分」；"
        "时间一律 time 且带 time_zone_note（默认 Asia/Shanghai）。"
    )


def _load_prompt(db: Session, code: str, default: str) -> str:
    """取启用中的提示词模板；无/异常/空 → 模块级默认（兜底不阻塞起草）。"""
    try:
        row = db.execute(
            select(OnboardingPrompt).where(
                OnboardingPrompt.code == code,
                OnboardingPrompt.is_active.is_(True),
            )
        ).scalars().first()
    except Exception as e:  # noqa: BLE001  提示词表查询失败不阻塞（回退默认）
        logger.warning("onboarding_draft 读提示词 %s 失败, 回退默认: %s", code, e)
        return default
    if row is None or not (row.content or "").strip():
        return default
    return row.content


def _safe_format(template: str, **kwargs) -> str:
    """format 占位符失败（如 DB 提示词被改掉占位符）→ 原样返回，不抛 KeyError。"""
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template


def build_messages(
    db: Session, *, table_hint: str | None, business_note: str | None,
    sanitized: list[dict],
) -> list[dict]:
    """构造 [system(角色+铁律+规范+输出结构), user(业务提示+样例)]。"""
    samples_text = json.dumps(sanitized, ensure_ascii=False, indent=1) if sanitized else "（无样例行）"
    system = _safe_format(
        _load_prompt(db, "draft_system", _DEFAULT_SYSTEM_PROMPT),
        spec_rules=build_spec_rules(),
        output_schema=_OUTPUT_SCHEMA,
    )
    user = _safe_format(
        _load_prompt(db, "draft_user", _DEFAULT_USER_PROMPT),
        table_hint=(table_hint or "").strip() or "未提供",
        business_note=(business_note or "").strip() or "未提供",
        row_count=len(sanitized),
        samples=samples_text,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ─────────────────────────────────────────────────────────────────────
# LLM 输出 schema 校验（防幻觉闸门第一道：结构层面）
# ─────────────────────────────────────────────────────────────────────
def _parse_llm_json(text: str) -> dict | None:
    """解析 LLM 输出 JSON（容错 markdown 代码块标记）；非 dict 返回 None。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def validate_llm_output(obj: dict) -> tuple[bool, list[str]]:
    """schema 校验 LLM 输出；返回 (是否合法, 错误列表)。

    覆盖设计 §5.4 第 1 条「spec/fields 必填字段齐全，角色枚举合法」；
    去重键红线（requires_human_confirm）在此即拦截，防止进入校验器前丢失。
    """
    errors: list[str] = []
    spec = obj.get("spec")
    if not isinstance(spec, dict):
        errors.append("spec 缺失或不是对象")
    else:
        for key in _REQUIRED_SPEC_KEYS:
            if spec.get(key) in (None, ""):
                errors.append(f"spec.{key} 缺失或为空")
        if spec.get("pk_strategy") not in _VALID_PK_STRATEGIES:
            errors.append(f"spec.pk_strategy 非法: {spec.get('pk_strategy')}")
    fields = obj.get("fields")
    if not isinstance(fields, list) or not fields:
        errors.append("fields 缺失或为空数组")
    else:
        for i, f in enumerate(fields):
            if not isinstance(f, dict):
                errors.append(f"fields[{i}] 不是对象")
                continue
            if not f.get("field_name"):
                errors.append(f"fields[{i}].field_name 缺失")
            if f.get("semantic_role") not in VALID_SEMANTIC_ROLES:
                errors.append(f"fields[{i}].semantic_role 非法: {f.get('semantic_role')}")
            conf = f.get("confidence")
            if not isinstance(conf, (int, float)) or not (0 <= conf <= 1):
                errors.append(f"fields[{i}].confidence 非法: {conf}")
    dedup = obj.get("dedup_recommendation")
    if not isinstance(dedup, dict):
        errors.append("dedup_recommendation 缺失或不是对象")
    else:
        if not isinstance(dedup.get("fields"), list) or not dedup.get("fields"):
            errors.append("dedup_recommendation.fields 缺失或为空（去重键是红线）")
        if not (dedup.get("rationale") or "").strip():
            errors.append("dedup_recommendation.rationale 缺失（必须给出依据，引用去重规范条款）")
        if dedup.get("requires_human_confirm") is not True:
            errors.append("dedup_recommendation.requires_human_confirm 必须为 true（AI 永不自决去重键）")
    conf = obj.get("overall_confidence")
    if not isinstance(conf, (int, float)) or not (0 <= conf <= 1):
        errors.append("overall_confidence 非法（须 0-1 数值）")
    return (not errors), errors


def _merge_spec(obj: dict) -> dict:
    """LLM 输出 → 校验器入参形态（spec + field_specs + dedup_recommendation 合并）。"""
    spec = dict(obj.get("spec") or {})
    spec["field_specs"] = obj.get("fields") or []
    spec["dedup_recommendation"] = obj.get("dedup_recommendation")
    return spec


# ─────────────────────────────────────────────────────────────────────
# 起草核心（LLM 调用 + 重试 + 校验 + 落库 + 留痕）
# ─────────────────────────────────────────────────────────────────────
def _last_call_log(db: Session) -> dict | None:
    """取本次起草最近一条 LLM 调用审计（provider/model 溯源，system_event 留痕用）。"""
    try:
        row = db.execute(
            select(LlmCallLog).where(LlmCallLog.source == _SOURCE)
            .order_by(LlmCallLog.id.desc()).limit(1)
        ).scalars().first()
    except Exception as e:  # noqa: BLE001  审计查询失败不阻塞
        logger.warning("onboarding_draft 查 LLM 审计失败: %s", e)
        return None
    if row is None:
        return None
    return {"provider_code": row.provider_code, "model": row.model}


async def _llm_draft_call(
    db: Session, messages: list[dict], *, correction: str | None = None,
) -> tuple[str | None, str | None]:
    """调一次 chat_completion（默认实时链 27b，超时 60s）。

    返回 (content, error)。correction 非空时追加「上次输出无效」纠错提示。
    """
    msgs = list(messages)
    if correction:
        msgs.append({
            "role": "user",
            "content": f"【上次输出无效】{correction}。请只输出符合 schema 的 JSON，不要解释。",
        })
    try:
        result = await llm_client.chat_completion(
            msgs,
            temperature=0.2,
            max_tokens=_MAX_TOKENS,
            timeout_s=_TIMEOUT_S,
            db=db,
            source=_SOURCE,
            provider_code=None,  # 默认实时链（27b），不降级 122b（#131 语义）
        )
        return str(result), None
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001  LLM 调用失败由上层重试/报错
        return None, str(e)


def _release_stale_code(db: Session, code: str, keep_id: int | None) -> int | None:
    """处理 AI 建议 code 的占用（#157 生产实测发现的缺陷）。

    AI 对同一份样例往往建议同一个 code，于是会出现两种占用：
    1. **失败/拒绝的旧草稿**（failed/rejected）—— 它没有有效声明，不该占住 code，
       否则「同一样例重试」会永远卡死（AI 每次都给同一个 code）。→ 让其让出 code（置 NULL）。
    2. **有效草稿**（draft/confirmed/applied）—— 真冲突，必须人工处置（改名或先处理旧草稿）。
       → 返回其 id，由调用方抛 DraftError 给出可读提示。

    返回真冲突行 id（None 表示无冲突，可继续落库）。
    """
    q = select(DataSourceSpec).where(DataSourceSpec.code == code)
    if keep_id is not None:
        q = q.where(DataSourceSpec.id != keep_id)
    clash = db.execute(q).scalars().first()
    if clash is None:
        return None
    if clash.status in (SPEC_STATUS_FAILED, SPEC_STATUS_REJECTED):
        logger.info("onboarding_draft: 释放失败草稿 id=%s 占用的 code=%s", clash.id, code)
        clash.code = None
        clash.error_message = (
            (clash.error_message or "") + " [该 code 已被新草稿接管]"
        )[:2000]
        db.flush()
        return None
    return clash.id


async def _draft_core(
    db: Session, *, spec_id: int | None = None, table_hint: str | None,
    business_note: str | None, samples: list[dict], created_by: int | None,
) -> dict:
    """起草核心流程（无调度层，供 draft_spec_async 经 L1 门闸调用）。

    spec_id 非空 → 更新既有占位行（#157 异步编排路径）；
    spec_id 为空 → 新建一行（同步调用 / 测试路径）。
    """
    t0 = time.perf_counter()

    # ── 0. 输入校验（样例非空、每行是对象）──
    if not isinstance(samples, list) or not samples:
        raise DraftError("samples 不能为空（至少一行样例数据）")
    for row in samples:
        if not isinstance(row, dict):
            raise DraftError("samples 每行必须是 JSON 对象（列名→值）")

    # ── 1. 脱敏 + 样例摘要 ──
    sanitized = sanitize_samples(samples)
    summary = build_sample_summary(sanitized)

    # ── 2. LLM 调用 + 重试（非法输出/调用失败均整体重试 1 次）──
    messages = build_messages(db, table_hint=table_hint, business_note=business_note,
                              sanitized=sanitized)
    obj: dict | None = None
    retried = False
    invalid_hint = ""
    llm_err = ""
    for attempt in (1, 2):
        if attempt == 2:
            retried = True
        content, err = await _llm_draft_call(db, messages, correction=invalid_hint)
        if content is None:
            llm_err = err or "未知错误"
            if attempt == 1:
                logger.warning("onboarding_draft LLM 调用失败(第1次, 将重试): %s", llm_err)
                continue
            raise DraftError(f"LLM 调用失败(重试后仍失败): {llm_err}")
        obj = _parse_llm_json(content)
        if obj is None:
            invalid_hint = "LLM 输出无法解析为 JSON（检查是否输出了解释文字或代码块）"
            if attempt == 1:
                continue
            raise DraftError("LLM 输出非法: " + invalid_hint)
        ok, errs = validate_llm_output(obj)
        if ok:
            break
        invalid_hint = "；".join(errs[:5])
        if attempt == 1:
            continue
        raise DraftError(f"LLM 输出 schema 校验不过(重试后仍失败): {invalid_hint}")

    # ── 3. 规范校验器（纯规则闸门，validate_spec 是纯函数）──
    merged = _merge_spec(obj)
    snapshot = build_registry_snapshot(db)
    report = validate_spec(merged, snapshot)

    # ── 4. 落库（status=draft；fail 未清零也落库，人工确认时才必须清零）──
    spec_obj = merged
    # code 唯一性预检（两条路径共用）: 直接 commit 会撞 uq_data_source_specs_code
    # 抛 IntegrityError（错误信息对使用者不可读），故先查、先给出可处置的提示。
    clash_id = _release_stale_code(db, spec_obj["code"], keep_id=spec_id)
    if clash_id is not None:
        raise DraftError(
            f"已存在同 code 草稿: {spec_obj['code']}（id={clash_id}）—— 请先处置旧草稿或调整 code"
        )
    if spec_id is not None:
        # #157 异步路径: 更新既有占位行（POST 时建的 drafting 行）
        spec = db.get(DataSourceSpec, spec_id)
        if spec is None:
            raise DraftError(f"草稿不存在: id={spec_id}")
        spec.code = spec_obj["code"]
        spec.name_cn = spec_obj["name_cn"]
        spec.category = spec_obj.get("category") or None
        spec.sort_order = int(spec_obj.get("sort_order") or 999)
        spec.time_field = spec_obj.get("time_field") or None
        spec.sn_field = spec_obj.get("sn_field") or None
        spec.pk_field = spec_obj.get("pk_field") or None
        spec.pk_strategy = spec_obj.get("pk_strategy") or "primary_key"
        spec.sync_source = spec_obj.get("sync_source") or None
        spec.description = spec_obj.get("description") or None
        spec.status = SPEC_STATUS_DRAFT
        spec.error_message = None
        spec.sample_summary = summary
        spec.field_specs = obj.get("fields") or []
        spec.dedup_recommendation = obj.get("dedup_recommendation")
        spec.validator_report = report
        db.commit()
        db.refresh(spec)
    else:
        # 同步路径: 新建一行
        spec = DataSourceSpec(
            code=spec_obj["code"],
            table_name=(table_hint or "").strip(),  # 物理表名来自输入提示（AI 不负责造表名）
            name_cn=spec_obj["name_cn"],
            category=spec_obj.get("category") or None,
            sort_order=int(spec_obj.get("sort_order") or 999),
            time_field=spec_obj.get("time_field") or None,
            sn_field=spec_obj.get("sn_field") or None,
            pk_field=spec_obj.get("pk_field") or None,
            pk_strategy=spec_obj.get("pk_strategy") or "primary_key",
            sync_source=spec_obj.get("sync_source") or None,
            description=spec_obj.get("description") or None,
            status=SPEC_STATUS_DRAFT,
            sample_summary=summary,
            field_specs=obj.get("fields") or [],
            dedup_recommendation=obj.get("dedup_recommendation"),
            validator_report=report,
            created_by=created_by,
        )
        db.add(spec)
        db.commit()
        db.refresh(spec)

    # ── 5. system_event 留痕（provider/耗时/校验 fail 数）──
    duration_ms = int((time.perf_counter() - t0) * 1000)
    call_log = _last_call_log(db)
    provider = (call_log or {}).get("provider_code") or "default(实时链)"
    fail_count = len(report.get("fail") or [])
    warn_count = len(report.get("warn") or [])
    record_system_event(
        db,
        category="ai_governance",
        action="onboarding_draft",
        title=f"AI 起草数据源接入声明: {spec.name_cn}",
        summary=(
            f"code={spec.code} fail={fail_count} warn={warn_count} "
            f"provider={provider} duration_ms={duration_ms}ms"
        ),
        status="success" if fail_count == 0 else "partial",
        detail=json.dumps({
            "spec_id": spec.id,
            "code": spec.code,
            "provider": provider,
            "model": (call_log or {}).get("model"),
            "duration_ms": duration_ms,
            "fail_count": fail_count,
            "warn_count": warn_count,
            "retried": retried,
        }, ensure_ascii=False),
    )

    logger.info(
        "onboarding_draft 完成 spec_id=%s code=%s fail=%s warn=%s provider=%s duration_ms=%s",
        spec.id, spec.code, fail_count, warn_count, provider, duration_ms,
    )
    return {
        "spec_id": spec.id,
        "code": spec.code,
        "name_cn": spec.name_cn,
        "status": spec.status,
        "validator_report": report,
        "field_specs": spec.field_specs,
        "dedup_recommendation": spec.dedup_recommendation,
        "sample_summary": spec.sample_summary,
        "provider": provider,
        "duration_ms": duration_ms,
        "retried": retried,
    }


# ─────────────────────────────────────────────────────────────────────
# 对外入口（L1 异步编排 + 同步包装）
# ─────────────────────────────────────────────────────────────────────
async def draft_spec_async(
    db: Session, *, spec_id: int | None = None, table_hint: str | None = None,
    business_note: str | None = None, samples: list[dict],
    created_by: int | None = None,
) -> dict:
    """异步起草入口：经 llm_scheduler L1 门闸执行。

    spec_id 非空：更新既有占位行（#157 路由后台任务路径）；为空：新建一行。

    失败（DraftError / 其它异常）→ 写 failed system_event 后重抛；
    被更高优先级（L0 预生成）抢占 → CancelledError 直接上抛，不写失败事件
    （属正常抢占，调用方可提示重试）。
    """
    from app.services.llm_scheduler import acquire, release

    handle = await acquire(level=1, name="onboarding_draft")
    try:
        try:
            return await _draft_core(
                db, spec_id=spec_id, table_hint=table_hint, business_note=business_note,
                samples=samples, created_by=created_by,
            )
        except asyncio.CancelledError:
            raise
        except DraftError as e:
            logger.warning("onboarding_draft 失败(可重试): %s", e)
            # DraftError 可能在 commit 后的写事件路径触发，先 rollback 清掉回滚态残留，
            # 避免 record_system_event 写入时再抛「transaction rolled back」（#175）
            db.rollback()
            record_system_event(
                db, category="ai_governance", action="onboarding_draft",
                title="AI 起草数据源接入声明失败",
                summary=str(e)[:200], status="failed", level="error",
                detail=json.dumps({"error": str(e), "retryable": True}, ensure_ascii=False),
            )
            raise
        except Exception as e:  # noqa: BLE001  未预期异常同样留痕后上抛
            logger.exception("onboarding_draft 异常: %s", e)
            # 未预期异常（多在 commit/flush 时抛）必然使 session 进入需回滚态；
            # 若不先 rollback，record_system_event 二次写入会再抛同源异常致失败事件丢失（#175）
            db.rollback()
            record_system_event(
                db, category="ai_governance", action="onboarding_draft",
                title="AI 起草数据源接入声明异常",
                summary=f"{e.__class__.__name__}: {e}"[:200],
                status="failed", level="error",
                detail=json.dumps({"error": f"{e.__class__.__name__}: {e}"}, ensure_ascii=False),
            )
            raise
    finally:
        await release(handle)


def _mark_failed(db: Session, spec_id: int, message: str) -> None:
    """把草稿行置为 failed + error_message（防占位行永久停在 drafting）。

    复用调用方 session（后台任务自己的 db）；先 rollback 清掉失败事务残留，
    再判断 status 是否仍是 drafting —— 已完成的行（status=draft）不覆盖。
    """
    try:
        db.rollback()
        spec = db.get(DataSourceSpec, spec_id)
        if spec is not None and spec.status == SPEC_STATUS_DRAFTING:
            spec.status = SPEC_STATUS_FAILED
            spec.error_message = message[:2000]
            db.commit()
            logger.warning("onboarding_draft 标记失败 spec_id=%s: %s", spec_id, message[:200])
    except Exception as e:  # noqa: BLE001  失败兜底标记异常不二次影响用户
        logger.error("onboarding_draft 标记失败异常 spec_id=%s: %s", spec_id, e)


def create_draft_placeholder(
    session: Session, *, table_hint: str | None, samples: list[dict],
    created_by: int | None,
) -> DataSourceSpec:
    """预建起草占位行（status=drafting, code=NULL），路由立即返回 202+id。

    输入校验在此同步做：样例格式非法时立即报错（不必等 LLM 跑一遍才失败）；
    脱敏/摘要/校验都在后台任务的 _draft_core 里做，此处不重复。
    """
    if not isinstance(samples, list) or not samples:
        raise DraftError("samples 不能为空（至少一行样例数据）")
    for row in samples:
        if not isinstance(row, dict):
            raise DraftError("samples 每行必须是 JSON 对象（列名→值）")
    spec = DataSourceSpec(
        code=None,  # AI 尚未给出建议 code（PostgreSQL unique 允许多个 NULL）
        table_name=(table_hint or "").strip(),
        name_cn="（AI 起草中）",  # 起草完成后被 AI 建议的 name_cn 覆盖
        status=SPEC_STATUS_DRAFTING,
        sort_order=999,
        pk_strategy="primary_key",
        created_by=created_by,
    )
    session.add(spec)
    session.commit()
    session.refresh(spec)
    return spec


def start_draft_task(
    *, session_factory, spec_id: int, table_hint: str | None = None,
    business_note: str | None = None, samples: list[dict],
    created_by: int | None = None,
) -> None:
    """在事件循环上调度起草后台任务（fire-and-forget）。

    - 任务自开独立 db session（session_factory 生成），不依赖请求生命周期；
    - 正常完成 → _draft_core 更新占位行为 draft；异常 → _mark_failed 兜底置 failed；
    - 被 L0 预生成抢占（CancelledError）→ 标记 failed("已被更高优先级抢占")，前端可重试。
    """
    async def _run() -> None:
        db = session_factory()
        try:
            await draft_spec_async(
                db, spec_id=spec_id, table_hint=table_hint,
                business_note=business_note, samples=samples, created_by=created_by,
            )
        except asyncio.CancelledError:
            _mark_failed(db, spec_id, "起草被更高优先级任务抢占, 请稍后重试")
        except DraftError as e:
            _mark_failed(db, spec_id, str(e))
        except Exception as e:  # noqa: BLE001  未预期异常兜底标记失败
            logger.exception("onboarding_draft 后台任务异常 spec_id=%s: %s", spec_id, e)
            _mark_failed(db, spec_id, f"{e.__class__.__name__}: {e}")
        finally:
            db.close()

    asyncio.get_running_loop().create_task(_run())


def draft_spec(
    db: Session, *, spec_id: int | None = None, table_hint: str | None = None,
    business_note: str | None = None, samples: list[dict],
    created_by: int | None = None,
) -> dict:
    """同步起草入口（worker / 测试 / 非 async 上下文用；同 ai_report_worker 模式）。

    已在事件循环中被调用时（RuntimeError），另起新 loop 跑（不跨线程 db）。
    """
    try:
        return asyncio.run(draft_spec_async(
            db, spec_id=spec_id, table_hint=table_hint, business_note=business_note,
            samples=samples, created_by=created_by,
        ))
    except RuntimeError as e:
        logger.warning("onboarding_draft 已在事件循环中, 换新 loop: %s", e)
        return asyncio.new_event_loop().run_until_complete(draft_spec_async(
            db, spec_id=spec_id, table_hint=table_hint, business_note=business_note,
            samples=samples, created_by=created_by,
        ))

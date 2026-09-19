"""AI 数据接入助手·规范校验器（工单 #155）

纯规则、确定性、无 AI：把 docs/standards/ 下 META 规范的断言变成接入声明
（data_source_specs）起草阶段的**实时拦截** —— fail 未清零禁止人工确认，
warn 需人工逐条备注处置理由后放行。这是防 LLM 幻觉的确定性闸门。

设计（对应 docs/design/AI数据接入助手设计-20260919.md §四）：
- 规则 = 函数清单：每条规则一个 `_rule_*` 函数，签名统一
  `(spec: dict, snapshot: RegistrySnapshot, report: _Report) -> None`，
  新规则只加函数、不碰流程 —— 框架级扩展点。
- 校验报告结构: {pass: [], warn: [], fail: []}，每条含 rule 编号与 detail。
- `validate_spec` 为纯函数（不查库），`build_registry_snapshot` 负责从 DB 取
  当前 registry 快照，二者解耦，便于单测逐规则构造正反用例。

规则编号与依据（R1~R14，见设计 §4.1）：
  R1/R2/R3  code/table_name/name_cn 全树唯一（命名规范 §3.4）
  R4        域前缀白名单（命名规范 §3.2，与 test_data_source_naming.py 镜像）
  R5        name_cn 禁 code 原文（命名规范 §3.2）
  R6        category 命中 CATEGORY_ORDER（命名规范 §二）
  R7        停用/易误用必须显示名标注（命名规范 §3.3）
  R8        去重键红线：upsert 必须给出 dedup 建议（去重规范）
  R9/R10    sn_field / time_field 引用合法性（引擎关联 + 时间字段规范）
  R11       字段语义角色枚举（模型约束）
  R12       低置信字段提示人工核（防幻觉）
  R13       amount 必须标注"分"单位（AGENTS.md 金额单位铁律）
  R14       字段语义角色与现有 registry 冲突检测（语义一致性）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.data_source import DataSource, DataSourceField
from app.services.data_source_registry import CATEGORY_ORDER, build_source_projections

# 字段语义角色白名单（对齐 DataSourceField.semantic_role / SemanticRole 枚举）
VALID_SEMANTIC_ROLES = {"dimension", "measure", "amount", "time", "person", "sn", "category"}

# name_cn 域前缀白名单（`前缀·` 形式）。
# ⚠️ 与 docs/standards/数据源命名与分组规范.md §3.2 及
# backend/tests/test_data_source_naming.py::_ALLOWED_PREFIXES 镜像 —— 三处新增前缀须同步。
_ALLOWED_PREFIXES = (
    "A", "B", "C", "D", "E",  # 经营五路
    "聚合", "视图",  # 物化派生
    "投诉", "工单", "客服", "SCRM",  # 工单客服域
    "订单", "售后",  # 交易售后域
    "设备",  # 基础数据域
    "旧",  # 已停用（与 ⚠️ 警示前缀搭配使用）
)

# 低置信度阈值：confidence < 该值视为需人工重点核
LOW_CONFIDENCE_THRESHOLD = 0.7


@dataclass
class RegistrySnapshot:
    """registry 快照（校验对照物）：真实数据源 + 投影，字段角色汇总。"""

    sources: list[dict] = field(default_factory=list)  # {code, table_name, name_cn, category}
    projections: list[dict] = field(default_factory=list)  # {code, name_cn}
    field_roles: dict[str, set] = field(default_factory=dict)  # field_name -> {role, ...}


@dataclass
class _Report:
    """校验过程容器（内部用），最终序列化为 dict。"""

    passed: list[dict] = field(default_factory=list)
    warned: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"pass": self.passed, "warn": self.warned, "fail": self.failed}


def build_registry_snapshot(db: Session) -> RegistrySnapshot:
    """从 DB 读取当前 registry 快照（数据源 + 字段角色 + 投影）。

    投影不落 data_sources 表（见 build_source_projections），这里从
    DATA_SOURCE_PROJECTIONS 展开，保证 name_cn 唯一性校验覆盖全树。
    """
    snap = RegistrySnapshot()
    sources = db.execute(select(DataSource)).scalars().all()
    for s in sources:
        snap.sources.append({
            "code": s.code,
            "table_name": s.table_name,
            "name_cn": s.name_cn,
            "category": s.category,
        })
    fields = db.execute(select(DataSourceField)).scalars().all()
    for f in fields:
        snap.field_roles.setdefault(f.field_name, set()).add(f.semantic_role)
    snap.projections = [
        {"code": p["code"], "name_cn": p["name_cn"]} for p in build_source_projections()
    ]
    return snap


def _name_prefix(name_cn: str) -> str | None:
    """取 name_cn 的域前缀（剥离 ⚠️ 等警示前缀后，`·` 前的一段）。"""
    stripped = re.sub(r"^[⚠️\s]+", "", name_cn or "")
    if "·" not in stripped:
        return None
    return stripped.split("·", 1)[0]


def _field_map(spec: dict) -> dict:
    """field_specs 按 field_name 索引，便于规则查引用。"""
    return {f["field_name"]: f for f in (spec.get("field_specs") or [])}


# ── 规则实现（每条一个函数，签名统一）──────────────────────────────
def _rule_code_unique(spec, snap, rep):
    code = spec.get("code")
    existing = [s["code"] for s in snap.sources] + [p["code"] for p in snap.projections]
    if code in existing:
        rep.failed.append({"rule": "R1", "detail": f"code '{code}' 与现有注册冲突"})


def _rule_table_unique(spec, snap, rep):
    tbl = spec.get("table_name")
    if tbl in {s["table_name"] for s in snap.sources}:
        rep.failed.append({"rule": "R2", "detail": f"table_name '{tbl}' 已注册（同表双注册会让统计口径分裂）"})


def _rule_name_unique(spec, snap, rep):
    name = spec.get("name_cn")
    existing = {s["name_cn"] for s in snap.sources} | {p["name_cn"] for p in snap.projections}
    if name in existing:
        rep.failed.append({"rule": "R3", "detail": f"name_cn '{name}' 全树已存在（同一名字指向两张表）"})


def _rule_prefix(spec, snap, rep):
    name = spec.get("name_cn") or ""
    prefix = _name_prefix(name)
    if prefix is None:
        rep.failed.append({"rule": "R4", "detail": f"name_cn '{name}' 缺少 `前缀·中文名` 结构"})
    elif prefix not in _ALLOWED_PREFIXES:
        rep.failed.append({"rule": "R4", "detail": f"name_cn '{name}' 前缀 '{prefix}' 不在白名单 {_ALLOWED_PREFIXES}"})


def _rule_code_leak(spec, _snap, rep):
    code = spec.get("code")
    name = spec.get("name_cn") or ""
    if code and re.search(rf"(?<![a-z_]){re.escape(code)}(?![a-z_])", name):
        rep.failed.append({"rule": "R5", "detail": f"name_cn '{name}' 泄露 code 原文 '{code}'"})


def _rule_category(spec, snap, rep):
    cat = spec.get("category")
    if cat not in CATEGORY_ORDER:
        rep.failed.append({"rule": "R6", "detail": f"category '{cat}' 未在 CATEGORY_ORDER 登记 {CATEGORY_ORDER}"})


def _rule_warning_note(spec, snap, rep):
    """停用/口径易误用 → 显示名必须标注（只写 description 不算）。"""
    name = spec.get("name_cn") or ""
    desc = spec.get("description") or ""
    needs_note = any(k in desc for k in ("停用", "作废", "对账基准"))
    if needs_note and not any(k in name for k in ("⚠️", "停用", "作废", "对账基准")):
        rep.warned.append({
            "rule": "R7",
            "detail": f"description 含停用/对账基准字样但显示名 '{name}' 未标注（树上不会点开描述）",
        })


def _rule_dedup_redline(spec, snap, rep):
    """去重红线（R8）：upsert 必须给出 dedup 建议 + 依据。"""
    if spec.get("pk_strategy") != "upsert":
        return
    dedup = spec.get("dedup_recommendation") or {}
    fields = dedup.get("fields") or []
    rationale = (dedup.get("rationale") or "").strip()
    if not fields:
        rep.failed.append({"rule": "R8", "detail": "pk_strategy=upsert 但未给出去重字段（去重键是红线）"})
    if not rationale:
        rep.failed.append({"rule": "R8", "detail": "pk_strategy=upsert 但 dedup 建议未给出依据（须引用去重规范条款）"})
    if dedup.get("requires_human_confirm") is not True:
        rep.failed.append({"rule": "R8", "detail": "dedup 建议须显式置 requires_human_confirm=true（AI 永不自决去重键）"})


def _rule_sn_ref(spec, _snap, rep):
    """sn_field 若声明，须是 field_specs 里的字段（否则引擎无法关联 devices）。"""
    sn = spec.get("sn_field")
    if sn and sn not in _field_map(spec):
        rep.warned.append({"rule": "R9", "detail": f"sn_field '{sn}' 未出现在 field_specs 中，引擎无法关联 devices"})


def _rule_time_zone(spec, snap, rep):
    """time_field 必须带时区说明（数据时间字段使用规范）。"""
    tf = spec.get("time_field")
    if not tf:
        return
    f = _field_map(spec).get(tf)
    if not f or not (f.get("time_zone_note") or f.get("time_zone")):
        rep.warned.append({"rule": "R10", "detail": f"time_field '{tf}' 未声明时区（默认应为 Asia/Shanghai）"})


def _rule_field_roles(spec, snap, rep):
    for f in spec.get("field_specs") or []:
        role = f.get("semantic_role")
        if role not in VALID_SEMANTIC_ROLES:
            rep.failed.append({
                "rule": "R11",
                "detail": f"字段 '{f.get('field_name')}' 角色 '{role}' 非法（须在 {sorted(VALID_SEMANTIC_ROLES)}）",
            })


def _rule_low_confidence(spec, snap, rep):
    for f in spec.get("field_specs") or []:
        conf = f.get("confidence")
        if conf is not None and conf < LOW_CONFIDENCE_THRESHOLD:
            rep.warned.append({
                "rule": "R12",
                "detail": f"字段 '{f.get('field_name')}' confidence={conf} < {LOW_CONFIDENCE_THRESHOLD}，需人工重点核",
            })


def _rule_amount_unit(spec, _snap, rep):
    for f in spec.get("field_specs") or []:
        if f.get("semantic_role") == "amount" and f.get("unit") != "分":
            rep.failed.append({
                "rule": "R13",
                "detail": f"金额字段 '{f.get('field_name')}' 必须标注单位 '分'（AGENTS.md 金额单位铁律）",
            })


def _rule_role_conflict(spec, snap, rep):
    for f in spec.get("field_specs") or []:
        role = f.get("semantic_role")
        name = f.get("field_name")
        known = snap.field_roles.get(name)
        if known and role in known:
            continue
        if known and role not in known:
            rep.warned.append({
                "rule": "R14",
                "detail": f"字段 '{name}' 建议角色 '{role}' 与现有 registry 角色 {sorted(known)} 冲突，需人工核",
            })


# 规则函数清单（新规则 = 在此追加一个 _rule_* 函数，不碰 validate_spec 流程）
_RULES: list[callable] = [
    _rule_code_unique,
    _rule_table_unique,
    _rule_name_unique,
    _rule_prefix,
    _rule_code_leak,
    _rule_category,
    _rule_warning_note,
    _rule_dedup_redline,
    _rule_sn_ref,
    _rule_time_zone,
    _rule_field_roles,
    _rule_low_confidence,
    _rule_amount_unit,
    _rule_role_conflict,
]


def validate_spec(spec: dict, snapshot: RegistrySnapshot) -> dict:
    """校验一份接入声明，返回 {pass: [], warn: [], fail: []}。

    纯函数：不查库、不写库。fail 未清零禁止确认（is_confirmable=False）。
    """
    rep = _Report()
    for rule in _RULES:
        try:
            rule(spec, snapshot, rep)
        except Exception as e:  # 单条规则异常不阻塞其余规则
            rep.failed.append({"rule": getattr(rule, "__name__", "unknown"), "detail": f"规则执行异常: {e}"})
    return rep.to_dict()


def is_confirmable(report: dict) -> bool:
    """fail 未清零 → 不可确认。"""
    return not (report.get("fail") or [])

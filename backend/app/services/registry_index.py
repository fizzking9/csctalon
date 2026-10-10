# -*- coding: utf-8 -*-
"""注册表能力位主表 + 启动引用完整性自检（工单 #210 / 台账 S-P0-2，阶段 A）

背景：采集/展示/分析三套映射人工多写，靠人肉保持同步：
  - ingest_service.DATA_TYPE_REGISTRY   data_type → {channel, dedup_key, label}
  - upsert._runner.UPSERT_DISPATCH      data_type → upsert 函数
  - data_source_registry.DATA_SOURCE_SEEDS  code → {model, name_cn, ...}（数据查看树）
  - analysis_engine._MODEL_REGISTRY     table_name → model（分析视图可分析表）

本模块是**显式汇聚层**（阶段 A：不改任何采集/分析函数体，采集数字前后等价）：
  ① build_registry_index()  从上述四处**动态汇聚**出每个 code 的能力位主表；
  ② CAPABILITY_NOTES        所有「看似失配、实为有意」的例外在此**单一事实定义**；
  ③ validate_registry_integrity()  启动时 fail-fast：任何无登记的失配 = 拒绝启动。

人工裁定记录（#210 评论，2026-10-08 用户拍板「按推荐执行」）：
  ① device_profile / merchant 能入库但不上数据查看树 → 有意不展示（in_view_tree=False）；
  ② _MODEL_REGISTRY 13 张 = 可分析表有意全集；其余 8 张显式登记 analyzable=False；
  ③ SCRM 三表走 scrm 专用同步通道（sync_source=scrm），不经 UPSERT_DISPATCH；
  ④ 指标体系（散在 analytics/today.py、api/v1/metrics.py 等）不纳入本单自检，另单；
  ⑤ 本单只交付阶段 A（汇聚 + 自检）；阶段 B（_TABLE_TYPES/_MODEL_REGISTRY 从主表
     派生、彻底去人工双写）另开工单。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.analysis_engine import _MODEL_REGISTRY
from app.services.data_source_registry import DATA_SOURCE_PROJECTIONS, DATA_SOURCE_SEEDS
from app.services.ingest_service import DATA_TYPE_REGISTRY
from app.services.upsert._runner import UPSERT_DISPATCH


class RegistryIntegrityError(RuntimeError):
    """注册表之间引用失配（启动 fail-fast，禁止静默失配）。"""


# ─────────────────────────────────────────────────────────────────────────────
# 例外的单一事实定义：凡「看似失配、实为有意」的 code 必须在此登记原因。
# validate_registry_integrity() 强制：失配且无登记 = 启动失败；
# 登记了但已无失配（冗余登记）同样报错，防止例外清单腐化。
# ─────────────────────────────────────────────────────────────────────────────
CAPABILITY_NOTES: dict[str, str] = {
    # ── 裁定①：能入库但有意不上数据查看树（不在 DATA_SOURCE_SEEDS）──
    "device_profile": "设备档案：入库供设备 360/内部关联使用，有意不上数据查看树",
    "merchant": "商户主数据：入库供商户号管理页/建链使用，有意不上数据查看树",
    # ── 裁定③：SCRM 专用同步通道（sync_source=scrm），绕开 ingest_table/UPSERT_DISPATCH ──
    "scrm_operator": "SCRM 专用同步通道直写（scrm_sync），不经通用采集；不纳入分析视图",
    "scrm_session": "SCRM 专用同步通道直写（scrm_sync），不经通用采集",
    "scrm_session_transfer": "SCRM 专用同步通道直写（scrm_sync），不经通用采集",
    # ── 系统物化宽表（internal）：由 wide_table_builder 重建，非采集通道 ──
    "cash_flow_wide": "系统物化宽表：由 wide_table_builder 周期性重建，非采集通道、不直接进分析视图",
    "refund_wide": "系统物化宽表：由 wide_table_builder 周期性重建，非采集通道",
    # ── 裁定②：在查看树/可入库，但有意不纳入分析视图（_MODEL_REGISTRY=13 张为有意全集）──
    "shanmao_refund": "不纳入分析视图：C 路金额经 refund_wide/cash 口径聚合，原表不进视图构建器",
    "manual_refund": "不纳入分析视图：手动退款经 refund_wide 宽表口径聚合，原表不进视图构建器",
    "bms_order": "不纳入分析视图：订单台账暂无分析视图需求",
    "device_flow_usage": "不纳入分析视图：设备月流量走专用流量分析，不进视图构建器",
    "device_tag": "不纳入分析视图：标签维度表（标签事实化待 A-20261007-002 B3 实施）",
    "device_tag_binding": "不纳入分析视图：标签打标关系表（标签事实化待 A-20261007-002 B3 实施）",
    "return_tracking": "不纳入分析视图：退货跟踪暂无分析视图需求",
}


@dataclass(frozen=True)
class RegistryEntry:
    """一个数据源 code 的能力位（由四处注册表动态汇聚而成，不写死）。"""

    code: str  # 逻辑 code（SEEDS.code 或仅入库的 data_type）
    table_name: str | None  # 物理表名（= model.__tablename__；model 不可解析时为 None）
    ingest: str | None  # 入库 data_type（= DATA_TYPE_REGISTRY 键）；None = 非通用采集
    channel: str | None  # table / complaint / manual_refund / scrm / internal
    in_dispatch: bool  # 是否在 upsert.UPSERT_DISPATCH
    analyzable: bool  # 是否在 analysis_engine._MODEL_REGISTRY
    internal: bool  # 系统物化宽表（cash_flow_wide / refund_wide）
    in_view_tree: bool  # 是否在数据查看树（DATA_SOURCE_SEEDS）
    note: str = ""  # 例外说明（有意失配必须在 CAPABILITY_NOTES 登记）


def build_registry_index() -> dict[str, RegistryEntry]:
    """从四处注册表动态汇聚能力位主表（code 全集 = SEEDS.code ∪ DATA_TYPE_REGISTRY.keys）。

    本函数纯读四个注册表做笛卡尔汇聚，自身不引入任何新事实；
    所有「例外」事实只能来自 CAPABILITY_NOTES。
    """
    seed_by_code: dict = {s["code"]: s for s in DATA_SOURCE_SEEDS}
    codes = list(dict.fromkeys([*seed_by_code.keys(), *DATA_TYPE_REGISTRY.keys()]))
    index: dict[str, RegistryEntry] = {}
    for code in codes:
        seed = seed_by_code.get(code)
        spec = DATA_TYPE_REGISTRY.get(code)
        model = seed["model"] if seed else None
        table_name = getattr(model, "__tablename__", None) if model else None
        internal = bool(seed.get("internal")) if seed else False
        # channel 推导：通用采集以 DATA_TYPE_REGISTRY 为准；
        # 非采集的 SCRM / internal 宽表以种子 sync_source/internal 标记表达
        if spec is not None:
            channel = spec.channel
        elif internal:
            channel = "internal"
        elif seed and seed.get("sync_source") == "scrm":
            channel = "scrm"
        else:
            channel = None
        index[code] = RegistryEntry(
            code=code,
            table_name=table_name,
            ingest=code if spec is not None else None,
            channel=channel,
            in_dispatch=code in UPSERT_DISPATCH,
            analyzable=table_name in _MODEL_REGISTRY if table_name else False,
            internal=internal,
            in_view_tree=seed is not None,
            note=CAPABILITY_NOTES.get(code, ""),
        )
    return index


def validate_registry_integrity() -> None:
    """启动时引用完整性自检：任何失配都必须有 CAPABILITY_NOTES 登记，否则 fail-fast。

    校验项（对应工单 #210 验收标准）：
      (a) DATA_TYPE_REGISTRY.keys == UPSERT_DISPATCH.keys（通用采集双写防漂移）
      (b) 每个 channel=table/complaint/manual_refund 的 code 必在 UPSERT_DISPATCH
      (c) _MODEL_REGISTRY 双向闭环：其每个表名必能在主表找到且 analyzable=True；
          主表每个 analyzable=True 的表必在 _MODEL_REGISTRY
      (d) SEEDS 每项 model 可解析出 __tablename__；code 唯一；name_cn 全树唯一（含投影）
      (e) 失配例外必须在 CAPABILITY_NOTES 登记；冗余登记（已无失配）同样报错
    """
    errors: list[str] = []
    index = build_registry_index()

    # (a) 通用采集双写一致性
    reg_keys = set(DATA_TYPE_REGISTRY.keys())
    dsp_keys = set(UPSERT_DISPATCH.keys())
    for code in sorted(reg_keys - dsp_keys):
        errors.append(f"DATA_TYPE_REGISTRY 有、UPSERT_DISPATCH 无: {code!r}")
    for code in sorted(dsp_keys - reg_keys):
        errors.append(f"UPSERT_DISPATCH 有、DATA_TYPE_REGISTRY 无: {code!r}")

    # (b) 通用采集通道必须有 upsert 函数
    for e in index.values():
        if e.channel in ("table", "complaint", "manual_refund") and not e.in_dispatch:
            errors.append(f"采集通道 {e.code!r}(channel={e.channel}) 缺 UPSERT_DISPATCH 映射")

    # (c) 分析注册表双向闭环
    tables_in_index = {e.table_name: e for e in index.values() if e.table_name}
    for tname in sorted(set(_MODEL_REGISTRY) - set(tables_in_index)):
        errors.append(f"_MODEL_REGISTRY 表 {tname!r} 在主表中不存在（SEEDS/采集均未注册）")
    for tname, e in tables_in_index.items():
        in_mr = tname in _MODEL_REGISTRY
        if in_mr and not e.analyzable:
            errors.append(f"{e.code!r}({tname}) 在 _MODEL_REGISTRY 但主表 analyzable=False（实现 bug）")
        if not in_mr and e.analyzable:
            errors.append(f"{e.code!r}({tname}) 主表 analyzable=True 但不在 _MODEL_REGISTRY（实现 bug）")

    # (d) SEEDS 可解析性 + 唯一性
    name_seen: dict[str, str] = {}
    for seed in DATA_SOURCE_SEEDS:
        code = seed["code"]
        e = index.get(code)
        if e is None or not e.table_name:
            errors.append(f"SEEDS 项 {code!r} 的 model 无法解析 __tablename__")
        name = seed.get("name_cn")
        if name:
            if name in name_seen:
                errors.append(f"name_cn 重复: {name!r}（{name_seen[name]!r} 与 {code!r}）")
            else:
                name_seen[name] = code
    for proj in DATA_SOURCE_PROJECTIONS:
        pname = proj.get("name_cn")
        if pname and pname in name_seen:
            errors.append(f"投影 name_cn 与真身重复: {pname!r}（真身 {name_seen[pname]!r}）")
        if proj.get("projection_of") not in {s["code"] for s in DATA_SOURCE_SEEDS}:
            errors.append(f"投影 {proj.get('code')!r} 的 projection_of={proj.get('projection_of')!r} 未注册")

    # (e) 失配必须有登记；登记必须对应真实失配（防腐化）
    for e in index.values():
        mismatches: list[str] = []
        if e.ingest and not e.in_view_tree:
            mismatches.append("能入库但不在数据查看树")
        if e.in_view_tree and not e.ingest:
            # 含 internal 物化宽表与 scrm 专用通道（均不经通用采集）
            mismatches.append("在查看树但不经通用采集（无 data_type 注册）")
        if e.in_view_tree and not e.internal and not e.analyzable:
            mismatches.append("非物化表但不纳入分析视图")
        if mismatches and not e.note:
            errors.append(f"{e.code!r} 存在无登记失配: {'; '.join(mismatches)}")
        if e.note and not mismatches:
            errors.append(f"{e.code!r} 在 CAPABILITY_NOTES 有冗余登记（当前无失配，请清理）")

    if errors:
        raise RegistryIntegrityError(
            "注册表引用完整性校验失败（#210，禁止静默失配）:\n  - " + "\n  - ".join(errors)
        )

"""统一入库接口 —— 所有采集入口的唯一通道 (#42, 2026-09-10)

背景(用户 2026-09-10 原则):
  "数据本身不重要, 处理流程更重要… 所有入口走统一入库接口, 便于维护不易出错, 统一返回值"

现状问题:
  - 7 类入口各调各的函数(推送/上传/BMS/共享文档/SCRM…), 清洗逻辑**双份实现**, 返回值
    3 种结构混用(ImportResult / SyncRowResult / dict) → 修一处漏一处。
  - 坏值静默置空、时间解析类型缺失(229 行 1969 坏值)、长数字精度丢失零感知。

本模块提供**单一入口**:
      ingest(db, data_type, df=...|rows=..., mapping=..., source=..., ...) -> IngestResult

  内部固定流水线: ① 归一(读取/清洗由各 data_type 的既有 upsert fn 承担)
                 ② 校验(可选 validator)
                 ③ 去重(各 data_type 的 dedup key, 既有实现)
                 ④ 入库(既有 upsert fn / 专用同步函数)
                 ⑤ 打标/拒收(既有 quality + ingest_rejected_rows)
                 ⑥ 统一 IngestResult

  ⚠️ 本期为**适配层**实现: 不替换既有入库实现(避免一次性大改引入回归), 而是统一
     「入口形状 + 返回值 + 上下文(source/run_id)」, 使 7 类入口改调本函数后:
       - 返回值结构统一(前端/日志/告警统一消费)
       - 入口/来源/批次号可追溯(run_id 贯穿拒收行与日志)
       - 后续把归一/校验收敛到 registry 时, 所有入口自动受益

注册表(单一事实源): DATA_TYPE_REGISTRY —— 每 data_type 声明其入库通道与元信息,
新增数据源只在注册表加一行。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# 统一返回值
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class IngestResult:
    """所有入口、所有 data_type 的统一入库结果(#42)

    字段语义:
      total     收到行数(含被拒/被滤)
      inserted  新增行数
      updated   更新行数(去重键命中)
      deleted   全量对账删除行数(仅 manual_refund 支持)
      skipped   空行/模板行跳过数
      failed    硬失败行数(整行被丢弃, 计数准确)
      rejected  未采纳(落 ingest_rejected_rows, 待人工判定)
      warnings  软告警(字段被清洗等, 行保留; 至多 50 条明细)
      errors    硬失败明细(至多 50 条)
      status    success / partial / failed
    """

    data_type: str
    source: str
    run_id: str
    total: int = 0
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0
    rejected: int = 0
    warnings: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    status: str = "success"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """统一对外结构(供 HTTP 层包 {code:0,message,data}; JSONB 友好)"""
        return {
            "data_type": self.data_type,
            "source": self.source,
            "run_id": self.run_id,
            "status": self.status,
            "total": self.total,
            "inserted": self.inserted,
            "updated": self.updated,
            "deleted": self.deleted,
            "skipped": self.skipped,
            "failed": self.failed,
            "rejected": self.rejected,
            "warnings": self.warnings[:50],
            "errors": self.errors[:50],
            "extra": self.extra,
        }


# ═══════════════════════════════════════════════════════════════════════
# 注册表(单一事实源)
# ═══════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class DataTypeSpec:
    """单个 data_type 的入库规格

    本期字段:
      channel        入库通道: "table"(走 UPSERT_DISPATCH 的表格型)
                     / "complaint"(投诉专用同步: 两条 sheet + SN 关联工单)
                     / "manual_refund"(手动退款专用同步: 含全量对账开关)
      dedup_key      去重键(文档/校验用, 便于统一展示与排查)
      label          中文名(日志/展示)
    """

    channel: str
    dedup_key: str | None = None
    label: str | None = None


# 表格型(走 upsert.UPSERT_DISPATCH)的 18 类 + 两个专用通道
_TABLE_TYPES: dict[str, tuple[str | None, str]] = {
    # data_type: (去重键, 中文名)
    "refund": ("refund_no", "退费单"),
    "wallet": ("business_id", "提现操作"),
    "ticket": ("ticket_no", "服务工单"),
    "device_combo": ("recharge_order_no", "设备套餐"),
    "merchant_refund": ("(trade_time,merchant_order_no)", "商户号现金流"),
    "merchant": ("sub_name", "商户主数据"),
    "cs_record": ("business_id", "智能助手记录"),
    "shanmao_refund": ("refund_no", "闪猫退款"),
    "device_tag": ("tag_name", "设备标签"),
    "device_tag_binding": ("(device_sn,tag_name)", "设备标签打标"),
    "device_flow_usage": ("(device_sn,stat_month)", "设备月流量"),
    "device_profile": ("device_sn", "设备档案"),
    "refund_record_report": ("dedup_hash", "退款记录报表"),
    "bms_order": ("order_no", "BMS订单"),
    "return_tracking": ("order_no", "退货跟踪"),
}

DATA_TYPE_REGISTRY: dict[str, DataTypeSpec] = {
    **{
        k: DataTypeSpec(channel="table", dedup_key=v[0], label=v[1])
        for k, v in _TABLE_TYPES.items()
    },
    # 投诉两表: 专用通道(一个 payload 含两个 sheet, 且需 SN→工单关联)
    "external_complaint": DataTypeSpec(channel="complaint", dedup_key="dedup_hash", label="外部投诉(315)"),
    "card_complaint": DataTypeSpec(channel="complaint", dedup_key="dedup_hash", label="卡商群投诉"),
    # 手动退款: 专用通道(含 full_sync 全量对账开关, 误用会删数据)
    "manual_refund": DataTypeSpec(channel="manual_refund", dedup_key="(登记日期,订单号,设备号,登记人)", label="手动退款"),
}


def get_spec(data_type: str) -> DataTypeSpec:
    """取 data_type 规格; 未注册则抛错(强制新数据源先注册)"""
    spec = DATA_TYPE_REGISTRY.get(data_type)
    if spec is None:
        raise ValueError(
            f"未注册的数据类型: {data_type!r}; 请在 ingest_service.DATA_TYPE_REGISTRY 注册后再入库"
        )
    return spec


# ═══════════════════════════════════════════════════════════════════════
# 统一入口
# ═══════════════════════════════════════════════════════════════════════
def ingest_table(
    db: Session,
    data_type: str,
    *,
    df: pd.DataFrame,
    mapping: dict[str, str],
    source: str,
    source_ref: str | None = None,
    run_id: str | None = None,
) -> IngestResult:
    """表格型统一入库(文件上传 / BMS 网页 / 共享文档 / 设备流量等)

    复用既有 upsert.run_upsert(内含清洗/去重/拒收), 仅统一返回值与上下文。
    """
    from app.services.upsert import run_upsert

    spec = get_spec(data_type)
    rid = run_id or uuid.uuid4().hex[:16]
    res = IngestResult(data_type=data_type, source=source, run_id=rid)
    if source_ref:
        res.extra["source_ref"] = source_ref

    raw = run_upsert(df, data_type, mapping, db)
    res.total = int(getattr(raw, "total", 0) or 0)
    res.inserted = int(getattr(raw, "inserted", 0) or 0)
    res.updated = int(getattr(raw, "updated", 0) or 0)
    res.failed = int(getattr(raw, "failed", 0) or 0)
    res.errors = list(getattr(raw, "errors", []) or [])
    res.warnings = list(getattr(raw, "warnings", []) or [])
    # rejected = 真正落拒收表(可按行可见/判定的)条数, 与 failed 不同义(#42)
    res.rejected = int(getattr(raw, "rejected", 0) or 0)
    res.status = "partial" if res.failed else "success"
    res.extra.setdefault("label", spec.label)
    res.extra.setdefault("dedup_key", spec.dedup_key)
    logger.info(
        f"[ingest] {data_type}<-{source} run={rid}: "
        f"{res.total}(+{res.inserted}/~{res.updated}/!{res.rejected}) {res.status}"
    )
    return res


def ingest_complaints(
    db: Session,
    *,
    payload: dict[str, Any],
    source: str,
    source_file: str | None = None,
    source_ref: str | None = None,
    run_id: str | None = None,
) -> IngestResult:
    """投诉统一入库(315 + 卡商两 sheet 的 payload)

    复用既有 complaint_sync.sync_complaints_from_payload(含清洗/指纹去重/SN关联工单/
    拒收), 仅统一返回值与上下文。
    """
    from app.services.complaint_sync import sync_complaints_from_payload

    rid = run_id or uuid.uuid4().hex[:16]
    res = IngestResult(data_type="external_complaint+card_complaint", source=source, run_id=rid)
    if source_ref:
        res.extra["source_ref"] = source_ref

    stats = sync_complaints_from_payload(db, payload, source_file=source_file, sync_source=source)
    ext = stats.get("external", {}) or {}
    card = stats.get("card", {}) or {}
    res.total = int(ext.get("total", 0)) + int(card.get("total", 0))
    res.inserted = int(ext.get("inserted", 0)) + int(card.get("inserted", 0))
    res.updated = int(ext.get("updated", 0)) + int(card.get("updated", 0))
    res.skipped = int(ext.get("filtered", 0)) + int(card.get("filtered", 0))
    res.failed = int(ext.get("failed", 0)) + int(card.get("failed", 0))
    # rejected: 拒收行数(由同步函数上报; 无则 0, 不伪装成 failed)
    res.rejected = int(stats.get("rejected", 0) or 0)
    # ⚠️ 语义差异: 同步函数返回的 ext/card.warnings 是**计数(int)**,
    #    而 IngestResult.warnings 是**明细列表(list[dict])** —— 不得直接赋值。
    #    计数放 extra(供前端/日志展示), 明细由 validation 提供。
    res.errors = list(ext.get("errors", []) or []) + list(card.get("errors", []) or [])
    res.status = stats.get("status", "success")
    res.extra.update({
        "log_id": stats.get("log_id"),
        # ⚠️ 字段白名单必须与对外契约一致: warnings/errors 是 workbuddy 依赖的字段,
        #    遗漏会让外部工具拿到 undefined(2026-09-10 审查 CRITICAL 修复)
        "external": {
            k: ext.get(k)
            for k in ("total", "inserted", "updated", "failed", "filtered", "matched", "warnings", "errors")
        },
        "card": {
            k: card.get(k)
            for k in ("total", "inserted", "updated", "failed", "filtered", "matched", "warnings", "errors")
        },
        # warnings 计数(与对外契约同源)
        "warnings_count": int(ext.get("warnings", 0) or 0) + int(card.get("warnings", 0) or 0),
        # #42 入库前校验问题(只读检测; 与 errors 严格分离)
        "validation": (stats.get("validation", []) or [])[:50],
        # abc-a: 按处置级别聚合(block=不可修复/优先处理, warn=可修复/常规)
        "validation_severity": stats.get("validation_severity") or {},
    })
    logger.info(
        f"[ingest] complaints<-{source} run={rid}: "
        f"315={ext.get('total')}(+{ext.get('inserted')}/~{ext.get('updated')}) "
        f"卡商={card.get('total')}(+{card.get('inserted')}/~{card.get('updated')}) {res.status}"
    )
    return res


def ingest_manual_refunds(
    db: Session,
    *,
    payload: dict[str, Any],
    source: str,
    source_file: str | None = None,
    source_ref: str | None = None,
    run_id: str | None = None,
) -> IngestResult:
    """手动退款统一入库

    复用既有 manual_refund_sync.sync_manual_refunds_from_payload。
    ⚠️ payload["full_sync"] 由调用方按入口语义显式给出(在线采集必须 False, 见既有教训)。
    """
    from app.services.manual_refund_sync import sync_manual_refunds_from_payload

    rid = run_id or uuid.uuid4().hex[:16]
    res = IngestResult(data_type="manual_refund", source=source, run_id=rid)
    if source_ref:
        res.extra["source_ref"] = source_ref

    stats = sync_manual_refunds_from_payload(db, payload, source_file=source_file, sync_source=source)
    res.total = int(stats.get("total", 0))
    res.inserted = int(stats.get("inserted", 0))
    res.updated = int(stats.get("updated", 0))
    res.deleted = int(stats.get("deleted", 0))
    res.skipped = int(stats.get("filtered", 0))
    res.failed = int(stats.get("failed", 0))
    # rejected: 拒收行数(由同步函数上报; 无则 0, 不伪装成 failed)
    res.rejected = int(stats.get("rejected", 0) or 0)
    res.errors = list(stats.get("errors", []) or [])
    res.status = stats.get("status", "success")
    res.extra.update({"log_id": stats.get("log_id"), "warnings_count": stats.get("warnings", 0)})
    # #42/N-3: 校验问题清单(只读 advisory; 与 errors 严格分离)
    res.extra["validation"] = (stats.get("validation", []) or [])[:50]
    # abc-a: 按处置级别聚合
    res.extra["validation_severity"] = stats.get("validation_severity") or {}
    logger.info(
        f"[ingest] manual_refund<-{source} run={rid}: "
        f"{res.total}(+{res.inserted}/~{res.updated}/-{res.deleted}/!{res.failed}) {res.status}"
    )
    return res

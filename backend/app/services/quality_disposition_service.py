"""质量处置服务(A-20260909-001 LOCKED) - 治理动作 + 规则复用

三层治理闭环的 L2/L3 落地:
- L2 处置动作: 对已入库质量问题行执行 ignore(彻底忽略)/restore(恢复)/backfill_pending(待E回填)
  → 源头打标 quality_status, 下游统计统一排除
- L3 规则复用: 人工处置时沉淀 quality_disposition_rules; 后续同类行命中规则
  → auto 直接执行 / suggest 带建议待人工确认

覆盖表: QUALITY_TABLES(refund/ticket/wallet/shanmao/manual_refund), 均含 quality_status。
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.quality_disposition_rule import QualityDispositionRule
from app.services import quality_check as QC

logger = logging.getLogger(__name__)

# 处置动作常量
DISPOSITION_IGNORE = "ignore"            # 彻底忽略(标 ignored, 从经营排除)
DISPOSITION_RESTORE = "restore"          # 恢复(清除 ignored/dup_residual → 回 pending/空)
DISPOSITION_BACKFILL = "backfill_from_e"  # 按 E 表回填
DISPOSITION_FIX = "fix_field"            # 修正字段
DISPOSITION_CONFIRM = "confirm_ok"       # 确认通过

# 排除状态(经营统计不纳入) —— 与 analytics._D_OTHER_EXCLUDE_QSTATUS 语义对齐并扩展
EXCLUDED_STATES = ("dup_residual", "ignored")

# 各表时间字段(D 侧对账时间用)
_TABLE_TIME_FIELD = {
    "manual_refund": "register_time",
    "refund": "created_at_raw",
    "wallet": "op_time",
    "shanmao": "apply_time",
    "ticket": "register_time",
}


# ─────────────────────────────────────────────────────────────
# L2 处置动作
# ─────────────────────────────────────────────────────────────
def quality_ignore(db, table: str, pk: str | int, operator: str | None = None,
                   reason: str | None = None) -> dict:
    """彻底忽略: quality_status → ignored (从经营排除, 不物理删除, 可恢复)

    dup_residual 是"重复治理专用"; ignored 是通用"人工裁定不纳入"(如无佐证残留/测试单)。
    统计排除: COALESCE(quality_status,'') NOT IN ('dup_residual','ignored')。
    """
    cfg = QC.get_table_config(table)
    r = QC._get_by_pk(db, cfg, pk)
    if (r.quality_status or "") in EXCLUDED_STATES and (r.quality_status or "") == "ignored":
        return {"id": pk, "quality_status": r.quality_status, "message": "已是忽略状态"}
    note_bits = [x for x in ((r.quality_note or "").split(" | ") if r.quality_note else []) if x]
    note_bits.append(f"人工彻底忽略{(':' + reason) if reason else ''} by {operator or '?'}")
    r.quality_status = "ignored"
    r.quality_note = " | ".join(note_bits)[:500]
    db.commit()
    logger.info(f"[quality_disposition] ignore table={table} pk={pk} by={operator}")
    return {"id": pk, "quality_status": "ignored", "message": "已彻底忽略(经营统计将排除)"}


def quality_restore(db, table: str, pk: str | int, operator: str | None = None) -> dict:
    """恢复: 清除 ignored/dup_residual 标记 → 回到待确认(pending)或空(正常)

    恢复后行重新可被编辑/确认/纳入统计。保留 note 历史(追加恢复记录)。
    """
    cfg = QC.get_table_config(table)
    r = QC._get_by_pk(db, cfg, pk)
    old = r.quality_status
    if old not in EXCLUDED_STATES:
        return {"id": pk, "quality_status": old, "message": "非排除状态, 无需恢复"}
    # 恢复到 pending(若原本是残留/重复人工恢复, 需重新判定)或空(原正常)
    r.quality_status = None if old == "ignored" and r.quality_note and "dup" not in (r.quality_note or "") else "pending"
    if old == "dup_residual":
        # dup_residual 恢复 → 回到 pending 人工复核
        r.quality_status = "pending"
    note_bits = [x for x in ((r.quality_note or "").split(" | ") if r.quality_note else []) if x]
    note_bits.append(f"人工恢复(原{old}) by {operator or '?'}")
    r.quality_note = " | ".join(note_bits)[:500]
    db.commit()
    logger.info(f"[quality_disposition] restore table={table} pk={pk} {old}→{r.quality_status}")
    return {"id": pk, "quality_status": r.quality_status, "message": "已恢复"}


def quality_suggestion(db, table: str, pk: str | int) -> dict:
    """单行治理建议: 返回命中规则 + E 匹配提示(质量页展示用)

    规则引擎的 suggest 路径: 不自动改, 仅告知"可按规则#N 处置(命中 n 次)"。
    """
    cfg = QC.get_table_config(table)
    r = QC._get_by_pk(db, cfg, pk)
    suggestions = []
    # 1) 规则命中
    rules = db.scalars(
        select(QualityDispositionRule).where(
            QualityDispositionRule.table_code == table,
            QualityDispositionRule.enabled.is_(True),
        )
    )
    for rule in rules:
        if _row_matches_rule(r, rule):
            suggestions.append({
                "rule_id": rule.id, "name": rule.name,
                "disposition": rule.disposition, "auto_level": rule.auto_level,
                "hit_count": rule.hit_count, "confidence": rule.confidence,
            })
    # 2) E 匹配提示(manual_refund: order_no 能否在 E 找到 → 建议回填)
    e_hint = None
    if table == "manual_refund":
        e_hint = _e_match_hint(db, r)
    return {"suggestions": suggestions, "e_hint": e_hint}


def _row_matches_rule(r, rule) -> bool:
    """行是否命中规则 match_pattern(字段级特征匹配, 简化实现)。

    match_pattern 为 {field: expected} 平铺结构:
      - expected 为 str/int/float → 字段值相等
      - expected 为 dict: {"op": "startswith"/"endswith"/"contains"/"gt"/"lt",
                            "value": x} → 按操作符匹配
      - 特殊键: quality_status(按状态匹配), order_no_prefix(订单前缀)
    本期支持常用算子, 覆盖 A/D 重叠 + 残留 + 前缀类规则即可。
    """
    pattern = rule.match_pattern or {}
    for field, expected in pattern.items():
        val = getattr(r, field, None)
        if expected is None:
            continue
        if isinstance(expected, dict) and "op" in expected:
            op = expected["op"]
            want = expected.get("value")
            if val is None:
                return False
            if op == "startswith":
                if not str(val).startswith(str(want)):
                    return False
            elif op == "endswith":
                if not str(val).endswith(str(want)):
                    return False
            elif op == "contains":
                if str(want) not in str(val or ""):
                    return False
            else:
                return False
        else:
            if field == "quality_status":
                if (r.quality_status or "") != expected:
                    return False
            else:
                if val is None or val != expected:
                    return False
    return True


def _e_match_hint(db, m) -> dict | None:
    """manual_refund 行在 E 表(refund_record_reports)能否匹配 → 回填建议提示。

    复用 reconcile 的 L1(order_no) 优先; 唯一候选 → found; 多候选 → ambiguous;
    无 → none(E 未覆盖/延迟)。
    """
    from app.models.refund_record_report import RefundRecordReport

    if not m.order_no:
        return None
    rows = list(db.scalars(
        select(RefundRecordReport).where(
            RefundRecordReport.platform_order_no == m.order_no.strip(),
            RefundRecordReport.refund_status.in_(("退款成功", "成功", "SUCCESS")),
        )
    ))
    if not rows:
        return {"level": "none", "hint": "E 表无匹配(可能非线上退款/采集未覆盖/延迟补录中)"}
    if len(rows) == 1:
        r = rows[0]
        return {
            "level": "found", "hint": f"E 表唯一匹配 report#{r.id}, 可按其 refund_time 回填 op_time",
            "report_id": r.id, "refund_time": r.refund_time.isoformat() if r.refund_time else None,
        }
    return {"level": "ambiguous", "hint": f"E 表多候选({len(rows)} 条同单子退款), 需人工核对",
            "report_ids": [x.id for x in rows]}


# ─────────────────────────────────────────────────────────────
# L3 规则 CRUD
# ─────────────────────────────────────────────────────────────
def list_rules(db, table_code: str | None = None, enabled: bool | None = None) -> list[dict]:
    q = select(QualityDispositionRule)
    if table_code:
        q = q.where(QualityDispositionRule.table_code == table_code)
    if enabled is not None:
        q = q.where(QualityDispositionRule.enabled.is_(enabled))
    rows = db.scalars(q.order_by(QualityDispositionRule.id.desc()))
    return [_rule_out(r) for r in rows]


def create_rule(db, payload: dict, operator: str | None = None) -> dict:
    """新建治理规则(人工或处置沉淀)"""
    rule = QualityDispositionRule(
        table_code=payload["table_code"],
        trigger_category=payload.get("trigger_category", "dup"),
        name=payload["name"],
        match_pattern=payload.get("match_pattern"),
        disposition=payload.get("disposition", DISPOSITION_IGNORE),
        disposition_params=payload.get("disposition_params"),
        auto_level=payload.get("auto_level", "suggest"),
        confidence=int(payload.get("confidence", 60)),
        note=payload.get("note"),
        created_by=operator,
        updated_by=operator,
        enabled=bool(payload.get("enabled", True)),
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return _rule_out(rule)


def update_rule(db, rule_id: int, payload: dict, operator: str | None = None) -> dict:
    rule = db.get(QualityDispositionRule, rule_id)
    if not rule:
        raise ValueError(f"治理规则不存在: id={rule_id}")
    for f in ("name", "match_pattern", "disposition", "disposition_params",
              "auto_level", "confidence", "note", "enabled", "trigger_category"):
        if f in payload:
            setattr(rule, f, payload[f])
    rule.updated_by = operator
    db.commit()
    return _rule_out(rule)


def delete_rule(db, rule_id: int) -> bool:
    rule = db.get(QualityDispositionRule, rule_id)
    if not rule:
        return False
    db.delete(rule)
    db.commit()
    return True


def apply_rule_suggest(db, rule_id: int, limit: int = 100) -> dict:
    """试匹配: 返回规则在当前表可命中的待处置行预览(不执行)。

    用于 suggest 路径: 管理员查看"这条规则会作用于哪些行"后再决定启用/批量执行。
    """
    rule = db.get(QualityDispositionRule, rule_id)
    if not rule:
        raise ValueError(f"治理规则不存在: id={rule_id}")
    cfg = QC.get_table_config(rule.table_code)
    model = cfg["model"]
    rows = db.scalars(
        select(model).where(
            model.quality_status.is_(None) | model.quality_status.in_(("pending",)),
        ).limit(limit)
    )
    hits = []
    for r in rows:
        if _row_matches_rule(r, rule):
            pk = getattr(r, cfg["pk_field"])
            hits.append({"pk": str(pk), "quality_status": r.quality_status})
    return {"rule_id": rule.id, "table_code": rule.table_code, "hit_count": len(hits), "preview": hits[:50]}


def _rule_out(r: QualityDispositionRule) -> dict:
    return {
        "id": r.id,
        "table_code": r.table_code,
        "trigger_category": r.trigger_category,
        "name": r.name,
        "match_pattern": r.match_pattern,
        "disposition": r.disposition,
        "disposition_params": r.disposition_params,
        "auto_level": r.auto_level,
        "confidence": r.confidence,
        "hit_count": r.hit_count,
        "last_hit_at": r.last_hit_at.isoformat() if r.last_hit_at else None,
        "note": r.note,
        "enabled": r.enabled,
        "created_by": r.created_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def record_hit(db, rule_id: int) -> None:
    """规则命中计数(学习信号)"""
    rule = db.get(QualityDispositionRule, rule_id)
    if rule:
        rule.hit_count = (rule.hit_count or 0) + 1
        rule.last_hit_at = datetime.now(timezone.utc)
        rule.confidence = min(99, (rule.confidence or 60) + 1)


# ─────────────────────────────────────────────────────────────
# A/D 真重复识别(ad_overlap_dup 种子规则, A-20260909-001 §2.8.2)
# 规则: D路(manual_refund)行 同 order_no + A路(已退款)金额一致±0.01
#       + |D侧时间(COALESCE op/register) - A.created_at_raw| ≤ 30天 → 疑似重复
# ─────────────────────────────────────────────────────────────
def scan_ad_overlap_dup(db, limit: int = 200) -> dict:
    """扫描 D 路与 A 路真重复候选(不自动处置, 生成建议清单供质量页/规则批量执行)。

    返回候选行列表(manual_refund 行 + 匹配的 A 退费单信息), 供人工确认后标 dup_residual。
    验证(2026-09-09 生产实测): 非 dup_residual 金额一致 28 行 → ±30 天命中 25 行,
    排除 3 行(乱年份残留, 走残留治理)。
    """
    from app.models.manual_refund import ManualRefund
    from app.models.refund import Refund

    manual_rows = db.scalars(
        select(ManualRefund).where(
            ManualRefund.is_refunded == "是",
            ManualRefund.order_no.isnot(None),
            ManualRefund.quality_status.is_(None) | ManualRefund.quality_status.in_(("pending",)),
        ).limit(limit)
    )
    hits = []
    for m in manual_rows:
        if not m.order_no:
            continue
        # D 侧时间(优先 op_time, 回退 register_time)
        d_time = m.op_time or m.register_time
        if d_time is None:
            continue
        # A 路匹配: 同 order_no + 金额一致 + 时间窗 ≤ 30 天
        a_rows = db.scalars(
            select(Refund).where(
                Refund.platform_order_no == m.order_no.strip(),
                Refund.status == "已退款",
                Refund.refund_amount.isnot(None),
            )
        )
        best = None
        for a in a_rows:
            if abs(float(a.refund_amount or 0) - float(m.refund_amount or 0)) > 0.01:
                continue
            if a.created_at_raw is None:
                continue
            gap = abs((a.created_at_raw - d_time).total_seconds())
            if gap <= 30 * 86400:
                if best is None or gap < best[1]:
                    best = (a, gap)
        if best:
            a, gap_days = best
            hits.append({
                "manual_refund_id": m.id,
                "order_no": m.order_no,
                "d_amount": float(m.refund_amount or 0),
                "d_time": d_time.isoformat() if d_time else None,
                "a_refund_no": a.refund_no,
                "a_amount": float(a.refund_amount or 0),
                "a_created_at": a.created_at_raw.isoformat() if a.created_at_raw else None,
                "gap_days": round(gap_days, 1),
                "quality_status": m.quality_status,
                "suggest": "标 dup_residual 从 D 路排除(A 已计此笔)",
            })
    return {"candidate_count": len(hits), "candidates": hits}

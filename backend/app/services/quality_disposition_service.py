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
def scan_ad_overlap_dup(db, limit: int = 500) -> dict:
    """扫描 D 路与 A 路真重复候选(不自动处置, 生成建议清单供质量页/规则批量执行)。

    单条 SQL(LATERAL 取每 D 行最近匹配 A 行), 全量识别不受 Python 逐行采样影响。
    返回候选行列表(manual_refund 行 + 匹配的 A 退费单信息), 供人工确认后标 dup_residual。
    验证(2026-09-09 生产实测): 非 dup_residual 金额一致 28 行 → ±30 天命中 25 行,
    排除 3 行(乱年份残留, 走残留治理)。
    """
    from sqlalchemy import text

    sql = text("""
        SELECT m.id AS manual_refund_id, m.order_no,
               m.refund_amount AS d_amount,
               COALESCE(m.op_time, m.register_time) AS d_time,
               m.quality_status, m.device_sn,
               a.refund_no AS a_refund_no, a.refund_amount AS a_amount,
               a.created_at_raw AS a_created_at,
               round(abs(extract(epoch FROM (a.created_at_raw - COALESCE(m.op_time, m.register_time))))/86400.0, 1) AS gap_days
        FROM manual_refunds m
        CROSS JOIN LATERAL (
            SELECT a.refund_no, a.refund_amount, a.created_at_raw
            FROM refunds a
            WHERE a.platform_order_no = m.order_no AND a.status = '已退款'
              AND a.refund_amount IS NOT NULL AND a.created_at_raw IS NOT NULL
              AND abs(a.refund_amount - m.refund_amount) <= 0.01
              AND abs(extract(epoch FROM (a.created_at_raw - COALESCE(m.op_time, m.register_time)))) <= 30 * 86400
            ORDER BY abs(extract(epoch FROM (a.created_at_raw - COALESCE(m.op_time, m.register_time))))
            LIMIT 1
        ) a
        WHERE m.is_refunded = '是' AND m.order_no IS NOT NULL
          AND COALESCE(m.op_time, m.register_time) IS NOT NULL
          AND (m.quality_status IS NULL OR m.quality_status = 'pending')
        ORDER BY m.id
        LIMIT :limit
    """)
    rows = db.execute(sql, {"limit": limit}).all()
    hits = [{
        "manual_refund_id": r.manual_refund_id,
        "order_no": r.order_no,
        "d_amount": float(r.d_amount or 0),
        "d_time": r.d_time.isoformat() if r.d_time else None,
        "quality_status": r.quality_status,
        "device_sn": r.device_sn,
        "a_refund_no": r.a_refund_no,
        "a_amount": float(r.a_amount or 0),
        "a_created_at": r.a_created_at.isoformat() if r.a_created_at else None,
        "gap_days": float(r.gap_days or 0),
        "suggest": "标 dup_residual 从 D 路排除(A 已计此笔)",
    } for r in rows]
    return {"candidate_count": len(hits), "candidates": hits}


# ─────────────────────────────────────────────────────────────
# D 路缺 SN 坏副本自动排除 (2026-09-09, 生产实测驱动)
# 背景: manual_refunds 缺 SN 已退款行 987 行中, 975 行(99%)与同 order_no 的
#       带 SN 真实行成对出现 → 登记坏副本(同单重复登记, 金额多为协商全额),
#       真实扣款已由带 SN 的 peer 行 + E 表代表。缺 SN 行回填 SN 会把坏行
#       "洗白"造成 D 路重复; 正确治理 = 标 dup_residual 从经营排除。
#       实测排除 975 行 / ¥190,746.80 (D 路经营对账虚高来源)。
# 自动范围: quality_status IS NULL / pending (未治理, 813 行);
# auto_fixed/auto_filled(157+5) 曾被既有自动修正认定有效 → 仅出建议, 不自动推翻。
# ─────────────────────────────────────────────────────────────
def auto_dispose_d_no_sn_bad_dup(db, limit: int = 2000, include_auto_fixed: bool = False) -> dict:
    """D 路缺 SN 坏副本自动排除(标 dup_residual, 幂等可恢复)。

    判定(缺 SN 登记坏副本):
      1) device_sn 为空, is_refunded='是'(在 D 路经营统计内)
      2) quality_status 未治理(默认 NULL/pending; include_auto_fixed=True 时含 auto_fixed/auto_filled)
      3) 同 order_no 存在另一条带 SN 且已退款的 manual_refund 行(真实行已在统计)
      4) E 表(refund_record_reports)该 order_no 存在成功退款(财务佐证该单确已真实出款)

    处置: quality_status → dup_residual(统计排除, 不物理删除, 可 quality_restore 恢复)。
    溯源: quality_note 追加 '自动排除: 缺SN坏副本 同单<order_no>已由带SN行/E表计, 2026-09-09'。
    """
    from sqlalchemy import text

    from app.models.manual_refund import ManualRefund

    auto_statuses = "('pending')"  # NULL 单独处理
    if include_auto_fixed:
        auto_statuses = "('pending','auto_fixed','auto_filled')"

    sql = text(f"""
        SELECT m.id, m.order_no, m.refund_amount, m.quality_status,
               (SELECT count(*) FROM manual_refunds m2
                 WHERE m2.order_no = m.order_no AND m2.id <> m.id
                   AND m2.is_refunded = '是'
                   AND NOT (m2.device_sn IS NULL OR m2.device_sn = '')) AS peer_sn_cnt,
               (SELECT count(*) FROM refund_record_reports r
                 WHERE r.platform_order_no = m.order_no
                   AND r.refund_status IN ('退款成功','成功','SUCCESS')) AS e_cnt
        FROM manual_refunds m
        WHERE (m.device_sn IS NULL OR m.device_sn = '')
          AND m.is_refunded = '是'
          AND (m.quality_status IS NULL OR m.quality_status IN {auto_statuses})
          AND NOT (COALESCE(m.quality_status,'') IN ('dup_residual','ignored'))
        ORDER BY m.id
        LIMIT :limit
    """)
    rows = db.execute(sql, {"limit": limit}).all()
    disposed = 0
    skipped_insufficient = 0
    samples = []
    for r in rows:
        # 护栏: 必须同单存在带SN已退款peer 且 E 表有成功退款佐证, 否则不动(留人工)
        if not (int(r.peer_sn_cnt or 0) > 0 and int(r.e_cnt or 0) > 0):
            skipped_insufficient += 1
            continue
        m = db.get(ManualRefund, r.id)
        if m is None:
            continue
        old_q = m.quality_status
        note_bits = [x for x in ((m.quality_note or "").split(" | ") if m.quality_note else []) if x]
        note_bits.append(
            f"自动排除: 缺SN坏副本 同单{r.order_no}已由带SN行/E表计 (2026-09-09, peer带SN={r.peer_sn_cnt}, E佐证={r.e_cnt})"
        )
        m.quality_status = "dup_residual"
        m.quality_note = " | ".join(note_bits)[:500]
        disposed += 1
        if len(samples) < 20:
            samples.append({"id": r.id, "order_no": r.order_no, "amount": float(r.refund_amount or 0),
                            "old_status": old_q, "peer_sn_cnt": int(r.peer_sn_cnt or 0), "e_cnt": int(r.e_cnt or 0)})
    db.commit()
    logger.info(f"[quality_disposition] auto_dispose_d_no_sn_bad_dup disposed={disposed} skipped={skipped_insufficient}")
    return {
        "disposed": disposed,
        "skipped_insufficient": skipped_insufficient,
        "scanned": len(rows),
        "note": "已标 dup_residual, 从 D 路经营统计排除(可 restore 恢复)",
        "samples": samples,
    }


# ─────────────────────────────────────────────────────────────
# E 空悬 → 落 D 台账对账回填 (2026-09-09, 用户方案)
# 背景: E(refund_record_reports 商户号真实退款)中存在"A/B/C/D 均未承接"的空悬单,
#       经备注/设备/钱包多维度核实, 其中大量是"系统/自动原路退"(用户侧直退/无钱包/
#       无人工退费单台账), 非客服手工登记漏项。
# 用户决策(2026-09-09): 既然 E 是财务权威, 对账回填 ABCD 是"修复"非"删除"。
#   → 把这些 E 空悬单 INSERT 到 D 路(manual_refunds)台账, 备注"实际对账回填",
#     is_refunded='是' + op_time/register_time=E.refund_time, 使 E 空悬消除、对账闭合;
#     后续若 ABC 路采集/登记补回, 以 ABC 为准(凭 order_no + "实际对账回填"标记识别让位)。
# 幂等: 仅处理 manual_refunds 尚无同 order_no 记录的 E 空悬单(防重复执行/与已有登记撞)。
# 仅对 WXALR(闪猫, 走 C)之外的非 C 系订单落 D; WXALR 空悬由 C 路采集解决不落 D。
# ─────────────────────────────────────────────────────────────
def backfill_e_orphan_to_d(db, start=None, end=None, limit: int = 2000) -> dict:
    """E 空悬单 → D 路台账回填(INSERT manual_refunds, 幂等可追溯)。

    start/end: 北京日期字符串(含), 限定 E refund_time 窗口; 默认不传时取全部。
    每行字段取 E: order_no=platform_order_no, device_sn, refund_amount,
    register_time=op_time=refund_time, merchant_id, is_refunded='是',
    remark 含"实际对账回填"溯源, registrant/refund_operator='系统-对账回填',
    quality_status='auto_filled', backfill_refund_record_report_id=E.id(双向溯源)。
    """
    from sqlalchemy import func, text

    from app.models.manual_refund import ManualRefund
    s_u, e_u = None, None
    if start or end:
        from datetime import datetime, timedelta, timezone

        _tz = timezone(timedelta(hours=8))
        if start:
            s_u = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=_tz).astimezone(timezone.utc)
        if end:
            # end 视为含当天 → 北京次日 0 点
            e_u = (datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)).replace(tzinfo=_tz).astimezone(timezone.utc)

    _win = ""
    params: dict = {"limit": limit}
    if s_u and e_u:
        _win = "AND r.refund_time >= :ts AND r.refund_time < :te"
        params["ts"] = s_u
        params["te"] = e_u
    elif s_u:
        _win = "AND r.refund_time >= :ts"
        params["ts"] = s_u
    elif e_u:
        _win = "AND r.refund_time < :te"
        params["te"] = e_u

    sql = text(f"""
        SELECT r.id, r.platform_order_no AS ono, r.device_sn AS esn, r.merchant_id AS mid,
               r.merchant_name AS mname, r.refund_amount AS amt, r.apply_refund_amount AS order_amt,
               r.refund_time AS rtime, r.product_name AS pname
        FROM refund_record_reports r
        WHERE r.refund_status IN ('退款成功','成功','SUCCESS')
          AND r.platform_order_no NOT LIKE 'WXALR%'
          AND r.platform_order_no IS NOT NULL
          {_win}
          AND NOT EXISTS (SELECT 1 FROM refunds a WHERE a.platform_order_no = r.platform_order_no AND a.status='已退款')
          AND NOT EXISTS (SELECT 1 FROM manual_refunds m WHERE m.order_no = r.platform_order_no)
          AND NOT EXISTS (SELECT 1 FROM shanmao_refunds c WHERE c.order_no = r.platform_order_no AND c.status='已退款')
        ORDER BY r.refund_time
        LIMIT :limit
    """)
    rows = db.execute(sql, params).all()
    inserted = 0
    skipped_existing = 0
    samples = []
    # 同 order_no 可能有多笔 E(同单分次/部分退)→ 聚合成一行落 D(一单一录, 金额累加,
    # 避免撞 manual_refunds 去重唯一键 uq_manual_refunds_dedup, 也符合 D 台账按单登记语义)
    groups: dict[str, dict] = {}
    for r in rows:
        key = str(r.ono)
        if key in groups:
            g = groups[key]
            g["amt"] += float(r.amt or 0)
            g["e_ids"].append(r.id)
        else:
            groups[key] = {"r": r, "amt": float(r.amt or 0), "e_ids": [r.id]}
    for _key, g in groups.items():
        r = g["r"]
        total_amt = g["amt"]
        e_ids = g["e_ids"]
        # 幂等: 双检(并发/事务内避免撞) — SQL 已过滤, 此处防御
        exists = db.scalar(
            select(func.count()).select_from(ManualRefund).where(ManualRefund.order_no == r.ono)
        ) or 0
        if exists:
            skipped_existing += 1
            continue
        e_ids_txt = ",".join(str(x) for x in e_ids)
        note = (
            f"实际对账回填: E路退款记录报表单#{e_ids_txt} 系统/自动原路退无人工台账, 由对账自动归入D路. "
            f"op_time/金额取E(refund_time={r.rtime.isoformat() if r.rtime else '?'}, 同单{len(e_ids)}笔已聚合¥{total_amt:.2f}). "
            "后续ABC采集补回时以ABC为准(凭本备注/order_no让位). 2026-09-09"
        )
        m = ManualRefund(
            register_time=r.rtime,
            device_sn=r.esn,
            order_no=r.ono,
            merchant_id=r.mid,
            order_amount=float(r.order_amt) if r.order_amt is not None else total_amt,
            registrant="系统-对账回填",
            is_refunded="是",
            refund_amount=total_amt,
            refund_operator="系统-对账回填",
            op_time=r.rtime,
            remark=(f"实际对账回填: E路#{e_ids_txt} {r.mname or ''} {r.pname or ''}".strip()),
            quality_status="auto_filled",
            quality_note=note[:500],
            backfill_refund_record_report_id=str(e_ids[0]),
        )
        db.add(m)
        inserted += 1
        if len(samples) < 20:
            samples.append({"e_id": e_ids[0], "order_no": r.ono, "device_sn": r.esn,
                            "amount": round(total_amt, 2), "refund_time": r.rtime.isoformat() if r.rtime else None})
    db.commit()
    logger.info(f"[quality_disposition] backfill_e_orphan_to_d inserted={inserted} skipped_existing={skipped_existing} scanned={len(rows)}")
    return {
        "inserted": inserted,
        "skipped_existing": skipped_existing,
        "scanned": len(rows),
        "note": "E 空悬单已 INSERT 到 D 路台账(auto_filled 可追溯), 经营对账将闭合; 后续 ABC 补回以 ABC 为准",
        "samples": samples,
    }


# ─────────────────────────────────────────────────────────────
# D 路同单同金额重复自动排除 (2026-09-09, 逐单对账驱动)
# 背景: 逐单(订单号粒度)对账发现 D 路大量"同 order_no + 同 refund_amount 双行"
#       = 同一退款被登记两遍(坏副本)。8月实测: 71 组, 可剔 71 行 / ¥11,205.76。
#       典型形态: 一条带SN+quality null(真实原始登记) vs 另一条 SN空/pending/
#       auto_fixed(副本/自动修正行); 偶见同SN双行。
# 规则: 同单同金额多行(已退款, 非dup/ignored) → 保留"带SN 且 q=null/ok"最优行,
#       其余行若满足护栏(SN空 或 q 非 null/ok)才自动标 dup_residual(避免误伤
#       同SN真实分笔); 不满足护栏的留人工。E 佐证(该单 E 只退一笔同额)可增强。
# ─────────────────────────────────────────────────────────────
def auto_dispose_d_dup_same_amount(db, limit: int = 2000) -> dict:
    """D 路同单同金额重复自动排除(标 dup_residual, 幂等可恢复)。

    对同 order_no + 同 refund_amount 的已退款多行(非 dup/ignored):
    - 保留: 带 SN 且 quality_status 为 null/ok 的行(视为原始真实登记)
    - 自动剔(标 dup_residual): 其余行中 SN 空 或 quality_status 为
      pending/auto_fixed/auto_filled 者(副本/自动修正重复行)
    - 留人工: 其余行若带 SN 且 q 为 null/ok(两条都像真实, 可能真实分笔)不自动剔
    幂等: 已 dup_residual/ignored 的行不再处理; 重复执行剩余候选自动跳过。
    """
    from sqlalchemy import text

    from app.models.manual_refund import ManualRefund

    # 候选: 同单同金额出现 >=2 次的行(已退款非排除)
    sql = text("""
        SELECT m.id, m.order_no AS ono, m.refund_amount AS amt,
               COALESCE(m.device_sn, '') AS sn, COALESCE(m.quality_status, 'null') AS q,
               COALESCE(m.op_time, m.register_time) AS dt,
               (SELECT count(*) FROM manual_refunds m2
                 WHERE m2.order_no = m.order_no AND m2.refund_amount = m.refund_amount
                   AND m2.is_refunded = '是'
                   AND (m2.quality_status IS NULL OR m2.quality_status NOT IN ('dup_residual','ignored'))) AS grp_n
        FROM manual_refunds m
        WHERE m.is_refunded = '是' AND m.order_no IS NOT NULL AND m.refund_amount IS NOT NULL
          AND (m.quality_status IS NULL OR m.quality_status NOT IN ('dup_residual','ignored'))
          AND EXISTS (
            SELECT 1 FROM manual_refunds m2
            WHERE m2.order_no = m.order_no AND m2.refund_amount = m.refund_amount
              AND m2.is_refunded = '是' AND m2.id <> m.id
              AND (m2.quality_status IS NULL OR m2.quality_status NOT IN ('dup_residual','ignored'))
          )
        ORDER BY m.order_no, m.refund_amount, m.id
        LIMIT :limit
    """)
    rows = db.execute(sql, {"limit": limit}).all()
    # 按 (order_no, amount) 分组
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r.ono, float(r.amt or 0)), []).append(r)

    disposed = 0
    kept = 0
    left_manual = 0
    samples = []
    for (ono, amt), items in groups.items():
        if len(items) < 2:
            continue

        def score(x):
            s_ = 0
            if x.sn:
                s_ += 8
            if x.q in ("null", "ok"):
                s_ += 4
            elif x.q == "auto_filled":
                s_ += 2
            elif x.q == "auto_fixed":
                s_ += 1
            return s_

        best = max(items, key=score)
        for x in items:
            if x.id == best.id:
                kept += 1
                continue
            # 护栏: 仅自动剔 SN空 或 q 非 null/ok 的副本行; 带SN且q=null/ok 的留人工
            is_copy = (not x.sn) or (x.q not in ("null", "ok"))
            if not is_copy:
                left_manual += 1
                continue
            m = db.get(ManualRefund, x.id)
            if m is None:
                continue
            note_bits = [z for z in ((m.quality_note or "").split(" | ") if m.quality_note else []) if z]
            note_bits.append(
                f"自动排除: D同单同金额重复(order={ono} ¥{amt:g}) 保留带SN行 id={best.id}, 本条为副本 (2026-09-09)"
            )
            m.quality_status = "dup_residual"
            m.quality_note = " | ".join(note_bits)[:500]
            disposed += 1
            if len(samples) < 20:
                samples.append({"id": x.id, "order_no": ono, "amount": amt,
                                "sn": x.sn or None, "q": x.q if x.q != "null" else None,
                                "keep_id": best.id, "keep_sn": best.sn or None})
    db.commit()
    logger.info(f"[quality_disposition] auto_dispose_d_dup_same_amount disposed={disposed} left_manual={left_manual} groups={len(groups)}")
    return {
        "disposed": disposed,
        "kept": kept,
        "left_manual": left_manual,
        "groups": len(groups),
        "note": "已标 dup_residual(副本行), 保留带SN/正常行; 可 restore 恢复",
        "samples": samples,
    }

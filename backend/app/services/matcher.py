"""跨表关联匹配服务

匹配规则按优先级:
1. 退费单.platform_order_no == 商户退款.merchant_order_no  (精确)
2. 退费单.platform_order_no == 工单.order_no  (精确)
3. 退费单.device_sn == 工单.device_sn  (精确)
4. 钱包提现.remark 含退费单号 RF...  (正则提取)
5. 退费单.device_sn == 钱包提现.device_sn  (精确)
6. 退费单.merchant_name == 商户.sub_name  (精确)

每次新数据导入后,对新数据 + 之前未关联数据重跑匹配(持续对齐)

性能优化(2026-08-12):
  旧版对每条记录单独查 _exists_relation (N+1 问题), 15万条 × 6 函数 = 上百万次 SQL,
  导致 post-sync "关联匹配中" 阶段耗时数十分钟。
  新版预加载已有关系到内存 set (1 次批量查询替代 N 次), 批量 add_all 插入新关系。
"""

import logging
import re
import time

from sqlalchemy import column, func, select, table, text
from sqlalchemy.orm import Session

from app.models.merchant import Merchant
from app.models.ticket import ServiceTicket
from app.models.upload import DataMatchRelation

# #338: 匹配服务读 std_all 镜像
_MR = table("std_merchant_refund_all", column("id"), column("merchant_order_no"))

logger = logging.getLogger(__name__)

REFUND_NO_PATTERN = re.compile(r"RF\d{10,}")


def match_all(db: Session, only_unmatched: bool = True, progress_callback=None) -> dict:
    """执行全量匹配,返回各类型新增匹配数及受影响的退费单号集合

    Args:
        db: 数据库会话
        only_unmatched: True=只处理之前未匹配的记录(增量),False=全量重跑
        progress_callback: 可选回调 (step_name: str, current: int, total: int) -> None

    Returns:
        dict:
            - 各标签 key: 新增匹配条数(UI展示用)
            - "_affected_refund_nos": 受影响的退费单号集合(set[str]),用于宽表增量重建
            - "_total_new_relations": 总新增关联条数
    """
    steps = [
        ("退费单↔商户退款(订单号)", _match_refund_merchant_refund),
        ("退费单↔工单(订单号)", _match_refund_ticket_by_order),
        ("退费单↔工单(设备SN)", _match_refund_ticket_by_sn),
        ("退费单↔钱包(备注提取)", _match_refund_wallet_by_remark),
        ("退费单↔钱包(设备SN)", _match_refund_wallet_by_sn),
        ("退费单↔商户(商户名)", _match_refund_merchant),
    ]
    stats: dict = {}
    affected_refund_nos: set[str] = set()
    total_relations = 0
    total_steps = len(steps)
    for i, (label, fn) in enumerate(steps):
        if progress_callback:
            try:
                progress_callback(label, i, total_steps)
            except Exception:
                pass
        logger.info(f"[matcher] {i+1}/{total_steps} {label} ...")
        count, affected = fn(db, only_unmatched)
        stats[label] = count
        total_relations += count
        affected_refund_nos.update(affected)
        logger.info(f"[matcher] {label} 完成, 新增 {count} 条, 影响 {len(affected)} 个退费单")
        # 读写并发友好:匹配步骤之间小睡 100ms,让出 DB 连接资源给前端查询
        if i < total_steps - 1:
            time.sleep(0.1)
    db.commit()
    stats["_affected_refund_nos"] = affected_refund_nos
    stats["_total_new_relations"] = total_relations
    if progress_callback:
        try:
            progress_callback("匹配完成", total_steps, total_steps)
        except Exception:
            pass
    return stats


def _preload_existing_relations(db: Session, src_table: str, dst_table: str, match_type: str) -> set[str]:
    """批量预加载已有关系到内存 set (1 次查询替代 N 次 _exists_relation)"""
    return set(db.scalars(
        select(DataMatchRelation.src_id).where(
            DataMatchRelation.src_table == src_table,
            DataMatchRelation.dst_table == dst_table,
            DataMatchRelation.match_type == match_type,
            DataMatchRelation.status == "matched",
        )
    ))


def _match_refund_merchant_refund(db: Session, only_unmatched: bool) -> tuple[int, set[str]]:
    """退费单.platform_order_no == 商户退款.merchant_order_no"""
    # #334: 读 std_*_all 全量镜像(ods 同构透传), 业务代码零读 ods
    refunds = db.execute(text(
        "SELECT refund_no, platform_order_no FROM std_refund_online_all WHERE platform_order_no IS NOT NULL"
    )).all()
    mr_map = {}
    for mr in db.execute(select(_MR).where(_MR.c.merchant_order_no.isnot(None))).mappings():
        key = (mr.merchant_order_no or "").strip()
        if key:
            mr_map[key] = str(mr.id)

    existing = _preload_existing_relations(db, "refund", "merchant_refund", "order_no") if only_unmatched else set()

    new_rels = []
    affected: set[str] = set()
    for r in refunds:
        key = (r.platform_order_no or "").strip()
        if not key or key not in mr_map:
            continue
        if r.refund_no in existing:
            continue
        new_rels.append(DataMatchRelation(
            src_table="refund", src_id=r.refund_no,
            dst_table="merchant_refund", dst_id=mr_map[key],
            match_type="order_no", confidence=100,
            matched_by="auto", status="matched",
        ))
        existing.add(r.refund_no)
        affected.add(r.refund_no)
    if new_rels:
        db.add_all(new_rels)
    return len(new_rels), affected


def _match_refund_ticket_by_order(db: Session, only_unmatched: bool) -> tuple[int, set[str]]:
    """退费单.platform_order_no == 工单.order_no"""
    # #334: 读 std_*_all 全量镜像(ods 同构透传), 业务代码零读 ods
    refunds = db.execute(text(
        "SELECT refund_no, platform_order_no FROM std_refund_online_all WHERE platform_order_no IS NOT NULL"
    )).all()
    ticket_map = {}
    for t in db.scalars(select(ServiceTicket).where(ServiceTicket.order_no.isnot(None))).all():
        key = (t.order_no or "").strip()
        if key:
            ticket_map[key] = t.ticket_no

    existing = _preload_existing_relations(db, "refund", "ticket", "order_no") if only_unmatched else set()

    new_rels = []
    affected: set[str] = set()
    for r in refunds:
        key = (r.platform_order_no or "").strip()
        if not key or key not in ticket_map:
            continue
        if r.refund_no in existing:
            continue
        new_rels.append(DataMatchRelation(
            src_table="refund", src_id=r.refund_no,
            dst_table="ticket", dst_id=ticket_map[key],
            match_type="order_no", confidence=100,
            matched_by="auto", status="matched",
        ))
        existing.add(r.refund_no)
        affected.add(r.refund_no)
    if new_rels:
        db.add_all(new_rels)
    return len(new_rels), affected


def _match_refund_ticket_by_sn(db: Session, only_unmatched: bool) -> tuple[int, set[str]]:
    """退费单.device_sn == 工单.device_sn"""
    # #334: 读 std_*_all 全量镜像(ods 同构透传), 业务代码零读 ods
    refunds = db.execute(text(
        "SELECT refund_no, device_sn FROM std_refund_online_all WHERE device_sn IS NOT NULL"
    )).all()
    ticket_sn_map = {}
    for t in db.scalars(select(ServiceTicket).where(ServiceTicket.device_sn.isnot(None))).all():
        ticket_sn_map.setdefault(t.device_sn, []).append(t.ticket_no)

    existing = _preload_existing_relations(db, "refund", "ticket", "device_sn") if only_unmatched else set()

    new_rels = []
    affected: set[str] = set()
    for r in refunds:
        if not r.device_sn:
            continue
        if r.refund_no in existing:
            continue
        matched_tns = ticket_sn_map.get(r.device_sn, [])
        if not matched_tns:
            # ⚠️ 2026-09-05: 该退费单的 SN 在本批工单里没有匹配目标,
            #   宽表关联状态没有变化 → 不应计入 affected,否则(仅此一处)
            #   全表有 SN 的退费单都会被算进 affected → 宽表每次都全量重建(~2分钟)。
            #   参考其它 4 个 step: 只有真正新增关系才进 affected。
            continue
        for tn in matched_tns:
            new_rels.append(DataMatchRelation(
                src_table="refund", src_id=r.refund_no,
                dst_table="ticket", dst_id=tn,
                match_type="device_sn", confidence=80,
                matched_by="auto", status="matched",
            ))
        existing.add(r.refund_no)
        affected.add(r.refund_no)
    if new_rels:
        db.add_all(new_rels)
    return len(new_rels), affected


def _match_refund_wallet_by_remark(db: Session, only_unmatched: bool) -> tuple[int, set[str]]:
    """钱包提现.remark 含退费单号 RF..."""
    # #334: 读 std_*_all 全量镜像(ods 同构透传), 业务代码零读 ods
    wallets = db.execute(text(
        "SELECT id, remark FROM std_wallet_flow_all WHERE remark IS NOT NULL"
    )).all()

    existing = _preload_existing_relations(db, "refund", "wallet", "refund_no_in_remark") if only_unmatched else set()

    new_rels = []
    affected: set[str] = set()
    for w in wallets:
        if not w.remark:
            continue
        m = REFUND_NO_PATTERN.search(w.remark)
        if not m:
            continue
        refund_no = m.group(0)
        if refund_no in existing:
            continue
        new_rels.append(DataMatchRelation(
            src_table="refund", src_id=refund_no,
            dst_table="wallet", dst_id=str(w.id),
            match_type="refund_no_in_remark", confidence=100,
            matched_by="auto", status="matched",
        ))
        existing.add(refund_no)
        affected.add(refund_no)
    if new_rels:
        db.add_all(new_rels)
    return len(new_rels), affected


def _match_refund_wallet_by_sn(db: Session, only_unmatched: bool) -> tuple[int, set[str]]:
    """退费单.device_sn == 钱包提现.device_sn"""
    # #334: 读 std_*_all 全量镜像(ods 同构透传), 业务代码零读 ods
    refunds = db.execute(text(
        "SELECT refund_no, device_sn FROM std_refund_online_all WHERE device_sn IS NOT NULL"
    )).all()
    wallet_sn_map = {}
    for w in db.execute(text(
        "SELECT id, device_sn FROM std_wallet_flow_all WHERE device_sn IS NOT NULL"
    )).all():
        wallet_sn_map.setdefault(w.device_sn, []).append(w.id)

    existing = _preload_existing_relations(db, "refund", "wallet", "device_sn") if only_unmatched else set()

    new_rels = []
    affected: set[str] = set()
    for r in refunds:
        if not r.device_sn:
            continue
        if r.refund_no in existing:
            continue
        matched_wids = wallet_sn_map.get(r.device_sn, [])
        if not matched_wids:
            # ⚠️ 2026-09-05: 同 _match_refund_ticket_by_sn —— SN 无匹配钱包时
            #   不计入 affected,避免全表退费单进 affected → 宽表每次全量重建。
            continue
        for wid in matched_wids:
            new_rels.append(DataMatchRelation(
                src_table="refund", src_id=r.refund_no,
                dst_table="wallet", dst_id=str(wid),
                match_type="device_sn", confidence=70,
                matched_by="auto", status="matched",
            ))
        existing.add(r.refund_no)
        affected.add(r.refund_no)
    if new_rels:
        db.add_all(new_rels)
    return len(new_rels), affected


def _match_refund_merchant(db: Session, only_unmatched: bool) -> tuple[int, set[str]]:
    """退费单.merchant_name == 商户.sub_name"""
    # #334: 读 std_*_all 全量镜像(ods 同构透传), 业务代码零读 ods
    refunds = db.execute(text(
        "SELECT refund_no, merchant_name FROM std_refund_online_all WHERE merchant_name IS NOT NULL"
    )).all()
    merchant_map = {m.sub_name: m.id for m in db.scalars(select(Merchant)).all()}

    existing = _preload_existing_relations(db, "refund", "merchant", "merchant_name") if only_unmatched else set()

    new_rels = []
    affected: set[str] = set()
    for r in refunds:
        if not r.merchant_name:
            continue
        if r.refund_no in existing:
            continue
        mid = merchant_map.get(r.merchant_name.strip())
        if mid:
            new_rels.append(DataMatchRelation(
                src_table="refund", src_id=r.refund_no,
                dst_table="merchant", dst_id=str(mid),
                match_type="merchant_name", confidence=100,
                matched_by="auto", status="matched",
            ))
            existing.add(r.refund_no)
            affected.add(r.refund_no)
    if new_rels:
        db.add_all(new_rels)
    return len(new_rels), affected


def get_unmatched_summary(db: Session) -> dict[str, int]:
    """获取各表待关联数据统计"""
    # #334: 读 std_*_all 全量镜像(ods 同构透传), 业务代码零读 ods
    refund_count = db.scalar(text("SELECT count(*) FROM std_refund_online_all")) or 0
    refund_with_mr = db.scalar(
        select(func.count(func.distinct(DataMatchRelation.src_id)))
        .where(
            DataMatchRelation.src_table == "refund",
            DataMatchRelation.dst_table == "merchant_refund",
            DataMatchRelation.status == "matched",
        )
    ) or 0
    refund_with_ticket = db.scalar(
        select(func.count(func.distinct(DataMatchRelation.src_id)))
        .where(
            DataMatchRelation.src_table == "refund",
            DataMatchRelation.dst_table == "ticket",
            DataMatchRelation.status == "matched",
        )
    ) or 0
    return {
        "refund_total": refund_count,
        "refund_without_merchant_refund": max(refund_count - refund_with_mr, 0),
        "refund_without_ticket": max(refund_count - refund_with_ticket, 0),
    }

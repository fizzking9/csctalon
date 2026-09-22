"""退费宽表(物化)构建服务

把 matcher 产出的 DataMatchRelation 关联关系物化到 refund_wide 宽表,
供分析引擎直接查询(引擎禁止 JOIN)。

构建策略(A-20260908-001 LOCKED 精简重构):
- 行范围: 线上+已退款 ∪ 带 wallet 关联 ∪ 设备SN有外部投诉
  (即「最终会进入 E 表退款记录报表的线上退款」的当日即时版 + 审计/客诉辅助行)
- 全量构建(only_refund_nos=None): 只对新行范围内的退费单构建宽表行,
  完成后清理不在范围内的残留行(refund_wide 现存但已不满足新范围 → 物理删除)
- 增量构建(only_refund_nos=[...]): 只为指定退费单重建,用于同步后钩子
  (受影响单不在新行范围内 → 查询不到 → 天然 skip)

同 SN 多工单选择规则:优先 order_no 命中的工单,否则取最新 finish_time 的工单。
同 SN 多笔钱包取最新 op_time 的钱包。
外部投诉按 device_sn 聚合为 count + latest_time,与 matcher 关系无关(直接查 external_complaints 表)。
注: merchant_refund(mr_*) 关联已不再物化(零消费死字段, 见 A-20260908-001), 本模块不再拉取。
"""

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete
from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.external_complaint import ExternalComplaint
from app.models.refund import Refund
from app.models.refund_wide import RefundWide
from app.models.ticket import ServiceTicket
from app.models.upload import DataMatchRelation
from app.models.wallet import WalletWithdrawal

logger = logging.getLogger(__name__)

# tz-aware 最小时间,用作排序兜底(None 排到最后)
_TZ_MIN = datetime.min.replace(tzinfo=timezone.utc)

# upsert 时需更新的字段(refund_no 是冲突键,不更新)
# 注: A-20260908-001 已删除 mr_*(wx_refund_no/mr_trade_time/...) 与 ticket_finish_time
_WIDE_UPDATE_FIELDS = [
    "refund_method", "status", "refund_reason", "operator", "auditor", "device_sn",
    "refund_amount", "created_at_raw",
    "ticket_no", "service_item", "service_category",
    "ticket_handle_duration", "ticket_handler",
    "wallet_id", "wallet_op_time", "wallet_op_amount",
    "has_external_complaint", "external_complaint_count", "external_complaint_latest_time",
    "matched_at",
]


# 分批大小:每批处理的退费单数量(避免全量加载 15万+ ORM 对象导致 OOM)
_BATCH_SIZE = 5000


def _in_scope_refund_nos(db: Session) -> set[str]:
    """返回新行范围内的退费单号集合(A-20260908-001 §2.2 方案 B)。

    范围 = 线上+已退款 ∪ 带 wallet 关联(refund↔wallet matched) ∪ 设备SN有外部投诉
    - 线上+已退款: refunds.refund_method='线上' AND status='已退款' (主体, 与 E 表一一对应)
    - 带 wallet 关联: data_match_relations dst_table='wallet' matched (视图5 提现时序审计)
    - 外部投诉: refunds.device_sn ∈ external_complaints.device_sn (分类规则1 客诉覆盖)
    """
    # 1. 线上+已退款(主体)
    main = set(db.scalars(
        select(Refund.refund_no).where(
            Refund.refund_method == "线上",
            Refund.status == "已退款",
        )
    ))
    # 2. 带 wallet 关联
    wallet_nos = set(db.scalars(
        select(DataMatchRelation.src_id).where(
            DataMatchRelation.src_table == "refund",
            DataMatchRelation.dst_table == "wallet",
            DataMatchRelation.status == "matched",
        )
    ))
    # 3. 设备SN有外部投诉
    ec_sns = set(db.scalars(
        select(ExternalComplaint.device_sn).where(ExternalComplaint.device_sn.isnot(None))
    ))
    ec_nos: set[str] = set()
    if ec_sns:
        ec_nos = set(db.scalars(
            select(Refund.refund_no).where(Refund.device_sn.in_(list(ec_sns)))
        ))
    return main | wallet_nos | ec_nos


def _scope_refund_no_subquery():
    """返回行范围退费单号的 SQL 子查询(用于残留清理 NOT EXISTS)。

    与 _in_scope_refund_nos 等价, 但以子查询形式表达(避免把数万参数塞进
    NOT IN 参数列表, 触发 PG 65,535 绑定参数上限 — 见 A-20260908-001 §4.2)。
    """
    main = select(Refund.refund_no).where(
        Refund.refund_method == "线上",
        Refund.status == "已退款",
    )
    wallet = select(DataMatchRelation.src_id).where(
        DataMatchRelation.src_table == "refund",
        DataMatchRelation.dst_table == "wallet",
        DataMatchRelation.status == "matched",
    )
    ec_refund = select(Refund.refund_no).where(
        Refund.device_sn.in_(
            select(ExternalComplaint.device_sn).where(ExternalComplaint.device_sn.isnot(None))
        )
    )
    return main.union(wallet, ec_refund).subquery()


def build_refund_wide(db: Session, only_refund_nos: list[str] | None = None, progress_callback=None) -> dict:
    """构建 refund_wide 物化宽表(分批处理,避免 OOM)

    Args:
        db: 数据库会话(函数内不 commit,由调用方决定;但每批 upsert 后会 commit)
        only_refund_nos: 指定退费单号列表(增量构建);None=全量构建(行范围内)
        progress_callback: 可选回调 (built: int, total: int) -> None, 每批完成时调用

    Returns:
        {"built": int, "updated": int, "skipped": int, "deleted": int}
    """
    # 1. 确定要处理的退费单号集合(仅加载字符串,内存占用极小)
    if only_refund_nos:
        target_refund_nos = list(dict.fromkeys(only_refund_nos))  # 去重保序
        scope: set[str] = set()  # 增量构建不需 scope
    else:
        # A-20260908-001: 全量构建只物化新行范围内的退费单(线上+已退款 ∪ 带wallet ∪ 外部投诉SN)
        # 构建目标用内存 set(分批遍历需要); 残留清理用独立子查询(见步骤3, 规避参数上限)
        scope = _in_scope_refund_nos(db)
        target_refund_nos = sorted(scope)

    if not target_refund_nos:
        return {"built": 0, "updated": 0, "skipped": 0, "deleted": 0}

    total_built = 0
    total_skipped = 0

    # 2. 分批处理,每批 _BATCH_SIZE 个退费单
    for start in range(0, len(target_refund_nos), _BATCH_SIZE):
        batch = target_refund_nos[start:start + _BATCH_SIZE]
        batch_stats = _build_wide_batch(db, batch)
        total_built += batch_stats["built"]
        total_skipped += batch_stats["skipped"]
        # 释放本批 ORM 对象,防止内存累积
        db.expunge_all()
        logger.info(
            f"refund_wide 批次完成: start={start}, batch_size={len(batch)}, "
            f"batch_built={batch_stats['built']}, 累计={total_built}/{len(target_refund_nos)}"
        )
        if progress_callback:
            try:
                progress_callback(total_built, len(target_refund_nos))
            except Exception:
                pass
        # ⚠️ 读写并发友好: 每批 upsert 后小憩 200ms,把 CPU/IO/连接槽位让给前端查询
        #   宽表构建宁可多花 5-10% 时间,也不把 PostgreSQL 打满导致看板卡顿
        if start + _BATCH_SIZE < len(target_refund_nos):
            time.sleep(0.2)

    # 3. 全量构建后清理残留: 已不在新行范围的行物理删除(增量构建不触发)
    #    用 NOT EXISTS 子查询(非 NOT IN 参数列表):
    #    - 避免把数万参数塞进绑定参数触发 PG 65,535 上限(W1)
    #    - NOT EXISTS 走 hash anti join(实测 0.4s), NOT IN 子查询会 nested loop(110s)
    deleted = 0
    if not only_refund_nos and scope:
        scope_sub = _scope_refund_no_subquery()
        no_longer_in_scope = ~exists(
            select(1).where(scope_sub.c.refund_no == RefundWide.refund_no)
        )
        result = db.execute(
            sa_delete(RefundWide).where(no_longer_in_scope)
        )
        deleted = result.rowcount or 0
        db.commit()
        if deleted:
            logger.info(f"refund_wide 残留清理: deleted={deleted}")

    logger.info(
        f"refund_wide 构建完成: built={total_built}, skipped={total_skipped}, "
        f"deleted={deleted}, target={len(target_refund_nos)}"
    )
    return {"built": total_built, "updated": total_built, "skipped": total_skipped, "deleted": deleted}


def _build_wide_batch(db: Session, target_refund_nos: list[str],
                      *, commit: bool = True) -> dict:
    """为一批退费单构建宽表行并 upsert

    ## `commit` 参数 (2026-09-22)

    生产调用方需要本函数**自己提交**（否则要求调用方管事务, 增加耦合）;
    但**测试**里若内部 `db.commit()`, 会绕过 pytest fixture 的**事务回滚** ——
    造的数据永久留在测试库, 污染后续依赖真实数据的用例。

    实测影响: `tests/test_wide_table_builder.py` 每跑一次就往
    `service_tickets` 留下几行 `SN-WIDE-*` / `GDC-WIDE-*` 合成数据
    （累计 193 行）。它们 `service_category='投诉'` 但 `register_time` 为 NULL,
    导致 A2/A5 相关测试的筛选条件恒为 0 条 → **35 个断言被跳过、从未真正执行**。

    故加 `commit` 开关: 测试传 `commit=False`, 数据随 fixture 事务一起回滚。
    ⚠️ 默认 `True` 保持生产行为不变（生产只有一个调用方, 见下方 `build_wide_table`）。
    """
    # 1. 拉取本批退费单主表记录
    refunds: dict[str, Refund] = {
        r.refund_no: r
        for r in db.scalars(
            select(Refund).where(Refund.refund_no.in_(target_refund_nos))
        )
    }

    # 2. 拉取本批关联关系(src_table='refund' AND status='matched')
    rel_rows = db.execute(
        select(DataMatchRelation.src_id, DataMatchRelation.dst_table, DataMatchRelation.dst_id, DataMatchRelation.match_type).where(
            DataMatchRelation.src_table == "refund",
            DataMatchRelation.status == "matched",
            DataMatchRelation.src_id.in_(target_refund_nos),
        )
    ).all()
    # refund_no → {dst_table: [(dst_id, match_type), ...]}
    refund_rel_map: dict[str, dict[str, list[tuple[str, str]]]] = defaultdict(lambda: defaultdict(list))
    for src_id, dst_table, dst_id, match_type in rel_rows:
        refund_rel_map[src_id][dst_table].append((dst_id, match_type))

    # 3. 收集本批所有 dst_id,批量拉取关联表记录
    #    注: A-20260908-001 起不再物化 merchant_refund(mr_*)关联, 只拉 ticket/wallet
    ticket_ids: set[str] = set()
    wallet_ids: set[str] = set()
    for tables in refund_rel_map.values():
        for dst_id, _ in tables.get("ticket", []):
            ticket_ids.add(dst_id)
        for dst_id, _ in tables.get("wallet", []):
            wallet_ids.add(dst_id)

    tickets: dict[str, ServiceTicket] = {}
    if ticket_ids:
        tickets = {t.ticket_no: t for t in db.scalars(select(ServiceTicket).where(ServiceTicket.ticket_no.in_(ticket_ids)))}

    wallets: dict[str, WalletWithdrawal] = {}
    if wallet_ids:
        wallet_int_ids = [int(wid) for wid in wallet_ids if wid.isdigit()]
        if wallet_int_ids:
            wallets = {str(w.id): w for w in db.scalars(select(WalletWithdrawal).where(WalletWithdrawal.id.in_(wallet_int_ids)))}

    # 4. 外部投诉按 device_sn 聚合(与 matcher 关系无关,直接查表)
    device_sns: set[str] = {r.device_sn for r in refunds.values() if r.device_sn}
    ec_stats: dict[str, tuple[int, datetime | None]] = {}
    if device_sns:
        ec_rows = db.execute(
            select(
                ExternalComplaint.device_sn,
                func.count(ExternalComplaint.id),
                func.max(ExternalComplaint.complaint_date),
            )
            .where(ExternalComplaint.device_sn.in_(device_sns))
            .group_by(ExternalComplaint.device_sn)
        ).all()
        ec_stats = {row[0]: (row[1], row[2]) for row in ec_rows}

    # 5. 组装 RefundWide 行
    now = datetime.now(timezone.utc)
    wide_rows: list[dict] = []
    skipped = 0
    for refund_no in target_refund_nos:
        refund = refunds.get(refund_no)
        if not refund:
            skipped += 1
            continue

        tables = refund_rel_map.get(refund_no, {})

        # 选择工单:优先 order_no 命中,否则最新 finish_time
        ticket = _select_ticket(tables.get("ticket", []), tickets)
        # 钱包:最新 op_time
        wallet = _select_wallet(tables.get("wallet", []), wallets)

        # 外部投诉聚合
        ec_count, ec_latest = (0, None)
        if refund.device_sn and refund.device_sn in ec_stats:
            ec_count, ec_latest = ec_stats[refund.device_sn]

        wide_rows.append({
            "refund_no": refund_no,
            "refund_method": refund.refund_method,
            "status": refund.status,
            "refund_reason": refund.refund_reason,
            "operator": refund.operator,
            "auditor": refund.auditor,
            "device_sn": refund.device_sn,
            "refund_amount": refund.refund_amount,
            "created_at_raw": refund.created_at_raw,
            "ticket_no": ticket.ticket_no if ticket else None,
            "service_item": ticket.service_item if ticket else None,
            "service_category": ticket.service_category if ticket else None,
            "ticket_handle_duration": ticket.handle_duration if ticket else None,
            "ticket_handler": ticket.handler if ticket else None,
            "wallet_id": str(wallet.id) if wallet else None,
            "wallet_op_time": wallet.op_time if wallet else None,
            "wallet_op_amount": wallet.op_amount if wallet else None,
            "has_external_complaint": ec_count > 0,
            "external_complaint_count": ec_count,
            "external_complaint_latest_time": ec_latest,
            "matched_at": now,
        })

    # 6. 批量 upsert(ON CONFLICT refund_no DO UPDATE)
    if wide_rows:
        stmt = pg_insert(RefundWide).values(wide_rows)
        update_set = {f: getattr(stmt.excluded, f) for f in _WIDE_UPDATE_FIELDS}
        stmt = stmt.on_conflict_do_update(index_elements=["refund_no"], set_=update_set)
        db.execute(stmt)
        # `commit=False` 时只 flush —— 数据进入当前事务, 由调用方（测试 fixture）
        # 决定提交或回滚。用 flush 而非不动作: 需保证同事务内后续读取能看到这些行。
        if commit:
            db.commit()
        else:
            db.flush()

    return {"built": len(wide_rows), "skipped": skipped}


def _select_ticket(
    ticket_rels: list[tuple[str, str]], tickets: dict[str, ServiceTicket]
) -> ServiceTicket | None:
    """从同 SN 的多条工单关系中选择一条:优先 order_no 命中,否则最新 finish_time"""
    if not ticket_rels:
        return None
    # 优先 order_no 命中
    for dst_id, match_type in ticket_rels:
        if match_type == "order_no" and dst_id in tickets:
            return tickets[dst_id]
    # 否则取最新 finish_time
    related = [tickets[tid] for tid, _ in ticket_rels if tid in tickets]
    related = [t for t in related if t is not None]
    if not related:
        return None
    related.sort(key=lambda t: t.finish_time or _TZ_MIN, reverse=True)
    return related[0]


def _select_wallet(
    wallet_rels: list[tuple[str, str]], wallets: dict[str, WalletWithdrawal]
) -> WalletWithdrawal | None:
    """从同 SN 的多笔钱包记录中选择最新 op_time 的一笔"""
    if not wallet_rels:
        return None
    related = [wallets[wid] for wid, _ in wallet_rels if wid in wallets]
    related = [w for w in related if w is not None]
    if not related:
        return None
    related.sort(key=lambda w: w.op_time or _TZ_MIN, reverse=True)
    return related[0]

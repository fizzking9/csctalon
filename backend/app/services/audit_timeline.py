"""审计时序服务 - 跨表合并设备/人员相关业务记录为统一时序流

为设备审计页 / 人员审计页提供后端数据支撑:
- 设备视角: 按 device_sn 查 7 张业务表(refund/ticket/wallet/merchant_refund/cs_record/external_complaint/card_complaint)
- 人员视角: 先查 PersonAlias → 用别名在各业务表人名字段 IN 查询

输出统一 TimelineItem 结构:
    {
        "type": "refund"|"ticket"|"wallet"|"merchant_refund"|"cs_record"|"external_complaint"|"card_complaint",
        "time": datetime,           # 主时间字段(用于排序)
        "ref_id": str,               # 业务主键/标识
        "title": str,                # 时序节点标题
        "summary": str,              # 一行摘要
        "amount": float | None,      # 金额(若有)
        "fields": dict,              # 精简字段(key→value),供明细表/卡片显示
    }

设计要点:
1. 各表查询独立,字段精简(用户要求"酌情显示部分有效信息,不需要全部显示")
2. 时间字段统一用各表的主业务时间(refund.created_at_raw / ticket.finish_time / wallet.op_time /
   merchant_refund.trade_time / cs_record.operation_time / external_complaint.complaint_date /
   card_complaint.complaint_date)
3. 合并后按 time 倒序,截断 limit 条
4. 人员视角的自由文本字段(external_complaints.reception_cs)用 LIKE 匹配,性能可控(别名数有限)
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.card_complaint import CardComplaint
from app.models.cs_record import CsRecord
from app.models.external_complaint import ExternalComplaint
from app.models.merchant_refund import MerchantRefund
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.models.wallet import WalletWithdrawal

# 事件类型 → 中文标签 + Element Plus timeline type(决定颜色)
EVENT_TYPE_META: dict[str, dict[str, str]] = {
    "refund": {"label": "退费单", "color": "danger", "icon": "Money"},
    "ticket": {"label": "服务工单", "color": "success", "icon": "Tickets"},
    "wallet": {"label": "钱包流水", "color": "primary", "icon": "Wallet"},
    "merchant_refund": {"label": "商户退款", "color": "warning", "icon": "CreditCard"},
    "cs_record": {"label": "客服接待", "color": "info", "icon": "Service"},
    "external_complaint": {"label": "外部投诉", "color": "danger", "icon": "Warning"},
    "card_complaint": {"label": "卡商投诉", "color": "warning", "icon": "WarningFilled"},
}


def _dt_iso(dt: datetime | None) -> str | None:
    """datetime → ISO 字符串(前端 dayjs 自动转北京时区显示)"""
    if dt is None:
        return None
    # 保证 aware:naive 视为 UTC(理论上业务表都是 aware,兜底)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _amount(v: Any) -> float | None:
    """Numeric → float,None/0 视情况保留(0 是有效金额,保留;None 返 None)"""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _build_refund_items(rows: list[Refund], include_device_sn: bool = False, include_person_name: bool = True) -> list[dict]:
    items: list[dict] = []
    for r in rows:
        amount = _amount(r.refund_amount)
        parts = []
        if r.refund_method:
            parts.append(r.refund_method)
        if r.status:
            parts.append(r.status)
        if include_person_name and r.operator:
            parts.append(f"操作:{r.operator}")
        if r.refund_reason:
            parts.append(r.refund_reason)
        fields = {
            "退费单号": r.refund_no,
            "退款方式": r.refund_method,
            "状态": r.status,
            "退费金额": amount,
            "操作人": r.operator,
            "审核人": r.auditor,
            "退费原因": r.refund_reason,
            "套餐": r.package_name,
            "创建时间": _dt_iso(r.created_at_raw),
        }
        if include_device_sn:
            fields["设备SN"] = r.device_sn
        items.append({
            "type": "refund",
            "time": r.created_at_raw,
            "ref_id": r.refund_no,
            "title": f"退费单 {r.refund_no}",
            "summary": " · ".join(parts) or "退费单",
            "amount": amount,
            "device_sn": r.device_sn if include_device_sn else None,
            "person_names": [n for n in (r.operator, r.auditor) if n] if include_person_name else None,
            "fields": fields,
        })
    return items


def _build_ticket_items(rows: list[ServiceTicket], include_device_sn: bool = False, include_person_name: bool = True) -> list[dict]:
    items: list[dict] = []
    for t in rows:
        amount = _amount(t.refund_amount)
        parts = []
        if t.service_category:
            parts.append(t.service_category)
        if t.service_item:
            parts.append(t.service_item)
        if include_person_name and t.handler:
            parts.append(f"处理:{t.handler}")
        if t.status:
            parts.append(t.status)
        fields = {
            "工单号": t.ticket_no,
            "服务类别": t.service_category,
            "服务项": t.service_item,
            "状态": t.status,
            "处理人": t.handler,
            "登记人": t.registrant,
            "退款金额": amount,
            "处理时长(分)": t.handle_duration,
            "登记时间": _dt_iso(t.register_time),
            "完成时间": _dt_iso(t.finish_time),
        }
        if include_device_sn:
            fields["设备SN"] = t.device_sn
        items.append({
            "type": "ticket",
            "time": t.finish_time or t.register_time,
            "ref_id": t.ticket_no,
            "title": f"工单 {t.ticket_no}",
            "summary": " · ".join(parts) or "服务工单",
            "amount": amount,
            "device_sn": t.device_sn if include_device_sn else None,
            "person_names": [n for n in (t.handler, t.registrant) if n] if include_person_name else None,
            "fields": fields,
        })
    return items


def _build_wallet_items(rows: list[WalletWithdrawal], include_device_sn: bool = False, include_person_name: bool = True) -> list[dict]:
    items: list[dict] = []
    for w in rows:
        amount = _amount(w.op_amount)
        parts = []
        if w.op_type:
            parts.append(w.op_type)
        if include_person_name and w.operator:
            parts.append(f"操作:{w.operator}")
        fields = {
            "流水ID": w.id,
            "操作类型": w.op_type,
            "操作金额": amount,
            "钱包余额": _amount(w.wallet_balance),
            "操作人": w.operator,
            "账户ID": w.account_id,
            "操作时间": _dt_iso(w.op_time),
            "备注": w.remark,
        }
        if include_device_sn:
            fields["设备SN"] = w.device_sn
        items.append({
            "type": "wallet",
            "time": w.op_time,
            "ref_id": f"W{w.id}",
            "title": f"钱包流水 #{w.id}",
            "summary": " · ".join(parts) or "钱包流水",
            "amount": amount,
            "device_sn": w.device_sn if include_device_sn else None,
            "person_names": [w.operator] if include_person_name and w.operator else None,
            "fields": fields,
        })
    return items


def _build_merchant_refund_items(rows: list[MerchantRefund], include_device_sn: bool = False, include_person_name: bool = True) -> list[dict]:
    items: list[dict] = []
    for m in rows:
        amount = _amount(m.refund_amount)
        parts = []
        if m.refund_status:
            parts.append(m.refund_status)
        if m.merchant_id:
            parts.append(f"商户:{m.merchant_id}")
        fields = {
            "微信退款单号": m.wx_refund_no,
            "商户号": m.merchant_id,
            "退款金额": amount,
            "手续费": _amount(m.fee),
            "退款状态": m.refund_status,
            "交易时间": _dt_iso(m.trade_time),
            "商户订单号": m.merchant_order_no,
            "商品名称": m.product_name,
        }
        # merchant_refund 表字段名叫 device_no (如果模型里是 device_sn 就用 device_sn, 否则看 device_no)
        device_sn_val = getattr(m, "device_sn", None) or getattr(m, "device_no", None)
        if include_device_sn and device_sn_val:
            fields["设备SN"] = device_sn_val
        items.append({
            "type": "merchant_refund",
            "time": m.trade_time,
            "ref_id": m.wx_refund_no,
            "title": f"商户退款 {m.wx_refund_no}",
            "summary": " · ".join(parts) or "商户号现金流",
            "amount": amount,
            "device_sn": device_sn_val if include_device_sn else None,
            "person_names": None,  # merchant_refund 无操作人字段
            "fields": fields,
        })
    return items


def _build_cs_record_items(rows: list[CsRecord], include_device_sn: bool = False, include_person_name: bool = True) -> list[dict]:
    items: list[dict] = []
    for c in rows:
        parts = []
        if c.problem_category:
            parts.append(c.problem_category)
        if c.problem_type:
            parts.append(c.problem_type)
        if include_person_name and c.operator:
            parts.append(f"客服:{c.operator}")
        if c.user_accepted:
            parts.append(f"用户{c.user_accepted}")
        fields = {
            "记录ID": c.id,
            "问题大类": c.problem_category,
            "问题类型": c.problem_type,
            "方案": c.solution,
            "操作人": c.operator,
            "执行结果": c.execution_result,
            "用户是否接受": c.user_accepted,
            "操作时间": _dt_iso(c.operation_time),
            "说明": c.description,
        }
        if include_device_sn:
            fields["设备SN"] = c.device_sn
        items.append({
            "type": "cs_record",
            "time": c.operation_time,
            "ref_id": f"C{c.id}",
            "title": f"客服接待 #{c.id}",
            "summary": " · ".join(parts) or "客服接待",
            "amount": None,
            "device_sn": c.device_sn if include_device_sn else None,
            "person_names": [c.operator] if include_person_name and c.operator else None,
            "fields": fields,
        })
    return items


def _build_external_complaint_items(rows: list[ExternalComplaint], include_device_sn: bool = False, include_person_name: bool = True) -> list[dict]:
    items: list[dict] = []
    for e in rows:
        amount = _amount(e.negotiate_refund_amount)
        parts = []
        if e.complaint_category:
            parts.append(e.complaint_category)
        if e.result:
            parts.append(e.result)
        if include_person_name and e.follower:
            parts.append(f"跟进:{e.follower}")
        fields = {
            "投诉编号": e.complaint_no,
            "投诉日期": _dt_iso(e.complaint_date),
            "平台": e.platform,
            "投诉分类": e.complaint_category,
            "投诉问题": e.problem,
            "关键词": e.keyword,
            "结果": e.result,
            "跟进人": e.follower,
            "接待客服": e.reception_cs,
            "协商退款金额": amount,
            "套餐名称": e.package_name,
        }
        if include_device_sn:
            fields["设备SN"] = e.device_sn
        persons = [n for n in (e.follower, e.reception_cs) if n] if include_person_name else None
        items.append({
            "type": "external_complaint",
            "time": e.complaint_date,
            "ref_id": e.complaint_no or f"E{e.id}",
            "title": f"外部投诉 {e.complaint_no or e.id}",
            "summary": " · ".join(parts) or "外部投诉",
            "amount": amount,
            "device_sn": e.device_sn if include_device_sn else None,
            "person_names": persons or None,
            "fields": fields,
        })
    return items


def _build_card_complaint_items(rows: list[CardComplaint], include_device_sn: bool = False, include_person_name: bool = True) -> list[dict]:
    items: list[dict] = []
    for c in rows:
        parts = []
        if c.complaint_group:
            parts.append(c.complaint_group)
        if c.problem_category:
            parts.append(c.problem_category)
        if include_person_name and c.handler:
            parts.append(f"处理:{c.handler}")
        if c.result:
            parts.append(c.result)
        fields = {
            "记录ID": c.id,
            "投诉群": c.complaint_group,
            "投诉日期": _dt_iso(c.complaint_date),
            "投诉人": c.complainant,
            "问题分类": c.problem_category,
            "投诉问题": c.problem,
            "结果": c.result,
            "解决方案": c.solution,
            "处理人": c.handler,
        }
        if include_device_sn:
            fields["设备SN"] = c.device_sn
        items.append({
            "type": "card_complaint",
            "time": c.complaint_date,
            "ref_id": f"K{c.id}",
            "title": f"卡商投诉 #{c.id}",
            "summary": " · ".join(parts) or "卡商投诉",
            "amount": None,
            "device_sn": c.device_sn if include_device_sn else None,
            "person_names": [c.handler] if include_person_name and c.handler else None,
            "fields": fields,
        })
    return items


def _merge_and_sort(item_lists: list[list[dict]], limit: int) -> list[dict]:
    """合并各表 items,按 time 倒序,截断 limit。无 time 的排末尾。"""
    merged: list[dict] = []
    for items in item_lists:
        merged.extend(items)
    merged.sort(key=lambda r: (r.get("time") is None, r.get("time") or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return merged[:limit]


def _resolve_time_window(days: int | None) -> datetime | None:
    """days=None 表示不限时间;否则返回 (now - days) 作为下界(aware UTC)"""
    if not days or days <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days)


def build_device_timeline(
    db: Session,
    sn: str,
    limit: int = 100,
    days: int | None = 90,
    include_person_name: bool = True,
    include_device_sn: bool = False,
) -> dict:
    """构建设备维度的审计时序流

    查 7 张表(device_sn 外键关联;merchant_refund 字段名是 device_no):
        refund / ticket / wallet / merchant_refund / cs_record / external_complaint / card_complaint

    Args:
        include_person_name: 每条事件补充「操作人姓名」汇总到 person_names 字段及 fields 中(默认 True,满足设备审计要含人员姓名)
        include_device_sn: 补设备 SN 字段到每条事件(设备视角默认 False,因为 subject 已带 SN)
    """
    since = _resolve_time_window(days)
    per_table_limit = max(limit, 200)  # 每表多取一些,合并后再截断 limit

    # refund
    refund_stmt = select(Refund).where(Refund.device_sn == sn)
    if since:
        refund_stmt = refund_stmt.where(Refund.created_at_raw >= since)
    refund_stmt = refund_stmt.order_by(Refund.created_at_raw.desc()).limit(per_table_limit)
    refunds = list(db.scalars(refund_stmt))

    # ticket
    ticket_stmt = select(ServiceTicket).where(ServiceTicket.device_sn == sn)
    if since:
        ticket_stmt = ticket_stmt.where(
            or_(ServiceTicket.finish_time >= since, ServiceTicket.register_time >= since)
        )
    ticket_stmt = ticket_stmt.order_by(ServiceTicket.finish_time.desc().nullslast()).limit(per_table_limit)
    tickets = list(db.scalars(ticket_stmt))

    # wallet
    wallet_stmt = select(WalletWithdrawal).where(WalletWithdrawal.device_sn == sn)
    if since:
        wallet_stmt = wallet_stmt.where(WalletWithdrawal.op_time >= since)
    wallet_stmt = wallet_stmt.order_by(WalletWithdrawal.op_time.desc()).limit(per_table_limit)
    wallets = list(db.scalars(wallet_stmt))

    # merchant_refund(字段名是 device_no,不是 device_sn)
    mr_stmt = select(MerchantRefund).where(MerchantRefund.device_no == sn)
    if since:
        mr_stmt = mr_stmt.where(MerchantRefund.trade_time >= since)
    mr_stmt = mr_stmt.order_by(MerchantRefund.trade_time.desc()).limit(per_table_limit)
    merchant_refunds = list(db.scalars(mr_stmt))

    # cs_record
    cs_stmt = select(CsRecord).where(CsRecord.device_sn == sn)
    if since:
        cs_stmt = cs_stmt.where(CsRecord.operation_time >= since)
    cs_stmt = cs_stmt.order_by(CsRecord.operation_time.desc()).limit(per_table_limit)
    cs_records = list(db.scalars(cs_stmt))

    # external_complaint
    ec_stmt = select(ExternalComplaint).where(ExternalComplaint.device_sn == sn)
    if since:
        ec_stmt = ec_stmt.where(ExternalComplaint.complaint_date >= since)
    ec_stmt = ec_stmt.order_by(ExternalComplaint.complaint_date.desc()).limit(per_table_limit)
    external_complaints = list(db.scalars(ec_stmt))

    # card_complaint
    cc_stmt = select(CardComplaint).where(CardComplaint.device_sn == sn)
    if since:
        cc_stmt = cc_stmt.where(CardComplaint.complaint_date >= since)
    cc_stmt = cc_stmt.order_by(CardComplaint.complaint_date.desc()).limit(per_table_limit)
    card_complaints = list(db.scalars(cc_stmt))

    items = _merge_and_sort(
        [
            _build_refund_items(refunds, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_ticket_items(tickets, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_wallet_items(wallets, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_merchant_refund_items(merchant_refunds, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_cs_record_items(cs_records, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_external_complaint_items(external_complaints, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_card_complaint_items(card_complaints, include_device_sn=include_device_sn, include_person_name=include_person_name),
        ],
        limit,
    )

    # 各类型计数
    counts: dict[str, int] = {}
    for it in items:
        counts[it["type"]] = counts.get(it["type"], 0) + 1

    return {
        "subject": {"kind": "device", "sn": sn},
        "items": items,
        "total": len(items),
        "counts": counts,
        "type_meta": EVENT_TYPE_META,
    }


def build_person_timeline(
    db: Session,
    person_id: int,
    limit: int = 100,
    days: int | None = 90,
    include_device_sn: bool = True,
    include_person_name: bool = False,
) -> dict:
    """构建人员维度的审计时序流

    流程:
        1. 查 PersonAlias where person_id=person_id → 别名列表(含 is_system 的 sys/system)
        2. 各业务表人名字段 IN(aliases) 查询
        3. 自由文本字段(external_complaints.reception_cs)用 LIKE 匹配
    注:merchant_refund 无人员字段,不参与人员视角时序

    Args:
        include_device_sn: 每条事件补充「设备 SN」字段到 fields 中(默认 True,满足人员审计要含设备 SN)
        include_person_name: 补操作人姓名字段(人员视角默认 False,因为 subject 已带姓名)
    """
    from app.models.person import Person, PersonAlias

    person = db.get(Person, person_id)
    if not person:
        return {"subject": None, "items": [], "total": 0, "counts": {}, "type_meta": EVENT_TYPE_META}

    aliases = list(db.scalars(select(PersonAlias).where(PersonAlias.person_id == person_id)))
    alias_vals = [a.alias for a in aliases if a.alias]
    if not alias_vals:
        return {
            "subject": {"kind": "person", "id": person_id, "name": person.display_name, "aliases": []},
            "items": [],
            "total": 0,
            "counts": {},
            "type_meta": EVENT_TYPE_META,
        }

    since = _resolve_time_window(days)
    per_table_limit = max(limit, 200)

    # refund: operator / auditor
    refund_stmt = select(Refund).where(or_(Refund.operator.in_(alias_vals), Refund.auditor.in_(alias_vals)))
    if since:
        refund_stmt = refund_stmt.where(Refund.created_at_raw >= since)
    refund_stmt = refund_stmt.order_by(Refund.created_at_raw.desc()).limit(per_table_limit)
    refunds = list(db.scalars(refund_stmt))

    # ticket: handler / registrant
    ticket_stmt = select(ServiceTicket).where(
        or_(ServiceTicket.handler.in_(alias_vals), ServiceTicket.registrant.in_(alias_vals))
    )
    if since:
        ticket_stmt = ticket_stmt.where(
            or_(ServiceTicket.finish_time >= since, ServiceTicket.register_time >= since)
        )
    ticket_stmt = ticket_stmt.order_by(ServiceTicket.finish_time.desc().nullslast()).limit(per_table_limit)
    tickets = list(db.scalars(ticket_stmt))

    # wallet: operator
    wallet_stmt = select(WalletWithdrawal).where(WalletWithdrawal.operator.in_(alias_vals))
    if since:
        wallet_stmt = wallet_stmt.where(WalletWithdrawal.op_time >= since)
    wallet_stmt = wallet_stmt.order_by(WalletWithdrawal.op_time.desc()).limit(per_table_limit)
    wallets = list(db.scalars(wallet_stmt))

    # cs_record: operator
    cs_stmt = select(CsRecord).where(CsRecord.operator.in_(alias_vals))
    if since:
        cs_stmt = cs_stmt.where(CsRecord.operation_time >= since)
    cs_stmt = cs_stmt.order_by(CsRecord.operation_time.desc()).limit(per_table_limit)
    cs_records = list(db.scalars(cs_stmt))

    # external_complaint: follower 精确 + reception_cs 自由文本 LIKE
    ec_conditions = [ExternalComplaint.follower.in_(alias_vals)]
    for av in alias_vals:
        escaped = av.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        ec_conditions.append(ExternalComplaint.reception_cs.like(f"%{escaped}%", escape="\\"))
    ec_stmt = select(ExternalComplaint).where(or_(*ec_conditions))
    if since:
        ec_stmt = ec_stmt.where(ExternalComplaint.complaint_date >= since)
    ec_stmt = ec_stmt.order_by(ExternalComplaint.complaint_date.desc()).limit(per_table_limit)
    external_complaints = list(db.scalars(ec_stmt))

    # card_complaint: handler
    cc_stmt = select(CardComplaint).where(CardComplaint.handler.in_(alias_vals))
    if since:
        cc_stmt = cc_stmt.where(CardComplaint.complaint_date >= since)
    cc_stmt = cc_stmt.order_by(CardComplaint.complaint_date.desc()).limit(per_table_limit)
    card_complaints = list(db.scalars(cc_stmt))

    items = _merge_and_sort(
        [
            _build_refund_items(refunds, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_ticket_items(tickets, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_wallet_items(wallets, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_cs_record_items(cs_records, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_external_complaint_items(external_complaints, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_card_complaint_items(card_complaints, include_device_sn=include_device_sn, include_person_name=include_person_name),
        ],
        limit,
    )

    counts: dict[str, int] = {}
    for it in items:
        counts[it["type"]] = counts.get(it["type"], 0) + 1

    return {
        "subject": {
            "kind": "person",
            "id": person_id,
            "name": person.display_name,
            "department": person.department,
            "role": person.role,
            "aliases": alias_vals,
        },
        "items": items,
        "total": len(items),
        "counts": counts,
        "type_meta": EVENT_TYPE_META,
    }

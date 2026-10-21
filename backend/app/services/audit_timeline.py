"""审计时序服务 - 跨表合并设备/人员相关业务记录为统一时序流

为设备审计页 / 人员审计页提供后端数据支撑:
- 设备视角: 按 device_sn 查业务表(refund/ticket/wallet/merchant_refund/cs_record/external_complaint/
            card_complaint/device_combo/manual_refund/shanmao_refund)
- 人员视角: 先查 PersonAlias → 用别名在各业务表人名字段 IN 查询

投诉类说明:
- service_tickets 上的投诉(商品/服务/微信/工信部/12315等)在"服务工单"tab 展示
- external_complaints(315/监督局) / card_complaints(卡商群) 两张补充表由 workbuddy
  推送, 作为工单口径的更详细补充, 同样纳入设备/人员时序流

输出统一 TimelineItem 结构:
    {
        "type": "refund"|"ticket"|"wallet"|"merchant_refund"|"cs_record"|"external_complaint"|"card_complaint"
              |"device_combo"|"manual_refund"|"shanmao_refund",
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
   merchant_refund.trade_time / cs_record.operation_time / complaint.complaint_date /
   device_combo.created_at_raw / manual_refund.register_time / shanmao_refund.apply_time)
3. 合并后按 time 倒序,截断 limit 条
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import column, or_, select, table, text
from sqlalchemy.orm import Session

# #338: 审计时序读 std_all 镜像(全量展示口径), 禁止直读 ods
_CS = table("std_cs_record_all",
            column("id"), column("problem_category"), column("problem_type"),
            column("solution"), column("operator"), column("execution_result"),
            column("user_accepted"), column("operation_time"), column("description"),
            column("device_sn"))
_EC = table("std_external_complaint_all",
            column("id"), column("complaint_no"), column("platform"), column("complainant"),
            column("complainant_phone"), column("complaint_category"), column("result"),
            column("solution"), column("follower"), column("complaint_date"),
            column("handle_time"), column("problem"), column("handle_detail"),
            column("matched_ticket_no"), column("device_sn"))
_CC = table("std_card_complaint_all",
            column("id"), column("complaint_group"), column("complainant"),
            column("complainant_phone"), column("problem_category"), column("result"),
            column("solution"), column("handler"), column("complaint_date"),
            column("problem"), column("handle_result"), column("matched_ticket_no"),
            column("device_sn"))
_DC = table("std_device_combo_all",
            column("recharge_order_no"), column("package_name"), column("package_type"),
            column("traffic_type"), column("merchant_name"), column("actual_amount"),
            column("start_date"), column("end_date"), column("created_at_raw"),
            column("status"), column("device_sn"), column("creator"))
_BO = table("std_bms_order_all",
            column("order_no"), column("order_type"), column("combo_name"),
            column("combo_price"), column("actual_pay_price"), column("balance_pay_price"),
            column("pay_type"), column("order_status"), column("refund_status"),
            column("business_name"), column("merchant_name"), column("create_user"),
            column("pay_date"), column("create_date"), column("device_sn"))

from app.models.manual_refund import ManualRefund  # noqa: F401  # 类型注解(构建函数签名)
from app.models.merchant_refund import MerchantRefund  # noqa: F401  # 类型注解
from app.models.refund import Refund  # noqa: F401  # 类型注解
from app.models.refund_record_report import RefundRecordReport  # noqa: F401  # 类型注解
from app.models.return_tracking import ReturnTracking  # noqa: F401  # 类型注解
from app.models.shanmao_refund import ShanmaoRefund  # noqa: F401  # 类型注解
from app.models.ticket import ServiceTicket
from app.models.wallet import WalletWithdrawal  # noqa: F401  # 类型注解

# 事件类型 → 中文标签 + Element Plus timeline type(决定颜色)
EVENT_TYPE_META: dict[str, dict[str, str]] = {
    "refund": {"label": "退费单", "color": "danger", "icon": "Money"},
    "ticket": {"label": "服务工单", "color": "success", "icon": "Tickets"},
    "wallet": {"label": "钱包流水", "color": "primary", "icon": "Wallet"},
    "merchant_refund": {"label": "商户退款", "color": "warning", "icon": "CreditCard"},
    "cs_record": {"label": "客服接待", "color": "info", "icon": "Service"},
    "external_complaint": {"label": "315外部投诉", "color": "danger", "icon": "Warning"},
    "card_complaint": {"label": "卡商群投诉", "color": "warning", "icon": "ChatDotRound"},
    "device_combo": {"label": "设备套餐", "color": "primary", "icon": "Goods"},
    "manual_refund": {"label": "手动退款登记", "color": "warning", "icon": "Wallet"},
    "shanmao_refund": {"label": "闪猫退款", "color": "danger", "icon": "Money"},
    # 2026-09-06 设备行为分析完善新增(BMS 五源扩容后入库的新表)
    "bms_order": {"label": "订单", "color": "success", "icon": "ShoppingCart"},
    "return_tracking": {"label": "退货跟踪", "color": "warning", "icon": "RefreshLeft"},
    "refund_record_report": {"label": "线上退款水单", "color": "danger", "icon": "Tickets"},
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
            "person_names": None,
            "fields": fields,
        })
    return items


def _build_cs_record_items(rows: list, include_device_sn: bool = False, include_person_name: bool = True) -> list[dict]:
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


def _build_external_complaint_items(rows: list, include_device_sn: bool = False, include_person_name: bool = True) -> list[dict]:
    """315外部投诉(监督局/315)时序项"""
    items: list[dict] = []
    for c in rows:
        parts = []
        if c.platform:
            parts.append(c.platform)
        if c.result:
            parts.append(c.result)
        if include_person_name and c.follower:
            parts.append(f"跟进:{c.follower}")
        if c.matched_ticket_no:
            parts.append(f"关联工单:{c.matched_ticket_no}")
        fields = {
            "投诉编号": c.complaint_no,
            "平台": c.platform,
            "投诉人": c.complainant,
            "投诉手机号": c.complainant_phone,
            "投诉分类": c.complaint_category,
            "结果": c.result,
            "解决方案": c.solution,
            "跟进人": c.follower,
            "投诉日期": _dt_iso(c.complaint_date),
            "处理时间": _dt_iso(c.handle_time),
            "关联工单": c.matched_ticket_no,
            "投诉问题": (c.problem or "")[:200],
            "处理明细": (c.handle_detail or "")[:200],
        }
        if include_device_sn:
            fields["设备SN"] = c.device_sn
        items.append({
            "type": "external_complaint",
            "time": c.complaint_date,
            "ref_id": c.complaint_no or f"E{c.id}",
            "title": f"315投诉 {c.complaint_no or f'#{c.id}'}",
            "summary": " · ".join(parts) or "315外部投诉",
            "amount": None,
            "device_sn": c.device_sn if include_device_sn else None,
            "person_names": [n for n in (c.follower, c.complainant) if n] if include_person_name else None,
            "fields": fields,
        })
    return items


def _build_card_complaint_items(rows: list, include_device_sn: bool = False, include_person_name: bool = True) -> list[dict]:
    """卡商群投诉时序项"""
    items: list[dict] = []
    for c in rows:
        parts = []
        if c.complaint_group:
            parts.append(c.complaint_group)
        if c.result:
            parts.append(c.result)
        if include_person_name and c.handler:
            parts.append(f"处理:{c.handler}")
        if c.matched_ticket_no:
            parts.append(f"关联工单:{c.matched_ticket_no}")
        fields = {
            "投诉群": c.complaint_group,
            "投诉人": c.complainant,
            "投诉手机号": c.complainant_phone,
            "问题分类": c.problem_category,
            "结果": c.result,
            "解决方案": c.solution,
            "处理人": c.handler,
            "投诉日期": _dt_iso(c.complaint_date),
            "关联工单": c.matched_ticket_no,
            "投诉问题": (c.problem or "")[:200],
            "处理结果": (c.handle_result or "")[:200],
        }
        if include_device_sn:
            fields["设备SN"] = c.device_sn
        items.append({
            "type": "card_complaint",
            "time": c.complaint_date,
            "ref_id": f"CC{c.id}",
            "title": f"卡商投诉 #{c.id}",
            "summary": " · ".join(parts) or "卡商群投诉",
            "amount": None,
            "device_sn": c.device_sn if include_device_sn else None,
            "person_names": [n for n in (c.handler, c.complainant) if n] if include_person_name else None,
            "fields": fields,
        })
    return items


def _build_device_combo_items(rows: list, include_device_sn: bool = False, include_person_name: bool = True) -> list[dict]:
    """设备套餐购买/流量时序项"""
    items: list[dict] = []
    for c in rows:
        parts = []
        if c.package_name:
            parts.append(c.package_name)
        if c.package_type:
            parts.append(c.package_type)
        if c.status:
            parts.append(c.status)
        amount = _amount(c.actual_amount)
        fields = {
            "充值订单号": c.recharge_order_no,
            "套餐名称": c.package_name,
            "套餐类型": c.package_type,
            "流量类型": c.traffic_type,
            "商户名称": c.merchant_name,
            "实付金额": amount,
            "套餐开始": _dt_iso(c.start_date),
            "套餐结束": _dt_iso(c.end_date),
            "套餐状态": c.status,
            "创建时间": _dt_iso(c.created_at_raw),
        }
        if include_device_sn:
            fields["设备SN"] = c.device_sn
        items.append({
            "type": "device_combo",
            "time": c.created_at_raw,
            "ref_id": c.recharge_order_no,
            "title": f"套餐 {c.package_name or c.recharge_order_no}",
            "summary": " · ".join(parts) or "设备套餐",
            "amount": amount,
            "device_sn": c.device_sn if include_device_sn else None,
            "person_names": [c.creator] if include_person_name and c.creator else None,
            "fields": fields,
        })
    return items


def _build_manual_refund_items(rows: list[ManualRefund], include_device_sn: bool = False, include_person_name: bool = True) -> list[dict]:
    """手动退款登记(商户号后台操作)时序项"""
    items: list[dict] = []
    for m in rows:
        parts = []
        if m.is_refunded:
            parts.append("已退款" if str(m.is_refunded) in ("是", "Y", "y", "1") else "未退款")
        if m.refund_amount:
            parts.append(f"¥{m.refund_amount}")
        if m.order_no:
            parts.append(f"订单:{m.order_no}")
        if include_person_name and m.registrant:
            parts.append(f"登记:{m.registrant}")
        fields = {
            "订单号": m.order_no,
            "商户ID": m.merchant_id,
            "订单金额": _amount(m.order_amount),
            "退款金额": _amount(m.refund_amount),
            "是否已退款": m.is_refunded,
            "登记人": m.registrant,
            "退款操作人": m.refund_operator,
            "登记时间": _dt_iso(m.register_time),
            "退款时间": _dt_iso(m.op_time),
            "备注": (m.remark or "")[:200],
        }
        if include_device_sn:
            fields["设备SN"] = m.device_sn
        items.append({
            "type": "manual_refund",
            "time": m.register_time,
            "ref_id": m.order_no or f"M{m.id}",
            "title": f"手动退款登记 {m.order_no or f'#{m.id}'}",
            "summary": " · ".join(parts) or "手动退款登记",
            "amount": _amount(m.refund_amount),
            "device_sn": m.device_sn if include_device_sn else None,
            "person_names": [n for n in (m.registrant, m.refund_operator) if n] if include_person_name else None,
            "fields": fields,
        })
    return items


def _build_shanmao_refund_items(rows: list[ShanmaoRefund], include_device_sn: bool = False, include_person_name: bool = True) -> list[dict]:
    """闪猫退款时序项"""
    items: list[dict] = []
    for s in rows:
        parts = []
        if s.status:
            parts.append(s.status)
        if s.refund_amount:
            parts.append(f"¥{s.refund_amount}")
        if include_person_name and s.applicant:
            parts.append(f"申请:{s.applicant}")
        fields = {
            "闪猫退款单号": s.refund_no,
            "订单号": s.order_no,
            "套餐": s.package_name,
            "商户名称": s.merchant_name,
            "支付金额": _amount(s.paid_amount),
            "申请退款金额": _amount(s.refund_amount),
            "实际退款金额": _amount(s.actual_amount),
            "状态": s.status,
            "退款原因": s.refund_reason,
            "退款方式": s.refund_method,
            "申请人": s.applicant,
            "审核人": s.auditor,
            "操作人": s.operator,
            "申请时间": _dt_iso(s.apply_time),
            "审核时间": _dt_iso(s.audit_time),
            "退款时间": _dt_iso(s.refund_time),
            "手机号": s.phone,
        }
        if include_device_sn:
            fields["设备SN"] = s.device_sn
        items.append({
            "type": "shanmao_refund",
            "time": s.apply_time or s.refund_time,
            "ref_id": s.refund_no,
            "title": f"闪猫退款 {s.refund_no}",
            "summary": " · ".join(parts) or "闪猫退款",
            "amount": _amount(s.refund_amount),
            "device_sn": s.device_sn if include_device_sn else None,
            "person_names": [n for n in (s.applicant, s.auditor, s.operator) if n] if include_person_name else None,
            "fields": fields,
        })
    return items


def _build_bms_order_items(rows: list, include_device_sn: bool = False, include_person_name: bool = True) -> list[dict]:
    """订单管理(bms_orders)时序项 - BMS 支付订单台账(微信续费/储值卡/后台充值/预存等)"""
    items: list[dict] = []
    for o in rows:
        parts = []
        if o.order_type:
            parts.append(o.order_type)
        if o.order_status:
            # 存库为中文(支付成功/未支付/已关闭等)或原始码, 统一展示原文
            parts.append(str(o.order_status))
        amount = _amount(o.actual_pay_price) if o.actual_pay_price is not None else _amount(o.combo_price)
        fields = {
            "订单号": o.order_no,
            "订单类型": o.order_type,
            "套餐": o.combo_name,
            "套餐价格": _amount(o.combo_price),
            "实付金额": _amount(o.actual_pay_price),
            "余额支付": _amount(o.balance_pay_price),
            "支付方式": o.pay_type,
            "订单状态": o.order_status,
            "退款状态": o.refund_status,
            "商户名称": o.business_name or o.merchant_name,
            "创建人": o.create_user,
            "支付时间": _dt_iso(o.pay_date),
            "创建时间": _dt_iso(o.create_date),
        }
        if include_device_sn:
            fields["设备SN"] = o.device_sn
        items.append({
            "type": "bms_order",
            "time": o.create_date or o.pay_date,
            "ref_id": o.order_no,
            "title": f"订单 {o.order_no}",
            "summary": " · ".join(parts) or "订单",
            "amount": amount,
            "device_sn": o.device_sn if include_device_sn else None,
            "person_names": [o.create_user] if include_person_name and o.create_user else None,
            "fields": fields,
        })
    return items


def _build_return_tracking_items(rows: list[ReturnTracking], include_device_sn: bool = False, include_person_name: bool = True) -> list[dict]:
    """退货跟踪(return_trackings)时序项 - 设备退货/换货全流程"""
    items: list[dict] = []
    for r in rows:
        parts = []
        if r.return_type:
            parts.append(r.return_type)
        if r.return_sign_status:
            parts.append(f"签收:{r.return_sign_status}")
        if r.return_reason:
            parts.append(r.return_reason)
        amount = _amount(r.refund_amount)
        fields = {
            "退货类型": r.return_type,
            "退货原因": r.return_reason,
            "退款金额": amount,
            "实付金额": _amount(r.paid_amount),
            "签收状态": r.return_sign_status,
            "快递单号": r.return_express_no,
            "店铺": r.shop,
            "商户": r.merchant,
            "销售订单号": r.order_no,
            "下单时间": _dt_iso(r.order_time),
            "退货时间": _dt_iso(r.return_time),
            "签收时间": _dt_iso(r.sign_time),
            "创建人": r.creator,
            "备注": (r.remark or "")[:200],
        }
        if include_device_sn:
            fields["设备SN"] = r.device_sn
        items.append({
            "type": "return_tracking",
            "time": r.return_time or r.sign_time or r.created_at_raw or r.order_time,
            "ref_id": r.order_no or f"RT{r.id}",
            "title": f"退货 {r.return_type or r.order_no or f'#{r.id}'}",
            "summary": " · ".join(parts) or "退货跟踪",
            "amount": amount,
            "device_sn": r.device_sn if include_device_sn else None,
            "person_names": [n for n in (r.creator, r.updater) if n] if include_person_name else None,
            "fields": fields,
        })
    return items


def _build_refund_record_report_items(rows: list[RefundRecordReport], include_device_sn: bool = False, include_person_name: bool = True) -> list[dict]:
    """线上退款水单(refund_record_reports, E 路)时序项 - BMS 财务退款记录报表持续采集"""
    items: list[dict] = []
    for r in rows:
        parts = []
        if r.refund_status:
            parts.append(r.refund_status)
        amount = _amount(r.refund_amount)
        if amount is not None:
            parts.append(f"¥{amount}")
        if r.merchant_name:
            parts.append(r.merchant_name)
        fields = {
            "平台订单号": r.platform_order_no,
            "微信订单号": r.wx_order_no,
            "商户号": r.merchant_id,
            "商户名": r.merchant_name,
            "商品名称": r.product_name,
            "申请退款金额": _amount(r.apply_refund_amount),
            "退款金额": amount,
            "退款状态": r.refund_status,
            "退款时间": _dt_iso(r.refund_time),
        }
        if include_device_sn:
            fields["设备SN"] = r.device_sn
        items.append({
            "type": "refund_record_report",
            "time": r.refund_time,
            "ref_id": r.platform_order_no or r.wx_order_no or f"RRR{r.id}",
            "title": f"线上退款 {r.platform_order_no or f'#{r.id}'}",
            "summary": " · ".join(parts) or "线上退款水单",
            "amount": amount,
            "device_sn": r.device_sn if include_device_sn else None,
            "person_names": None,
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

    查业务表(device_sn 外键关联;merchant_refund 字段名是 device_no):
        refund / ticket / wallet / merchant_refund / cs_record / external_complaint / card_complaint
        / device_combo(设备套餐) / manual_refund(手动退款登记) / shanmao_refund(闪猫退款)
    """
    since = _resolve_time_window(days)
    per_table_limit = max(limit, 200)

    # #334: 读 std_*_all 全量镜像(ods 同构透传), 业务代码零读 ods
    refund_sql = "SELECT * FROM std_refund_online_all WHERE device_sn = :sn"
    refund_params: dict = {"sn": sn, "lim": per_table_limit}
    if since:
        refund_sql += " AND created_at_raw >= :since"
        refund_params["since"] = since
    refund_sql += " ORDER BY created_at_raw DESC LIMIT :lim"
    refunds = list(db.execute(text(refund_sql), refund_params).all())

    ticket_stmt = select(ServiceTicket).where(ServiceTicket.device_sn == sn)
    if since:
        ticket_stmt = ticket_stmt.where(
            or_(ServiceTicket.finish_time >= since, ServiceTicket.register_time >= since)
        )
    tickets = list(db.scalars(ticket_stmt.order_by(ServiceTicket.finish_time.desc().nullslast()).limit(per_table_limit)))

    # #334: 读 std_*_all 全量镜像(ods 同构透传), 业务代码零读 ods
    wallet_sql = "SELECT * FROM std_wallet_flow_all WHERE device_sn = :sn"
    wallet_params: dict = {"sn": sn, "lim": per_table_limit}
    if since:
        wallet_sql += " AND op_time >= :since"
        wallet_params["since"] = since
    wallet_sql += " ORDER BY op_time DESC LIMIT :lim"
    wallets = list(db.execute(text(wallet_sql), wallet_params).all())

    mr_sql = "SELECT * FROM std_merchant_refund_all WHERE device_no = :sn"
    mr_params: dict = {"sn": sn, "lim": per_table_limit}
    if since:
        mr_sql += " AND trade_time >= :since"
        mr_params["since"] = since
    mr_sql += " ORDER BY trade_time DESC LIMIT :lim"
    merchant_refunds = list(db.execute(text(mr_sql), mr_params).all())

    cs_stmt = select(_CS).where(_CS.c.device_sn == sn)
    if since:
        cs_stmt = cs_stmt.where(_CS.c.operation_time >= since)
    cs_records = list(db.execute(cs_stmt.order_by(_CS.c.operation_time.desc()).limit(per_table_limit)).mappings())

    ec_stmt = select(_EC).where(_EC.c.device_sn == sn)
    if since:
        ec_stmt = ec_stmt.where(_EC.c.complaint_date >= since)
    external_complaints = list(db.execute(ec_stmt.order_by(_EC.c.complaint_date.desc().nullslast()).limit(per_table_limit)).mappings())

    cc_stmt = select(_CC).where(_CC.c.device_sn == sn)
    if since:
        cc_stmt = cc_stmt.where(_CC.c.complaint_date >= since)
    card_complaints = list(db.execute(cc_stmt.order_by(_CC.c.complaint_date.desc().nullslast()).limit(per_table_limit)).mappings())

    # 设备套餐(device_combos): 主时间=created_at_raw(购买时间), 无则退 start_date
    combo_stmt = select(_DC).where(_DC.c.device_sn == sn)
    if since:
        combo_stmt = combo_stmt.where(
            or_(_DC.c.created_at_raw >= since, _DC.c.start_date >= since)
        )
    device_combos = list(db.execute(combo_stmt.order_by(_DC.c.created_at_raw.desc().nullslast()).limit(per_table_limit)).mappings())

    # 手动退款登记(manual_refunds): 主时间=register_time
    # #334: 读 std_*_all 全量镜像(ods 同构透传), 业务代码零读 ods
    manual_sql = "SELECT * FROM std_refund_manual_all WHERE device_sn = :sn"
    manual_params: dict = {"sn": sn, "lim": per_table_limit}
    if since:
        manual_sql += " AND register_time >= :since"
        manual_params["since"] = since
    manual_sql += " ORDER BY register_time DESC NULLS LAST LIMIT :lim"
    manual_refunds = list(db.execute(text(manual_sql), manual_params).all())

    # 闪猫退款(shanmao_refunds): 主时间=apply_time
    # #334: 读 std_*_all 全量镜像(ods 同构透传), 业务代码零读 ods
    shanmao_sql = "SELECT * FROM std_refund_shanmao_all WHERE device_sn = :sn"
    shanmao_params: dict = {"sn": sn, "lim": per_table_limit}
    if since:
        shanmao_sql += " AND apply_time >= :since"
        shanmao_params["since"] = since
    shanmao_sql += " ORDER BY apply_time DESC NULLS LAST LIMIT :lim"
    shanmao_refunds = list(db.execute(text(shanmao_sql), shanmao_params).all())

    # 订单管理(bms_orders): 主时间=create_date(订单创建/同步锚点), 退 create_date
    order_stmt = select(_BO).where(_BO.c.device_sn == sn)
    if since:
        order_stmt = order_stmt.where(
            or_(_BO.c.create_date >= since, _BO.c.pay_date >= since)
        )
    bms_orders = list(db.execute(order_stmt.order_by(_BO.c.create_date.desc().nullslast()).limit(per_table_limit)).mappings())

    # 退货跟踪(return_trackings): 主时间=return_time(退货发生), 退 created_at_raw/order_time
    # #338: 读 std_return_tracking_all 全量镜像(ods 同构透传), 业务代码零读 ods
    rt_sql = "SELECT * FROM std_return_tracking_all WHERE device_sn = :sn"
    rt_params: dict = {"sn": sn, "lim": per_table_limit}
    if since:
        rt_sql += " AND (return_time >= :since OR sign_time >= :since OR created_at_raw >= :since)"
        rt_params["since"] = since
    rt_sql += " ORDER BY return_time DESC NULLS LAST LIMIT :lim"
    return_trackings = list(db.execute(text(rt_sql), rt_params).all())

    # 线上退款水单(refund_record_reports, E路): 主时间=refund_time
    # #334: 读 std_*_all 全量镜像(ods 同构透传), 业务代码零读 ods
    rrr_sql = "SELECT * FROM std_refund_record_all WHERE device_sn = :sn"
    rrr_params: dict = {"sn": sn, "lim": per_table_limit}
    if since:
        rrr_sql += " AND refund_time >= :since"
        rrr_params["since"] = since
    rrr_sql += " ORDER BY refund_time DESC NULLS LAST LIMIT :lim"
    refund_record_reports = list(db.execute(text(rrr_sql), rrr_params).all())

    items = _merge_and_sort(
        [
            _build_refund_items(refunds, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_ticket_items(tickets, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_wallet_items(wallets, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_merchant_refund_items(merchant_refunds, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_device_combo_items(device_combos, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_manual_refund_items(manual_refunds, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_shanmao_refund_items(shanmao_refunds, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_cs_record_items(cs_records, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_external_complaint_items(external_complaints, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_card_complaint_items(card_complaints, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_bms_order_items(bms_orders, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_return_tracking_items(return_trackings, include_device_sn=include_device_sn, include_person_name=include_person_name),
            _build_refund_record_report_items(refund_record_reports, include_device_sn=include_device_sn, include_person_name=include_person_name),
        ],
        limit,
    )

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
        1. 查 PersonAlias where person_id=person_id → 别名列表
        2. 各业务表人名字段 IN(aliases) 查询
    注:merchant_refund 无人员字段,不参与人员视角时序
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

    # #334: 读 std_*_all 全量镜像(ods 同构透传), 业务代码零读 ods
    refund_sql = "SELECT * FROM std_refund_online_all WHERE (operator = ANY(:aliases) OR auditor = ANY(:aliases))"
    refund_params = {"aliases": alias_vals, "lim": per_table_limit}
    if since:
        refund_sql += " AND created_at_raw >= :since"
        refund_params["since"] = since
    refund_sql += " ORDER BY created_at_raw DESC LIMIT :lim"
    refunds = list(db.execute(text(refund_sql), refund_params).all())

    ticket_stmt = select(ServiceTicket).where(
        or_(ServiceTicket.handler.in_(alias_vals), ServiceTicket.registrant.in_(alias_vals))
    )
    if since:
        ticket_stmt = ticket_stmt.where(
            or_(ServiceTicket.finish_time >= since, ServiceTicket.register_time >= since)
        )
    tickets = list(db.scalars(ticket_stmt.order_by(ServiceTicket.finish_time.desc().nullslast()).limit(per_table_limit)))

    # #334: 读 std_*_all 全量镜像(ods 同构透传), 业务代码零读 ods
    wallet_sql = "SELECT * FROM std_wallet_flow_all WHERE operator = ANY(:aliases)"
    wallet_params = {"aliases": alias_vals, "lim": per_table_limit}
    if since:
        wallet_sql += " AND op_time >= :since"
        wallet_params["since"] = since
    wallet_sql += " ORDER BY op_time DESC LIMIT :lim"
    wallets = list(db.execute(text(wallet_sql), wallet_params).all())

    cs_stmt = select(_CS).where(_CS.c.operator.in_(alias_vals))
    if since:
        cs_stmt = cs_stmt.where(_CS.c.operation_time >= since)
    cs_records = list(db.execute(cs_stmt.order_by(_CS.c.operation_time.desc()).limit(per_table_limit)).mappings())

    ec_stmt = select(_EC).where(
        _EC.c.follower.in_(alias_vals)
    )
    if since:
        ec_stmt = ec_stmt.where(_EC.c.complaint_date >= since)
    external_complaints = list(db.execute(ec_stmt.order_by(_EC.c.complaint_date.desc().nullslast()).limit(per_table_limit)).mappings())

    cc_stmt = select(_CC).where(_CC.c.handler.in_(alias_vals))
    if since:
        cc_stmt = cc_stmt.where(_CC.c.complaint_date >= since)
    card_complaints = list(db.execute(cc_stmt.order_by(_CC.c.complaint_date.desc().nullslast()).limit(per_table_limit)).mappings())

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

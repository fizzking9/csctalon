"""手动退款登记 数据质量审核 (L3: 自动纠错记录 + 待人工确认)。

## 为什么单独成模块(#322 P2 方案A)

原实现位于 `app/services/analytics/refund.py`, 但其中 `manual_refund_quality_confirm/fix`
是**写路径** —— 需要直接 UPDATE `ods.manual_refunds` 基表(`ManualRefund` 只能映射基表,
视图不可写)。analytics 层已整体切到 `public` 干净视图读路径, 门禁 C2 禁止 analytics 再引用
ods 基表/基类, 故把写路径**移出 analytics**:

- 读(列表): 走 `ManualRefundClean`(薄视图 `public.manual_refund_clean`)
- 写(确认/修正): 走 ods 基表 `ManualRefund`(可写)

行为与签名逐字不变, 仅数据源归属调整。
"""

import datetime as _dt
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.manual_refund import ManualRefund
from app.models.ods_clean import ManualRefundClean
from app.services.analytics._core import TZ, D


def _mr_quality_out(r: ManualRefund) -> dict[str, Any]:
    """手动退款登记行 → 质量审核输出"""
    def _sh(dt):
        return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S") if dt else None
    return {
        "id": r.id,
        "register_time": _sh(r.register_time),
        "op_time": _sh(r.op_time),
        "is_refunded": r.is_refunded,
        "refund_amount": float(r.refund_amount or 0),
        "order_amount": float(r.order_amount or 0),
        "device_sn": r.device_sn,
        "order_no": r.order_no,
        "merchant_id": r.merchant_id,
        "registrant": r.registrant,
        "refund_operator": r.refund_operator,
        "remark": r.remark,
        "quality_status": r.quality_status,
        "quality_note": r.quality_note,
    }

def manual_refund_quality_list(db: Session, status: str = "all", page: int = 1, page_size: int = 50) -> dict[str, Any]:
    """手动退款登记质量审核列表: status = all/pending/auto_fixed/ok

    统计摘要: 各状态计数(供前端徽标)。列表按登记日期倒序。
    """
    summary = {}
    for st in ("pending", "auto_fixed", "ok"):
        summary[st] = db.scalar(
            select(func.count()).select_from(ManualRefundClean).where(ManualRefundClean.quality_status == st)
        ) or 0
    q = select(ManualRefundClean)
    if status in ("pending", "auto_fixed", "ok"):
        q = q.where(ManualRefundClean.quality_status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.execute(
        q.order_by(ManualRefundClean.register_time.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return {"total": total, "summary": summary,
            "list": [_mr_quality_out(r) for r in rows]}

def manual_refund_quality_confirm(db: Session, rid: int) -> dict[str, Any]:
    """人工确认通过: quality_status → ok (保留纠错说明, 追加确认标记)"""
    r = db.get(ManualRefund, rid)
    if not r:
        raise ValueError(f"手动退款记录不存在: id={rid}")
    r.quality_status = "ok"
    r.quality_note = ((r.quality_note or "") + " | 人工确认通过") if r.quality_note else "人工确认通过"
    db.commit()
    return {"id": rid, "quality_status": "ok"}

def manual_refund_quality_fix(db: Session, rid: int, payload: dict[str, Any]) -> dict[str, Any]:
    """人工修正: 更新指定字段(register_time/op_time/is_refunded/refund_amount/order_no/device_sn等),
    修正后置 ok。仅接受白名单字段, 时间字段支持 'YYYY-MM-DD' / 'YYYY-MM-DD HH:MM:SS' / ISO 格式。
    """
    r = db.get(ManualRefund, rid)
    if not r:
        raise ValueError(f"手动退款记录不存在: id={rid}")
    allowed = {"register_time", "op_time", "is_refunded", "refund_amount", "order_amount",
               "order_no", "device_sn", "merchant_id", "registrant", "refund_operator", "remark"}
    changes = []
    for k, v in (payload or {}).items():
        if k not in allowed or v is None:
            continue
        if k in ("register_time", "op_time"):
            setattr(r, k, _dt.datetime.fromisoformat(str(v)) if isinstance(v, str) and "T" in str(v)
                    else _dt.datetime.strptime(str(v), "%Y-%m-%d %H:%M:%S")
                    if isinstance(v, str) and len(str(v)) > 10
                    else _dt.datetime.strptime(str(v), "%Y-%m-%d"))
            changes.append(k)
        elif k in ("refund_amount", "order_amount"):
            setattr(r, k, D(v))
            changes.append(k)
        else:
            setattr(r, k, str(v))
            changes.append(k)
    r.quality_status = "ok"
    r.quality_note = ((r.quality_note or "") + f" | 人工修正字段: {','.join(changes)}") if changes else (r.quality_note or "")
    db.commit()
    return {"id": rid, "quality_status": "ok", "changed": changes}

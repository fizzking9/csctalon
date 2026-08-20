"""发票/报销路由(规划设计 §4.5)

上传电子发票(二维码/PDF/图片)→识别→记录→汇总报销→导出。
首版: 全电发票二维码内容解析(本地,无第三方) + 手工字段兜底 + 人工核对。
"""

import io
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DBSession, ManagerUser
from app.models.expense import ExpenseInvoice, Reimbursement, ReimbursementItem
from app.schemas.common import PageBase, R

router = APIRouter(prefix="/invoices", tags=["发票登记"])


def _decode_qr_from_image(img_bytes: bytes) -> list[str]:
    """从图片字节中本地解码二维码(pyzbar), 返回所有二维码文本列表。

    依赖 pyzbar + 系统库 libzbar(zbar)。未安装时返回空列表, 由调用方降级到人工录入。
    """
    try:
        from PIL import Image  # type: ignore
        from pyzbar.pyzbar import decode  # type: ignore
    except ImportError:
        return []
    try:
        img = Image.open(io.BytesIO(img_bytes))
        results = decode(img)
        return [r.data.decode("utf-8", errors="replace") for r in results if r.data]
    except Exception:  # noqa: BLE001 解码失败返回空, 不影响主流程
        return []


def _gen_batch_no() -> str:
    return "RB" + datetime.now().strftime("%Y%m%d%H%M%S%f")


def _serialize_inv(i: ExpenseInvoice) -> dict:
    return {
        "id": i.id,
        "invoice_code": i.invoice_code,
        "invoice_no": i.invoice_no,
        "invoice_type": i.invoice_type,
        "seller_name": i.seller_name,
        "buyer_name": i.buyer_name,
        "amount": float(i.amount) if i.amount is not None else None,
        "tax_amount": float(i.tax_amount) if i.tax_amount is not None else None,
        "total_amount": float(i.total_amount) if i.total_amount is not None else None,
        "invoice_date": i.invoice_date.isoformat() if i.invoice_date else None,
        "file_url": i.file_url,
        "file_type": i.file_type,
        "confidence": i.confidence,
        "status": i.status,
        "submitter_id": i.submitter_id,
        "remark": i.remark,
        "created_at": i.created_at.isoformat() if i.created_at else None,
    }


def parse_qr_invoice(qr_text: str) -> dict:
    """解析全电发票二维码内容(尽力而为, 不抛异常)。

    全电发票二维码内容为逗号分隔: 01,10,发票号码,发票代码,价税合计,开票日期,校验码,...
    无法可靠解析时返回原始文本, 由人工核对兜底。
    """
    raw = qr_text.strip()
    result = {"raw": raw, "invoice_no": None, "invoice_code": None, "total_amount": None, "invoice_date": None}
    parts = raw.split(",")
    if len(parts) >= 7 and parts[0] == "01":
        # 常见结构: 01,类型,发票号码,发票代码,价税合计,开票日期,校验码
        try:
            result["invoice_no"] = parts[2].strip() or None
            result["invoice_code"] = parts[3].strip() or None
            amt = parts[4].strip()
            if amt:
                result["total_amount"] = float(amt)
            dt = parts[5].strip()
            if dt and len(dt) == 8:
                result["invoice_date"] = f"{dt[0:4]}-{dt[4:6]}-{dt[6:8]}"
        except (ValueError, IndexError):
            pass
    return result


@router.get("", response_model=R[PageBase], summary="发票列表")
def list_invoices(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    keyword: str | None = None,
):
    stmt = select(ExpenseInvoice)
    if status:
        stmt = stmt.where(ExpenseInvoice.status == status)
    if keyword:
        kw = f"%{keyword}%"
        stmt = stmt.where(
            (ExpenseInvoice.invoice_no.like(kw)) | (ExpenseInvoice.seller_name.like(kw)) | (ExpenseInvoice.buyer_name.like(kw))
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(db.scalars(stmt.order_by(ExpenseInvoice.id.desc()).offset((page - 1) * page_size).limit(page_size)))
    return R(data=PageBase(items=[_serialize_inv(i) for i in items], total=total or 0, page=page, page_size=page_size))


@router.post("", response_model=R, summary="登记发票(支持二维码文本识别)")
def create_invoice(payload: dict, db: DBSession, current_user: CurrentUser):
    qr_text = payload.get("qr_text")
    parsed = {}
    if qr_text:
        parsed = parse_qr_invoice(qr_text)
    invoice_no = payload.get("invoice_no") or parsed.get("invoice_no")
    invoice_code = payload.get("invoice_code") or parsed.get("invoice_code")
    total_amount = payload.get("total_amount") if payload.get("total_amount") is not None else parsed.get("total_amount")
    invoice_date = payload.get("invoice_date") or parsed.get("invoice_date")
    inv = ExpenseInvoice(
        invoice_code=invoice_code,
        invoice_no=invoice_no,
        invoice_type=payload.get("invoice_type") or "全电",
        seller_name=payload.get("seller_name"),
        buyer_name=payload.get("buyer_name"),
        amount=payload.get("amount"),
        tax_amount=payload.get("tax_amount"),
        total_amount=total_amount,
        invoice_date=datetime.fromisoformat(invoice_date) if invoice_date else None,
        file_url=payload.get("file_url"),
        file_type=payload.get("file_type") or ("qr" if qr_text else None),
        ocr_result=parsed if qr_text else None,
        confidence=1.0 if (qr_text and parsed.get("invoice_no")) else None,
        status=payload.get("status", "已识别" if parsed.get("invoice_no") else "待核对"),
        submitter_id=current_user.id,
        remark=payload.get("remark"),
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return R(data=_serialize_inv(inv))


@router.post("/decode-image", response_model=R, summary="上传发票图片本地解码二维码(规划设计 §4.5)")
async def decode_invoice_image(file: UploadFile = File(...), _: CurrentUser = None):
    """上传电子发票截图/拍图(PNG/JPG), 本地解码二维码返回结构化数据。

    依赖 pyzbar + 系统库 libzbar。未安装时返回 parsed=None + 安装提示, 前端引导用户改用
    手工录入或文本粘贴。返回的 parsed 字段可经人工核对后调用 POST /invoices 写入。
    """
    if not file:
        raise HTTPException(status_code=400, detail="未提供文件")
    img_bytes = await file.read()
    if not img_bytes:
        raise HTTPException(status_code=400, detail="文件为空")
    # 限制 10MB
    if len(img_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件过大(>10MB)")
    qr_texts = _decode_qr_from_image(img_bytes)
    if not qr_texts:
        # 未检测到二维码 - 可能是无二维码图、模糊、遮挡, 或 pyzbar 未安装
        return R(
            data={
                "decoded": False,
                "qr_texts": [],
                "parsed": None,
                "message": "未检测到二维码。请确认图片清晰、二维码无遮挡; 或改用文本粘贴录入。",
            }
        )
    # 取第一个能解析出 invoice_no 的二维码; 否则用第一个文本
    parsed = None
    used_text = qr_texts[0]
    for t in qr_texts:
        p = parse_qr_invoice(t)
        if p.get("invoice_no"):
            parsed = p
            used_text = t
            break
    if parsed is None:
        parsed = parse_qr_invoice(qr_texts[0])
    return R(
        data={
            "decoded": True,
            "qr_texts": qr_texts,
            "used_text": used_text,
            "parsed": parsed,
            "filename": file.filename,
            "content_type": file.content_type,
            "file_size": len(img_bytes),
        }
    )


@router.put("/{invoice_id}", response_model=R, summary="更新发票")
def update_invoice(invoice_id: int, payload: dict, db: DBSession, _: CurrentUser):
    inv = db.get(ExpenseInvoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="发票不存在")
    for k in ("invoice_code", "invoice_no", "invoice_type", "seller_name", "buyer_name",
              "amount", "tax_amount", "total_amount", "file_url", "file_type", "status", "remark"):
        if k in payload:
            setattr(inv, k, payload[k])
    if "invoice_date" in payload:
        inv.invoice_date = datetime.fromisoformat(payload["invoice_date"]) if payload["invoice_date"] else None
    db.commit()
    db.refresh(inv)
    return R(data=_serialize_inv(inv))


@router.delete("/{invoice_id}", response_model=R, summary="删除发票")
def delete_invoice(invoice_id: int, db: DBSession, _: ManagerUser):
    inv = db.get(ExpenseInvoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="发票不存在")
    db.delete(inv)
    db.commit()
    return R()


# ---------- 报销单 ----------


@router.get("/reimbursements", response_model=R[PageBase], summary="报销单列表")
def list_reimbursements(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    stmt = select(Reimbursement).order_by(Reimbursement.id.desc())
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)))
    return R(
        data=PageBase(
            items=[
                {
                    "id": r.id,
                    "batch_no": r.batch_no,
                    "title": r.title,
                    "submitter_id": r.submitter_id,
                    "total_amount": float(r.total_amount) if r.total_amount is not None else None,
                    "invoice_count": r.invoice_count,
                    "status": r.status,
                    "approved_at": r.approved_at.isoformat() if r.approved_at else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in items
            ],
            total=total or 0,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/reimbursements", response_model=R, summary="新建报销单(含发票明细)")
def create_reimbursement(payload: dict, db: DBSession, current_user: CurrentUser):
    # 2026-08-20 审计 H-2/API-01 修复: 状态由服务端强制为"待审核", 不接受客户端指定(防绕过主管审核)
    client_status = payload.get("status")
    if client_status and client_status != "待审核":
        raise HTTPException(status_code=400, detail="新建报销单状态只能为待审核, 审核请走审核接口")
    invoice_ids = payload.get("invoice_ids") or []
    invoices = list(db.scalars(select(ExpenseInvoice).where(ExpenseInvoice.id.in_(invoice_ids)))) if invoice_ids else []
    if len(invoices) != len(set(invoice_ids)):
        raise HTTPException(status_code=400, detail="存在无效的发票 ID")
    # 2026-08-20 审计 H-2/API-02 修复: 拦截已占用/已报销发票(防同一发票重复报销)
    occupied = [i for i in invoices if i.status in ("已入账", "已报销")]
    if occupied:
        nos = ", ".join((i.invoice_no or f"#{i.id}") for i in occupied[:5])
        raise HTTPException(status_code=400, detail=f"以下发票已被占用或已报销, 不能加入报销单: {nos}")
    total = sum(float(i.total_amount or 0) for i in invoices)
    rb = Reimbursement(
        batch_no=_gen_batch_no(),
        title=payload.get("title"),
        submitter_id=current_user.id,
        total_amount=total,
        invoice_count=len(invoices),
        status="待审核",
        remark=payload.get("remark"),
    )
    db.add(rb)
    db.flush()
    for i in invoices:
        db.add(ReimbursementItem(reimbursement_id=rb.id, invoice_id=i.id, amount=i.total_amount))
        i.status = "已入账"  # 占用标记: 审核通过后置"已报销", 驳回后释放
    db.commit()
    db.refresh(rb)
    return R(data={"id": rb.id, "batch_no": rb.batch_no, "total_amount": float(rb.total_amount or 0), "invoice_count": rb.invoice_count})


@router.post("/reimbursements/{rb_id}/approve", response_model=R, summary="审核报销单")
def approve_reimbursement(rb_id: int, payload: dict, db: DBSession, current_user: ManagerUser):
    rb = db.get(Reimbursement, rb_id)
    if not rb:
        raise HTTPException(status_code=404, detail="报销单不存在")
    # 2026-08-20 审计 H-2/API-01 修复: 仅允许 待审核 → 已通过/已驳回, 不可重复审核
    if rb.status != "待审核":
        raise HTTPException(status_code=409, detail=f"当前状态为 {rb.status}, 只有待审核的报销单可被审核")
    new_status = payload.get("status", "已通过")
    if new_status not in ("已通过", "已驳回"):
        raise HTTPException(status_code=400, detail="审核结果只能为: 已通过 / 已驳回")
    rb.status = new_status
    rb.approved_by = current_user.id
    rb.approved_at = datetime.now(timezone.utc)
    # 2026-08-20 审计 H-2/API-02 修复: 审核通过 → 发票置"已报销"(全库首个赋值路径); 驳回 → 释放发票供重新提交
    items = list(db.scalars(select(ReimbursementItem).where(ReimbursementItem.reimbursement_id == rb_id)))
    for it in items:
        inv = db.get(ExpenseInvoice, it.invoice_id)
        if not inv:
            continue
        inv.status = "已报销" if new_status == "已通过" else "待核对"
    db.commit()
    return R(message="审核完成")


@router.get("/reimbursements/{rb_id}/export", summary="导出报销清单(Excel)")
def export_reimbursement(rb_id: int, db: DBSession, _: CurrentUser):
    rb = db.get(Reimbursement, rb_id)
    if not rb:
        raise HTTPException(status_code=404, detail="报销单不存在")
    items = list(
        db.scalars(
            select(ExpenseInvoice)
            .join(ReimbursementItem, ReimbursementItem.invoice_id == ExpenseInvoice.id)
            .where(ReimbursementItem.reimbursement_id == rb_id)
            .order_by(ExpenseInvoice.id)
        )
    )
    import io
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "报销清单"
    ws.append(["报销单号", "标题", "总金额", "发票张数", "状态"])
    ws.append([rb.batch_no, rb.title or "", float(rb.total_amount or 0), rb.invoice_count, rb.status])
    ws.append([])
    ws.append(["发票号码", "发票代码", "类型", "销方", "购方", "金额", "税额", "价税合计", "开票日期"])
    for i in items:
        ws.append([
            i.invoice_no, i.invoice_code, i.invoice_type, i.seller_name, i.buyer_name,
            float(i.amount or 0), float(i.tax_amount or 0), float(i.total_amount or 0),
            i.invoice_date.strftime("%Y-%m-%d") if i.invoice_date else "",
        ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    rb.exported_at = datetime.now(timezone.utc)
    db.commit()
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=reimbursement_{rb.batch_no}.xlsx"},
    )

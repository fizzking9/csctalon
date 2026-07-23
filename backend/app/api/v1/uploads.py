"""数据上传与导入路由"""

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DBSession
from app.core.config import settings
from app.core.database import get_db
from app.models.upload import UploadRecord
from app.schemas.common import PageBase, R
from app.schemas.upload import ImportResponse, UploadRecordOut
from app.services.data_parser import parse_file, save_upload_file
from app.services.matcher import match_all
from app.services.upsert import run_upsert

router = APIRouter(prefix="/uploads", tags=["数据中心-上传"])

DATA_TYPES = {"refund", "wallet", "ticket", "merchant_refund", "merchant"}


@router.post("/parse", response_model=R, summary="上传并解析文件(返回预览)")
async def parse_upload(
    current_user: CurrentUser,
    data_type: str = Form(...),
    file: UploadFile = File(...),
    sheet_name: str | None = Form(None),
):
    """上传文件,解析后返回列名/样例/建议映射,不写入数据库"""
    if data_type not in DATA_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的数据类型,可选: {DATA_TYPES}")
    content = await file.read()
    if len(content) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"文件超过 {settings.MAX_UPLOAD_SIZE_MB}MB 限制")
    path = save_upload_file(content, file.filename or "upload")
    try:
        preview = parse_file(path, data_type, sheet_name=sheet_name)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件解析失败: {e}") from e
    return R(data=preview)


@router.post("/import", response_model=R[ImportResponse], summary="确认导入(upsert+关联匹配)")
async def confirm_import(
    payload: dict,
    db: DBSession,
    current_user: CurrentUser,
):
    """确认导入:读取已上传文件,按列映射执行 upsert,并触发关联匹配

    Body:
        data_type, filename, sheet_name, encoding, column_mapping
    """
    data_type = payload.get("data_type")
    filename = payload.get("filename")
    if data_type not in DATA_TYPES or not filename:
        raise HTTPException(status_code=400, detail="data_type 或 filename 缺失")
    mapping = payload.get("column_mapping") or {}
    sheet_name = payload.get("sheet_name")
    encoding = payload.get("encoding")

    path = Path(settings.UPLOAD_DIR) / Path(filename).name
    if not path.exists():
        raise HTTPException(status_code=404, detail="上传文件不存在,请重新上传")

    # 创建上传记录
    record = UploadRecord(
        data_type=data_type,
        filename=str(path.name),
        file_size=path.stat().st_size,
        sheet_name=sheet_name,
        status="parsing",
        uploaded_by=current_user.username,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    try:
        # 重新加载完整 DataFrame
        from app.services.data_parser import load_dataframe

        df = load_dataframe(path, sheet_name=sheet_name, encoding=encoding)
        # 执行 upsert
        result = run_upsert(df, data_type, mapping, db)
        # 触发关联匹配(增量)
        match_stats = match_all(db, only_unmatched=True)
        record.total_rows = result.total
        record.inserted_rows = result.inserted
        record.updated_rows = result.updated
        record.failed_rows = result.failed
        record.matched_rows = sum(match_stats.values())
        record.status = "imported"
        record.finished_at = datetime.now(timezone.utc)
        if result.errors:
            record.error_detail = {"errors": result.errors[:50]}  # 仅存前50条错误
        db.commit()
        db.refresh(record)
        return R(
            data=ImportResponse(
                upload_id=record.id,
                data_type=record.data_type,
                filename=record.filename,
                total_rows=record.total_rows,
                inserted_rows=record.inserted_rows,
                updated_rows=record.updated_rows,
                failed_rows=record.failed_rows,
                matched_rows=record.matched_rows,
                status=record.status,
                errors=result.errors[:50] if result.errors else None,
                finished_at=record.finished_at,
            )
        )
    except Exception as e:
        record.status = "failed"
        record.error_detail = {"error": str(e)}
        record.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=500, detail=f"导入失败: {e}") from e


@router.get("/records", response_model=R[PageBase[UploadRecordOut]], summary="上传记录列表")
def list_records(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    data_type: str | None = None,
):
    from sqlalchemy import func

    stmt = select(UploadRecord)
    if data_type:
        stmt = stmt.where(UploadRecord.data_type == data_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(stmt.order_by(UploadRecord.id.desc()).offset((page - 1) * page_size).limit(page_size))
    )
    return R(
        data=PageBase(
            items=[UploadRecordOut.model_validate(u) for u in items],
            total=total or 0,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/records/{record_id}", response_model=R[UploadRecordOut], summary="上传记录详情")
def get_record(record_id: int, db: DBSession, _: CurrentUser):
    record = db.get(UploadRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")
    return R(data=UploadRecordOut.model_validate(record))

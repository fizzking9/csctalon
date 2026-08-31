"""数据上传与导入路由"""

from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DBSession
from app.core.config import settings
from app.core.database import get_db
from app.core.storage import resolve_upload_path
from app.models.data_source import DataSource
from app.models.upload import UploadRecord
from app.schemas.common import PageBase, R
from app.schemas.upload import DiffPreviewRequest, ImportResponse, UploadRecordOut
from app.services.data_parser import load_dataframe, parse_file, save_upload_file
from app.services.diff_preview import compute_diff
from app.services.matcher import match_all
from app.services.person_extractor import extract_person_aliases
from app.services.upsert import run_upsert

router = APIRouter(prefix="/uploads", tags=["数据中心-上传"])

DATA_TYPES = {"refund", "shanmao_refund", "wallet", "ticket", "merchant_refund", "merchant", "cs_record", "external_complaint", "card_complaint", "staff"}


@router.post("/parse", response_model=R, summary="上传并解析文件(返回预览)")
async def parse_upload(
    current_user: CurrentUser,
    data_type: str = Form(...),
    file: UploadFile | None = File(None),
    sheet_name: str | None = Form(None),
    stored_path: str | None = Form(None),
    filename: str | None = Form(None),
):
    """上传文件,解析后返回列名/样例/建议映射,不写入数据库

    支持两种模式:
    1. 上传文件模式:提供 file 参数
    2. 已有文件模式:提供 stored_path 参数(用于抓取等场景)
    """
    if data_type not in DATA_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的数据类型,可选: {DATA_TYPES}")

    if stored_path:
        # 已有文件模式(路径穿越校验)
        path = resolve_upload_path(stored_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="文件不存在")
        actual_stored_path = stored_path
    elif file:
        # 上传文件模式
        content = await file.read()
        if len(content) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"文件超过 {settings.MAX_UPLOAD_SIZE_MB}MB 限制")
        try:
            # P0-3: save_upload_file 落盘前校验扩展名白名单 + magic bytes
            path, actual_stored_path = save_upload_file(content, file.filename or "upload")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        filename = file.filename
    else:
        raise HTTPException(status_code=400, detail="必须提供 file 或 stored_path 参数")

    try:
        preview = parse_file(path, data_type, sheet_name=sheet_name)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件解析失败: {e}") from e
    preview["stored_path"] = actual_stored_path
    preview["filename"] = filename
    return R(data=preview)


@router.post("/preview-diff", response_model=R, summary="diff 预览(导入前查看 existing/new/abnormal)")
async def preview_diff(
    payload: DiffPreviewRequest,
    db: DBSession,
    current_user: CurrentUser,
):
    """在确认导入前，按主键策略计算 diff：
    - existing: 主键已存在且字段全一致（将跳过/覆盖）
    - new: 主键不存在（将新增）
    - abnormal: 主键已存在但有字段不同（将覆盖，需人工确认）或主键为空

    Body: data_type, filename, stored_path, sheet_name, encoding, column_mapping
    """
    if payload.data_type not in DATA_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的数据类型,可选: {DATA_TYPES}")

    path = resolve_upload_path(payload.stored_path, payload.filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="上传文件不存在,请重新上传")

    try:
        df = load_dataframe(path, sheet_name=payload.sheet_name, encoding=payload.encoding)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件解析失败: {e}") from e

    # 查数据源元数据（决定 pk_strategy）
    ds = db.scalar(select(DataSource).where(DataSource.code == payload.data_type))
    diff = compute_diff(db, df, payload.data_type, payload.column_mapping, ds)
    return R(data=diff)


@router.post("/import", response_model=R[ImportResponse], summary="确认导入(upsert+关联匹配)")
async def confirm_import(
    payload: dict,
    db: DBSession,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
):
    """确认导入:读取已上传文件,按列映射执行 upsert,并触发关联匹配

    Body:
        data_type, filename, sheet_name, encoding, column_mapping
    """
    data_type = payload.get("data_type")
    filename = payload.get("filename")
    stored_path = payload.get("stored_path")
    if data_type not in DATA_TYPES or not filename:
        raise HTTPException(status_code=400, detail="data_type 或 filename 缺失")
    mapping = payload.get("column_mapping") or {}
    sheet_name = payload.get("sheet_name")
    encoding = payload.get("encoding")

    # 优先用 stored_path 找文件（归档后路径），兼容旧 filename；路径穿越校验
    path = resolve_upload_path(stored_path, filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="上传文件不存在,请重新上传")

    # 创建上传记录
    record = UploadRecord(
        data_type=data_type,
        filename=filename,
        stored_path=stored_path,
        file_size=path.stat().st_size,
        sheet_name=sheet_name,
        status="parsing",
        uploaded_by=current_user.username,
        source="manual",
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    try:
        # 重新加载完整 DataFrame
        df = load_dataframe(path, sheet_name=sheet_name, encoding=encoding)
        # 执行 upsert
        result = run_upsert(df, data_type, mapping, db)
        # 更新记录状态
        record.total_rows = result.total
        record.inserted_rows = result.inserted
        record.updated_rows = result.updated
        record.failed_rows = result.failed
        record.status = "imported"
        record.finished_at = datetime.now(timezone.utc)
        if result.errors:
            record.error_detail = {"errors": result.errors[:50], "warnings": result.warnings[:50]}  # 仅存前50条
        db.commit()
        db.refresh(record)

        # 后台异步执行关联匹配和人员别名提取（避免阻塞导入响应）
        background_tasks.add_task(_run_post_import_tasks, data_type)

        return R(
            data=ImportResponse(
                upload_id=record.id,
                data_type=record.data_type,
                filename=record.filename,
                total_rows=record.total_rows,
                inserted_rows=record.inserted_rows,
                updated_rows=record.updated_rows,
                failed_rows=record.failed_rows,
                matched_rows=0,  # 关联匹配在后台执行
                status=record.status,
                errors=result.errors[:50] if result.errors else None,
                warnings=result.warnings[:50] if result.warnings else None,
                finished_at=record.finished_at,
            )
        )
    except Exception as e:
        # upsert 失败时事务可能已 aborted,需先 rollback 才能更新上传记录状态
        db.rollback()
        record = db.get(UploadRecord, record.id)
        if record:
            record.status = "failed"
            record.error_detail = {"error": str(e)[:2000]}
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
    source: str | None = None,
):
    from sqlalchemy import func

    stmt = select(UploadRecord)
    if data_type:
        stmt = stmt.where(UploadRecord.data_type == data_type)
    if source:
        stmt = stmt.where(UploadRecord.source == source)
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


def _run_post_import_tasks(data_type: str):
    """后台任务:关联匹配、人员别名提取、退费宽表物化"""
    from app.core.database import SessionLocal
    from app.services.matcher import match_all
    from app.services.person_extractor import extract_person_aliases
    from app.services.wide_table_builder import build_refund_wide

    try:
        db = SessionLocal()
        try:
            # 触发关联匹配(增量)
            match_stats = match_all(db, only_unmatched=True)
            # 触发人员别名提取(增量,幂等)
            person_stats = extract_person_aliases(db)
            db.commit()
            # 重建退费宽表(全量,确保源数据更新后宽表同步刷新;失败不阻塞)
            try:
                wide_stats = build_refund_wide(db)
                import logging
                logging.getLogger(__name__).info(
                    f"refund_wide 重建完成(data_type={data_type}): {wide_stats}"
                )
            except Exception as we:
                import logging
                logging.getLogger(__name__).warning(
                    f"refund_wide 重建失败(data_type={data_type},不影响导入): {we}"
                )
            # 导入会更新工单/退款等主台账 → 失效看板缓存(含投诉), 避免旧聚合
            # (见 数据源接入与缓存加速规范.md §九: 每个写入入口必须失效缓存)
            try:
                from app.core.redis_client import cache_invalidate_pattern
                n = 0
                for pat in ("today:*", "refund:*", "complaint:*"):
                    n += cache_invalidate_pattern(pat)
                if n:
                    import logging
                    logging.getLogger(__name__).info(
                        f"上传导入后失效 {n} 条看板缓存(data_type={data_type})"
                    )
            except Exception as ce:
                import logging
                logging.getLogger(__name__).debug(
                    f"上传导入后缓存失效失败(data_type={data_type},不影响导入): {ce}"
                )
        finally:
            db.close()
    except Exception as e:
        # 后台任务异常不影响主流程,记录日志即可
        import logging
        logging.getLogger(__name__).error(f"后台任务执行失败(data_type={data_type}): {e}")

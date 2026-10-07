"""数据上传与导入路由"""

import csv
import io
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.api.deps import CurrentUser, DBSession
from app.core.config import settings
from app.core.storage import resolve_upload_path
from app.models.data_source import DataSource
from app.models.upload import UploadRecord
from app.schemas.common import PageBase, R
from app.schemas.upload import DiffPreviewRequest, ImportResponse, UploadRecordOut
from app.services.data_parser import COLUMN_TEMPLATES, load_dataframe, parse_file, save_upload_file
from app.services.diff_preview import compute_diff
from app.services.ingest_service import ingest_table
from app.services.matcher import match_all
from app.services.person_extractor import extract_person_aliases

router = APIRouter(prefix="/uploads", tags=["数据中心-上传"])

DATA_TYPES = {
    "refund", "shanmao_refund", "wallet", "ticket", "merchant_refund", "merchant",
    "cs_record", "external_complaint", "card_complaint", "staff",
    # BMS 五源扩容新增(2026-09-05, A-20260905-001): 支持文件导入回补/补录
    "refund_record_report", "bms_order", "return_tracking",
    "device_flow_usage", "device_profile",
    # 其它既有可导入类型(补齐 Upload 未暴露/历史遗漏)
    "manual_refund", "device_combo", "device_tag", "device_tag_binding",
}


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
            # #213: 同步落盘/校验放入线程池，避免阻塞事件循环（本函数因需 await file.read 保留 async）。
            path, actual_stored_path = await run_in_threadpool(
                save_upload_file, content, file.filename or "upload"
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        filename = file.filename
    else:
        raise HTTPException(status_code=400, detail="必须提供 file 或 stored_path 参数")

    try:
        # #213: pandas 解析是 CPU/IO 重活，放入线程池，避免大文件解析阻塞事件循环。
        preview = await run_in_threadpool(parse_file, path, data_type, sheet_name=sheet_name)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件解析失败: {e}") from e
    preview["stored_path"] = actual_stored_path
    preview["filename"] = filename
    return R(data=preview)


@router.post("/preview-diff", response_model=R, summary="diff 预览(导入前查看 existing/new/abnormal)")
# #213: 改同步 def —— 体内 load_dataframe(pandas) / compute_diff(同步 DB) 全是阻塞调用且无
# await；声明 async 反而让重活跑在事件循环线程。普通 def 由 FastAPI 自动放入线程池执行。
def preview_diff(
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
# #213: 改同步 def —— load_dataframe + ingest_table(批量 upsert) 是最重的同步调用且无 await，
# 必须跑在线程池而非事件循环线程；关联匹配等后处理已在 BackgroundTasks 中异步执行。
def confirm_import(
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
        # 统一入库接口(#42): 所有采集入口走同一通道, 返回值统一 IngestResult
        result = ingest_table(
            db, data_type,
            df=df, mapping=mapping,
            source="upload", source_ref=f"upload_record:{record.id}",
        )
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


@router.get("/template", summary="下载导入模板(CSV, 按类型)")
def download_template(
    _: CurrentUser,
    data_type: str = Query(..., description="数据类型(与上传下拉一致)"),
):
    """生成 CSV 导入模板: 表头 = 该类型建议映射列(COLUMN_TEMPLATES 首个候选中文列名)。

    用户下载后按列填数, 再走 上传→解析→映射→导入; 用于 BMS 五源新表的历史补录。
    输出 UTF-8 BOM, Excel 打开不乱码。
    """
    if data_type not in DATA_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的数据类型,可选: {sorted(DATA_TYPES)}")
    tpl = COLUMN_TEMPLATES.get(data_type)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"{data_type} 暂无列模板,无法生成")
    # 每个目标字段取第一个候选中文列名作为模板表头(与 suggest_mapping 一致)
    headers: list[str] = []
    for target, candidates in tpl.items():
        cand = candidates[0] if candidates else target
        headers.append(cand)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    csv_bytes = ("\ufeff" + buf.getvalue()).encode("utf-8")
    fname = f"template_{data_type}_{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/file", summary="下载已上传/采集的原始文件")
def download_upload_file(
    db: DBSession,
    _: CurrentUser,
    record_id: int = Query(..., description="上传记录 ID(upload_records)"),
):
    """下载 upload_records 中某条记录对应的原始 CSV/XLSX(含 BMS 采集落盘的)。

    安全: 经 resolve_upload_path 校验路径在 UPLOAD_DIR 内; 仅登录用户可访问。
    用于回看采集到的原始文件 / 校验导入依据 / 导出发给他人参考。
    """
    record = db.get(UploadRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")
    path = resolve_upload_path(record.stored_path, record.filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="原始文件不存在,可能已被清理")
    return FileResponse(
        path=str(path),
        filename=record.filename or path.name,
        media_type="application/octet-stream",
    )


def _run_post_import_tasks(data_type: str):
    """后台任务:关联匹配、人员别名提取、退费宽表物化

    ⚠️ 2026-09-14 (#88): 退费宽表改为**按相关源白名单触发**(与 bms_sync 的 post-sync
    钩子对齐)。修复前此处是**无条件全量** `build_refund_wide(db)`,`data_type` 只用于
    打日志 —— 导致上传一个与宽表毫无关系的设备套餐(device_combo)CSV, 也会全量重建
    6.6 万行宽表(约 50s)。宽表行本体来自 refunds, 列来自 tickets/wallet_withdrawals/
    external_complaints, 与 device_combo 完全无关。
    """
    from app.core.database import SessionLocal
    from app.services.bms_sync import (
        CASH_FLOW_WIDE_RELEVANT_SOURCES,
        REFUND_WIDE_RELEVANT_SOURCES,
        is_cash_flow_wide_relevant,
        is_refund_wide_relevant,
    )
    from app.services.wide_table_builder import build_refund_wide

    try:
        db = SessionLocal()
        try:
            # 触发关联匹配(增量)
            match_stats = match_all(db, only_unmatched=True)
            # 触发人员别名提取(增量,幂等)
            extract_person_aliases(db)
            db.commit()
            # 重建退费宽表 —— 仅当本次导入的源属于宽表相关源,或匹配新增了关系时才做。
            # 判定直接复用 bms_sync 的白名单(单一事实来源, 避免两处判据漂移)。
            # 注意: 上传通道能拿到的只有 data_type, 拿不到真实写入行数; 该通道本身
            # 就是"用户确认导入"语义(调用前已完成 ingest_table), 故不再校验行数。
            # 失败不阻塞导入。
            import logging
            _log = logging.getLogger(__name__)
            relevant = is_refund_wide_relevant(data_type)
            match_affected = (match_stats or {}).get("_affected_refund_nos") or set()
            if not relevant and not match_affected:
                _log.info(
                    f"跳过 refund_wide 重建(data_type={data_type} 非宽表相关源, "
                    f"相关源={list(REFUND_WIDE_RELEVANT_SOURCES)}, 且匹配无新增)"
                )
            else:
                try:
                    wide_stats = build_refund_wide(db)
                    wide_stats["relevant_sources"] = [data_type] if relevant else ["(matcher)"]
                    _log.info(
                        f"refund_wide 重建完成(data_type={data_type}): {wide_stats}"
                    )
                except Exception as we:
                    _log.warning(
                        f"refund_wide 重建失败(data_type={data_type},不影响导入): {we}"
                    )
            # 2026-09-08 (#41/A-20260908-004): 现金流水统一宽表重建(ABCD 出款源上传后刷新;失败不阻塞)
            # 2026-09-14 (#88): 判据统一到 CASH_FLOW_WIDE_RELEVANT_SOURCES 白名单(与
            # bms_sync post-sync 同源), 并补齐 external_complaint(has_external_complaint 列依赖它)。
            if is_cash_flow_wide_relevant(data_type):
                try:
                    from app.services.cash_flow_wide_builder import build_cash_flow_wide
                    _cfw_stats = build_cash_flow_wide(db)
                    _log.info(
                        f"cash_flow_wide 重建完成(data_type={data_type}): {_cfw_stats}"
                    )
                except Exception as cfe:
                    _log.warning(
                        f"cash_flow_wide 重建失败(data_type={data_type},不影响导入): {cfe}"
                    )
            else:
                _log.info(
                    f"跳过 cash_flow_wide 重建(data_type={data_type} 非相关源, "
                    f"相关源={list(CASH_FLOW_WIDE_RELEVANT_SOURCES)})"
                )
            # 导入会更新工单/退款等主台账 → 失效看板缓存(含投诉), 避免旧聚合
            # (见 数据源接入与缓存加速规范.md §九: 每个写入入口必须失效缓存)
            try:
                from app.core.redis_client import cache_invalidate_recent_days
                n = 0
                for pat in ("today:*", "refund:*", "complaint:*"):
                    n += cache_invalidate_recent_days(pat)
                if n:
                    import logging
                    logging.getLogger(__name__).info(
                        f"上传导入后失效 {n} 条看板缓存(data_type={data_type},近N天窗口保留历史)"
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

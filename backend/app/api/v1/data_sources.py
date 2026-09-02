"""数据源元数据管理路由 + 通用数据浏览"""

import io
import json
from datetime import datetime
from typing import Any
from urllib.parse import quote

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.models.data_source import DataSource, DataSourceField
from app.schemas.common import PageBase, R
from app.schemas.data_source import (
    DataSourceDetail,
    DataSourceFieldOut,
    DataSourceFieldUpdate,
    DataSourceOut,
    DataSourceUpdate,
)
from app.services.data_source_registry import DATA_SOURCE_SEEDS, ensure_data_sources_registered
from app.utils.excel import clean_df_for_excel

# 导出 Excel 单次最大行数(防止 OOM;超出提示加筛选条件)
EXPORT_MAX_ROWS = 200_000

router = APIRouter(prefix="/data-sources", tags=["系统-数据源"])


def _with_field_count(db, ds: DataSource) -> DataSourceOut:
    cnt = db.scalar(select(func.count()).select_from(DataSourceField).where(DataSourceField.source_id == ds.id)) or 0
    out = DataSourceOut.model_validate(ds)
    out.field_count = cnt
    return out


@router.get("", response_model=R, summary="数据源列表")
def list_sources(db: DBSession, _: CurrentUser, include_placeholder: bool = Query(False)):
    stmt = select(DataSource).order_by(DataSource.id)
    # 默认排除占位数据源(规划中,无实际表);数据查看页传 include_placeholder=true 显示占位项
    if not include_placeholder:
        stmt = stmt.where(DataSource.status != "placeholder")
    items = list(db.scalars(stmt))
    return R(data=[_with_field_count(db, ds) for ds in items])


@router.get("/{code}", response_model=R, summary="数据源详情(含字段)")
def get_source(code: str, db: DBSession, _: CurrentUser):
    ds = db.scalar(select(DataSource).where(DataSource.code == code))
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    detail = DataSourceDetail.model_validate(ds)
    detail.field_count = len(detail.fields)
    return R(data=detail)


@router.get("/{code}/fields", response_model=R, summary="数据源字段列表")
def list_fields(code: str, db: DBSession, _: CurrentUser):
    ds = db.scalar(select(DataSource).where(DataSource.code == code))
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    fields = list(
        db.scalars(
            select(DataSourceField).where(DataSourceField.source_id == ds.id).order_by(DataSourceField.sort_order)
        )
    )
    return R(data=[DataSourceFieldOut.model_validate(f) for f in fields])


@router.patch("/{code}", response_model=R, summary="更新数据源元数据")
def update_source(code: str, payload: DataSourceUpdate, db: DBSession, _: AdminUser):
    ds = db.scalar(select(DataSource).where(DataSource.code == code))
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(ds, k, v)
    db.commit()
    db.refresh(ds)
    return R(data=_with_field_count(db, ds))


@router.patch("/fields/{field_id}", response_model=R, summary="更新字段元数据")
def update_field(field_id: int, payload: DataSourceFieldUpdate, db: DBSession, _: AdminUser):
    f = db.get(DataSourceField, field_id)
    if not f:
        raise HTTPException(status_code=404, detail="字段不存在")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(f, k, v)
    db.commit()
    db.refresh(f)
    return R(data=DataSourceFieldOut.model_validate(f))


@router.post("/refresh", response_model=R, summary="触发数据源元数据重建(反射模型)")
def refresh_sources(db: DBSession, _: AdminUser):
    stats = ensure_data_sources_registered(db)
    return R(data=stats)


# ---------- 通用数据浏览 ----------


def _get_source_model(code: str):
    """根据数据源 code 获取对应的 SQLAlchemy 模型类(占位数据源无 model,返回 None)"""
    for seed in DATA_SOURCE_SEEDS:
        if seed["code"] == code:
            return seed.get("model"), seed.get("time_field")
    return None, None


@router.get("/{code}/records", response_model=R[PageBase], summary="通用数据浏览")
def browse_records(
    code: str,
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_field: str | None = None,
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    filters: str | None = Query(None, description="JSON 格式筛选条件,如 {\"status\":\"已退款\"}"),
):
    """浏览指定数据源的原始记录,支持分页/排序/简单筛选

    filters 参数示例:
    - {"status": "已退款"} → 精确匹配
    - {"amount__gt": 100} → 大于
    - {"amount__gte": 100} → 大于等于
    - {"amount__lt": 100} → 小于
    - {"amount__lte": 100} → 小于等于
    - {"name__contains": "关键词"} → 模糊匹配
    """
    stmt, field_dicts, total = _build_browse_query(code, db, sort_field, sort_dir, filters, with_count=True)
    rows = list(db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)))

    # 序列化
    items = []
    for row in rows:
        rec = {}
        for fd in field_dicts:
            fname = fd["field_name"]
            v = getattr(row, fname, None)
            if fd["data_type"] == "timestamp" and v is not None:
                rec[fname] = v.isoformat()
            elif fd["data_type"] == "numeric" and v is not None:
                try:
                    rec[fname] = float(v)
                except Exception:
                    rec[fname] = None
            else:
                rec[fname] = v
        items.append(rec)

    return {"code": 0, "message": "ok", "data": {"items": items, "total": total, "page": page, "page_size": page_size}}


def _build_browse_query(
    code: str,
    db: Session,
    sort_field: str | None,
    sort_dir: str,
    filters: str | None,
    with_count: bool = True,
) -> tuple[Select, list[dict], int]:
    """构建通用数据浏览查询(筛选+排序),供 browse_records 与 export 共用

    Returns: (stmt, field_dicts, total)
      - stmt: 已应用筛选/排序的 select(model) 语句,调用方自行加 offset/limit 或全部取出
      - field_dicts: 字段定义列表(用于序列化/列头)
      - total: 符合筛选条件的总行数(with_count=False 时为 0,导出用 True 便于超限校验)
    """
    model, default_sort_field = _get_source_model(code)
    if not model:
        raise HTTPException(status_code=404, detail=f"数据源不存在: {code}")

    # 解析 filters JSON
    filters_dict: dict = {}
    if filters:
        try:
            filters_dict = json.loads(filters)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="filters 参数格式错误,应为 JSON")

    # 获取字段定义(用于序列化和筛选校验)
    ds = db.scalar(select(DataSource).where(DataSource.code == code))
    if not ds:
        raise HTTPException(status_code=404, detail=f"数据源未注册: {code}")
    field_defs = list(
        db.scalars(
            select(DataSourceField).where(DataSourceField.source_id == ds.id).order_by(DataSourceField.sort_order)
        )
    )
    field_names = {f.field_name for f in field_defs}
    field_map = {f.field_name: f for f in field_defs}
    # 转为 dict 列表便于访问(避免 SQLAlchemy 对象属性访问问题)
    field_dicts = [
        {"field_name": f.field_name, "field_name_cn": f.field_name_cn, "data_type": f.data_type,
         "semantic_role": f.semantic_role, "is_filterable": f.is_filterable}
        for f in field_defs
    ]

    # 构建查询
    stmt = select(model)

    # 筛选条件
    if filters_dict:
        for key, value in filters_dict.items():
            # 支持 field__op 格式(如 amount__gt)
            parts = key.split("__")
            fname = parts[0]
            op = parts[1] if len(parts) > 1 else "eq"

            if fname not in field_names:
                continue
            if not field_map[fname].is_filterable:
                continue

            col = getattr(model, fname, None)
            if col is None:
                continue

            # 根据 op 构建条件
            if op == "eq":
                stmt = stmt.where(col == value)
            elif op == "ne":
                stmt = stmt.where(col != value)
            elif op == "gt":
                stmt = stmt.where(col > value)
            elif op == "gte":
                stmt = stmt.where(col >= value)
            elif op == "lt":
                stmt = stmt.where(col < value)
            elif op == "lte":
                stmt = stmt.where(col <= value)
            elif op == "contains":
                stmt = stmt.where(col.like(f"%{value}%"))
            elif op == "is_null":
                stmt = stmt.where(col.is_(None))
            elif op == "is_not_null":
                stmt = stmt.where(col.isnot(None))
            elif op == "in":
                if isinstance(value, list):
                    stmt = stmt.where(col.in_(value))

    # 排序(默认按时间字段降序)
    sort_col = None
    if sort_field and sort_field in field_names:
        sort_col = getattr(model, sort_field, None)
    elif default_sort_field and default_sort_field in field_names:
        sort_col = getattr(model, default_sort_field, None)
    elif hasattr(model, "id"):
        sort_col = model.id

    if sort_col:
        if sort_dir == "desc":
            stmt = stmt.order_by(sort_col.desc())
        else:
            stmt = stmt.order_by(sort_col.asc())

    total = 0
    if with_count:
        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    return stmt, field_dicts, total


@router.get("/{code}/export", summary="导出数据源为 Excel")
def export_records(
    code: str,
    db: DBSession,
    _: CurrentUser,
    sort_field: str | None = None,
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    filters: str | None = Query(None, description="JSON 格式筛选条件,与 /records 一致"),
):
    """导出指定数据源的(经筛选/排序的)全量记录为 xlsx,供线下分析。

    - 不分页,导出全部匹配行(上限 EXPORT_MAX_ROWS,超出报 400 提示加筛选)
    - 列头用字段中文名(field_name_cn),无中文名回退英文 field_name,与数据查看页表格一致
    - 时间字段以本地时区字符串写出(与页面 fmtCell 显示口径对齐),数值字段保留原值
    """
    stmt, field_dicts, total = _build_browse_query(code, db, sort_field, sort_dir, filters, with_count=True)
    if total > EXPORT_MAX_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"匹配 {total} 行超过单次导出上限 {EXPORT_MAX_ROWS} 行,请增加筛选条件缩小范围后再导出",
        )

    # 取全部匹配行(已校验未超上限)
    rows = list(db.scalars(stmt))

    # 序列化为 dict 列表 → DataFrame
    records: list[dict] = []
    for row in rows:
        rec: dict[str, Any] = {}
        for fd in field_dicts:
            fname = fd["field_name"]
            v = getattr(row, fname, None)
            if fd["data_type"] == "timestamp" and v is not None:
                # 时间字段输出为本地时区字符串(与前端 fmtCell 显示口径一致,便于分析阅读)
                rec[fname] = v.isoformat()
            elif fd["data_type"] == "numeric" and v is not None:
                try:
                    rec[fname] = float(v)
                except Exception:
                    rec[fname] = None
            else:
                rec[fname] = v
        records.append(rec)

    # 列头:中文名优先,无则回退英文 field_name;列顺序与字段 sort_order 一致
    columns = [fd["field_name"] for fd in field_dicts]
    headers = [fd["field_name_cn"] or fd["field_name"] for fd in field_dicts]
    df = pd.DataFrame(records, columns=columns) if columns else pd.DataFrame()
    if not df.empty:
        df.columns = headers

    # 写入 xlsx(openpyxl)。先清洗非法控制字符(openpyxl IllegalCharacterError)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        clean_df_for_excel(df).to_excel(writer, index=False, sheet_name="数据")
    buf.seek(0)

    # 文件名:数据源中文名_导出_yyyymmdd_hhmmss.xlsx(UTF-8 编码,兼容中文)
    ds = db.scalar(select(DataSource).where(DataSource.code == code))
    name_cn = ds.name_cn if ds else code
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = quote(f"{name_cn}_导出_{ts}.xlsx")

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
    )


@router.get("/{code}/summary", response_model=R, summary="数据源汇总统计")
def get_source_summary(code: str, db: DBSession, _: CurrentUser):
    """获取数据源的汇总统计信息:记录数、时间范围、SN统计等"""
    model, time_field = _get_source_model(code)
    if not model:
        raise HTTPException(status_code=404, detail=f"数据源不存在: {code}")

    ds = db.scalar(select(DataSource).where(DataSource.code == code))
    if not ds:
        raise HTTPException(status_code=404, detail=f"数据源未注册: {code}")

    # 记录总数
    total_count = db.scalar(select(func.count()).select_from(model)) or 0

    # 时间范围
    time_col = getattr(model, time_field, None) if time_field else None
    time_min = None
    time_max = None
    if time_col:
        time_min = db.scalar(select(func.min(time_col)))
        time_max = db.scalar(select(func.max(time_col)))

    # SN统计
    sn_field = ds.sn_field
    sn_col = getattr(model, sn_field, None) if sn_field else None
    sn_stats = {}
    if sn_col:
        sn_count = db.scalar(select(func.count(func.distinct(sn_col)))) or 0
        # TOP 10 SN 出现次数
        sn_top = db.execute(
            select(sn_col, func.count(sn_col).label("cnt"))
            .where(sn_col.isnot(None))
            .group_by(sn_col)
            .order_by(func.count(sn_col).desc())
            .limit(10)
        ).fetchall()
        sn_stats = {
            "unique_count": sn_count,
            "top_list": [{"sn": str(row[0]), "count": row[1]} for row in sn_top],
        }

    # 返回结果
    result = {
        "code": code,
        "name_cn": ds.name_cn,
        "table_name": ds.table_name,
        "total_count": total_count,
        "time_range": {
            "min": time_min.isoformat() if time_min else None,
            "max": time_max.isoformat() if time_max else None,
        },
        "sn_stats": sn_stats,
    }

    return {"code": 0, "message": "ok", "data": result}

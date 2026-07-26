"""数据源元数据管理路由"""

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.models.data_source import DataSource, DataSourceField
from app.schemas.common import R
from app.schemas.data_source import (
    DataSourceDetail,
    DataSourceFieldOut,
    DataSourceFieldUpdate,
    DataSourceOut,
    DataSourceUpdate,
)
from app.services.data_source_registry import ensure_data_sources_registered

router = APIRouter(prefix="/data-sources", tags=["系统-数据源"])


def _with_field_count(db, ds: DataSource) -> DataSourceOut:
    cnt = db.scalar(select(func.count()).select_from(DataSourceField).where(DataSourceField.source_id == ds.id)) or 0
    out = DataSourceOut.model_validate(ds)
    out.field_count = cnt
    return out


@router.get("", response_model=R, summary="数据源列表")
def list_sources(db: DBSession, _: CurrentUser):
    items = list(db.scalars(select(DataSource).order_by(DataSource.id)))
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

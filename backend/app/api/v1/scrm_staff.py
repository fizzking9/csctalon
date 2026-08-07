"""SCRM 人设信息 (scrm_staff_profiles) API

提供: 列表/详情/更新(手动绑定 person/user)/匹配建议
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DBSession
from app.models.scrm_staff_profile import ScrmStaffProfile
from app.schemas.common import PageBase as PagedOut
from app.schemas.scrm_staff import (
    ScrmStaffMatchCandidate,
    ScrmStaffProfileOut,
    ScrmStaffProfileUpdate,
)
from app.services import scrm_staff_service as svc

router = APIRouter(prefix="/scrm-staff", tags=["SCRM 人设信息"])


@router.get("", response_model=PagedOut[ScrmStaffProfileOut])
def list_scrm_staff(
    db: DBSession,
    _user: CurrentUser,
    source_config_id: int | None = Query(default=None),
    match_status: str | None = Query(default=None, pattern=r"^(unmatched|auto_matched|manual_confirmed|rejected)?$"),
    enterprise_suffix: str | None = None,
    company_group: str | None = None,
    keyword: str | None = None,
    only_unmatched: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
):
    rows, total = svc.list_staff(
        db,
        source_config_id=source_config_id,
        match_status=match_status,
        enterprise_suffix=enterprise_suffix,
        company_group=company_group,
        keyword=keyword,
        only_unmatched=only_unmatched,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return PagedOut(items=rows, total=total, page=page, page_size=page_size)


@router.get("/{staff_id}", response_model=ScrmStaffProfileOut)
def get_scrm_staff(
    staff_id: int,
    db: DBSession,
    _user: CurrentUser,
):
    row = svc.get_staff(db, staff_id)
    if not row:
        raise HTTPException(404, "未找到 SCRM 人设信息")
    return row


@router.patch("/{staff_id}", response_model=ScrmStaffProfileOut)
def update_scrm_staff(
    staff_id: int,
    payload: ScrmStaffProfileUpdate,
    db: DBSession,
    _user: CurrentUser,
):
    row = svc.update_staff(db, staff_id, payload)
    if not row:
        raise HTTPException(404, "未找到 SCRM 人设信息")
    db.commit()
    db.refresh(row)
    return row


@router.get("/{staff_id}/match-suggestions", response_model=list[ScrmStaffMatchCandidate])
def get_match_suggestions(
    staff_id: int,
    db: DBSession,
    _user: CurrentUser,
    top_k: int = Query(default=5, ge=1, le=20),
):
    row = svc.get_staff(db, staff_id)
    if not row:
        raise HTTPException(404, "未找到 SCRM 人设信息")
    return svc.suggest_matches_for_staff(db, row, top_k=top_k)

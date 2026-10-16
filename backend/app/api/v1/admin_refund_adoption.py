"""退款五路采纳「人工覆盖」管理端点 (#332 退款五路数据标准层, T4)

设计说明（Design Rationale）:
- 目的(替谁/答何决策): 替运营/数据治理同学提供「机器采纳结论的人工纠偏」入口。
  机器规则(app.services.governance.refund_adoption)按白名单给每行 ods 盖三态,
  但个别行业务上确需推翻机器结论(如普查后新确认的特例), 必须留痕可审计,
  禁止直接改库无迹可循。
- 逻辑(为何这样组织):
  - POST /overrides 提交覆盖(route+src_pk+target_state+reason) → upsert 进
    adoption_overrides(唯一键 (route,src_pk), 重复提交=更新最新生效), 写
    system_event 审计(category=governance), 可选 rerun 立即重跑该路采纳;
  - GET /overrides 分页查看现有覆盖; DELETE /overrides/{id} 撤销(删除行,
    撤销后该原始行在下一次采纳作业时回到机器结论);
  - 提交/撤销本身不改 ods 四列 —— ods.adopt_state 只在采纳作业重算时统一刷新
    (LEFT JOIN adoption_overrides), 保证「判定只在一个地方发生」。
- 口径: docs/standards/退款五路数据标准与采纳规则.md(META, 权威);
  三态白名单与路别白名单直接复用 refund_adoption 模块常量, 不另定义。
- 取舍(不做什么及原因): ① 一期不保留覆盖历史版本(机器码在 ods.adopt_reason、
  人工最新在 overrides、审计在 system_event, 三方已可对照); ② 不提供批量导入
  (人工纠偏应逐条确认理由); ③ 撤销不做「立即回刷」, 由下一次采纳作业统一处理,
  避免 API 内联大事务。
- 工单: #332 · 规范: docs/standards/error-code.md(响应格式)
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app.api.deps import AdminUser, DBSession
from app.models.refund_standard import AdoptionOverride
from app.schemas.common import R
from app.services.governance.jobs import (
    run_adoption_job,
    run_dim_device_merchant_job,
)
from app.services.governance.refund_adoption import (
    ROUTES,
    ST_ADOPTED,
    ST_EXCLUDED,
    ST_PENDING,
)
from app.services.system_event_service import record_system_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/refund-adoption", tags=["admin-refund-adoption"])

# 三态白名单(与采纳作业常量同源, 禁止外部新造状态词)
_TARGET_STATES = (ST_ADOPTED, ST_EXCLUDED, ST_PENDING)


class OverrideIn(BaseModel):
    """提交人工覆盖的请求体。枚举非法/理由为空 → 422(error-code 规范)。"""

    route: str = Field(..., description="路别: A/B/C/D/E")
    src_pk: str = Field(..., min_length=1, max_length=128, description="原始行主键值(字符串化)")
    target_state: Literal["adopted", "excluded", "pending"] = Field(..., description="人工目标三态")
    reason: str = Field(..., min_length=1, description="人工覆盖理由(必填, 审计用)")
    rerun: bool = Field(default=False, description="提交后立即重跑该路采纳作业")

    @field_validator("route")
    @classmethod
    def _route_whitelist(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in ROUTES:
            raise ValueError(f"非法路别 {v!r}; 合法值: {list(ROUTES)}")
        return v

    @field_validator("reason")
    @classmethod
    def _reason_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("覆盖理由必填")
        return v


def _serialize(o: AdoptionOverride) -> dict:
    return {
        "id": o.id,
        "route": o.route,
        "src_pk": o.src_pk,
        "target_state": o.target_state,
        "reason": o.reason,
        "operator": o.operator,
        "created_at": o.created_at.isoformat() if o.created_at else None,
    }


@router.post("/overrides", response_model=R, summary="提交采纳人工覆盖(admin)")
def submit_override(req: OverrideIn, db: DBSession, user: AdminUser):
    """upsert (route, src_pk) 覆盖行 + 审计; rerun=True 时同步重跑该路采纳。"""
    row = db.scalar(
        select(AdoptionOverride).where(
            AdoptionOverride.route == req.route,
            AdoptionOverride.src_pk == req.src_pk,
        )
    )
    is_update = row is not None
    if row is None:
        row = AdoptionOverride(route=req.route, src_pk=req.src_pk)
        db.add(row)
    # 重复提交 = 更新最新生效(唯一键保证不产生第二条)
    row.target_state = req.target_state
    row.reason = req.reason
    row.operator = user.username
    db.commit()
    db.refresh(row)

    # 审计留痕(TR-4.2): system_event 记提交动作, 详情含前后文
    record_system_event(
        db,
        category="governance",
        action="adoption_override",
        title=f"采纳人工覆盖 {req.route}/{req.src_pk} → {req.target_state}",
        summary=("更新" if is_update else "新建") + f" by {user.username}",
        status="success",
        detail=f"route={req.route}\nsrc_pk={req.src_pk}\ntarget_state={req.target_state}\n"
               f"reason={req.reason}\noperator={user.username}",
        badge=True,  # 人工纠偏属需管理员可见的治理动作
    )

    data: dict = {"override": _serialize(row), "updated": is_update}
    if req.rerun:
        # 复用 T6 带锁 runner(与调度器/admin 手动触发同一互斥源);
        # 作业在跑时不排队, 覆盖行已落库, 下轮调度会自动生效。
        data["rerun"] = run_adoption_job(db, routes=req.route, trigger="override_rerun")
        if data["rerun"].get("skipped"):
            logger.info("adoption override rerun skipped(作业在运行) route=%s", req.route)
            return R(data=data, message="覆盖已提交; 采纳作业运行中未重跑, 下轮调度自动生效")
        logger.info(
            "adoption override rerun route=%s by %s: %s",
            req.route, user.username, data["rerun"]["routes"].get(req.route),
        )
    return R(data=data, message="覆盖已提交" + ("并重跑该路采纳" if req.rerun else ""))


@router.get("/overrides", response_model=R, summary="人工覆盖列表(admin)")
def list_overrides(
    db: DBSession,
    _: AdminUser,
    route: str | None = Query(default=None, description="按路别过滤(A-E)"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=200),
):
    stmt = select(AdoptionOverride)
    if route:
        r = route.strip().upper()
        if r not in ROUTES:
            raise HTTPException(status_code=422, detail=f"非法路别 {route!r}; 合法值: {list(ROUTES)}")
        stmt = stmt.where(AdoptionOverride.route == r)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(db.scalars(
        stmt.order_by(AdoptionOverride.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ))
    return R(data={
        "total": total, "page": page, "page_size": page_size,
        "items": [_serialize(o) for o in items],
    })


@router.delete("/overrides/{override_id}", response_model=R, summary="撤销人工覆盖(admin)")
def revoke_override(override_id: int, db: DBSession, user: AdminUser):
    """删除覆盖行; 该原始行在下一次采纳作业时回到机器结论。"""
    row = db.get(AdoptionOverride, override_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"覆盖记录 {override_id} 不存在")
    snapshot = _serialize(row)
    db.delete(row)
    db.commit()
    record_system_event(
        db,
        category="governance",
        action="adoption_override_revoke",
        title=f"撤销采纳人工覆盖 {snapshot['route']}/{snapshot['src_pk']}",
        summary=f"by {user.username}(原目标 {snapshot['target_state']})",
        status="success",
        detail=f"revoked={snapshot}\noperator={user.username}",
        badge=True,
    )
    return R(data={"revoked": snapshot}, message="覆盖已撤销, 下次采纳作业回到机器结论")


# ── T6: 手动触发作业(与调度器共用 advisory lock 互斥源) ─────────────────────


class RunIn(BaseModel):
    """手动触发采纳作业: 不传 routes = 五路全跑。"""

    routes: list[Literal["A", "B", "C", "D", "E"]] | None = Field(
        default=None, description="指定路别序列; null=五路全跑"
    )


@router.post("/run", response_model=R, summary="手动触发五路采纳作业(admin)")
def run_adoption(req: RunIn, db: DBSession, _: AdminUser):
    """并发第二次 → 409(不排队等锁, 作业幂等, 下轮调度自然生效)。"""
    result = run_adoption_job(db, routes=req.routes, trigger="admin_api")
    if result.get("skipped"):
        raise HTTPException(status_code=409, detail="采纳作业已在运行, 请稍后再试")
    return R(data=result, message="采纳作业完成")


@router.post("/dim-device-merchant/rebuild", response_model=R, summary="手动重建E路企业商户维表(admin)")
def rebuild_dim_device_merchant(db: DBSession, _: AdminUser):
    result = run_dim_device_merchant_job(db, trigger="admin_api")
    if result.get("skipped"):
        raise HTTPException(status_code=409, detail="维表重建已在运行, 请稍后再试")
    return R(data=result, message="维表重建完成")

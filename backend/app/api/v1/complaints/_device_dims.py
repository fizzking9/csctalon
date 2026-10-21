"""投诉设备维度汇总(按渠道可切): complaint_device_dims.

设计说明（Design Rationale）
- 目的(替谁/答何决策): 班组长/运营回答「本期投诉集中在哪类设备/套餐/商户/激活阶段」,
  并能按渠道(内部/商户号/外部/卡商)拆分, 定位质量问题 vs 服务问题的归因差异。
- 逻辑(为何这样组织): 复用积压页 _device_dim_groups(设备类型/套餐/商户号/激活时长五桶 #295),
  保证本页 ↔ 概览A6 ↔ 积压明细 同源自洽(点击桶 → 筛选行数 = 桶计数)。
  与概览A6差异: 本页支持 source_type 渠道过滤(概览A6=三源合计剔卡商)。
- 口径: 投诉判据 = 工单 service_category 空白或='投诉'(complaint_scope 唯一出口);
  设备型号/套餐取 bms_orders 最新订单; 商户号=devices表; 激活=bms最早∪devices兜底。
  卡商来源=card_complaints 表(独立体系, 不入总量)。
- 取舍: ❌ 不做金额维度(投诉工单金额字段稀疏);
        ❌ 不做跨表去重(事件口径, 同一SN多渠道登记算多次投诉事件);
        source_type=None → 三源合计剔卡商(与概览A6完全一致, 保证自洽)。
- 工单: #67(投诉分析整体重构) · 新增「设备维度」独立tab
"""

from typing import Annotated

from fastapi import Query
from sqlalchemy import column, select, table

from app.api.deps import CurrentUser, DBSession

# 复用积压页的设备四维分组函数, 保证同源自洽
from app.api.v1.complaints._backlog import _device_dim_groups
from app.api.v1.complaints._helpers import _parse_dt, _parse_dt_end
from app.api.v1.complaints._router import router
from app.core.redis_client import cache_ttl_for, cached_json
from app.models.ticket import ServiceTicket

# #338: 卡商投诉读 std_all 镜像
_CC = table("std_card_complaint_all", column("complaint_date"), column("device_sn"))
from app.schemas.common import R
from app.services.analytics import _complaint_source_type_case, _ticket_category_case
from app.services.complaint_scope import CARD_SCOPE


@router.get("/device-dims", response_model=R,
            summary="投诉设备维度汇总(按渠道可切: 内部/商户号/外部/卡商) #67")
@cached_json(
    lambda *a, **kw: (
        f"complaint:device-dims:{kw.get('start')}:{kw.get('end')}:{kw.get('source_type')}"
    ),
    ttl=lambda **kw: cache_ttl_for(kw.get('start'), kw.get('end'), 120),
)
def complaint_device_dims(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    source_type: Annotated[str | None, Query(
        description="渠道过滤: 内部投诉/商户号投诉/外部投诉/(外)卡商; None=三源合计剔卡商"
    )] = None,
):
    """投诉设备维度四维汇总(设备类型/套餐/商户号/激活时长), 支持按渠道拆分。

    ★ 与概览 device_dims 同源自洽: source_type=None 时返回值 ≡ /overview 的 device_dims。
    ★ 卡商来源=card_complaints 表(独立体系, 不入总量); 其余三源=工单侧分桶。
    """
    ts = _parse_dt(start)
    te = _parse_dt_end(end)

    rows: list[dict] = []

    # ── 工单侧: 内部投诉 / 商户号投诉 / 外部投诉 ──
    #   source_type=None → 三源合计剔卡商(与概览A6 device_dims 完全一致, 保证自洽)
    if source_type != CARD_SCOPE:
        src_expr = _complaint_source_type_case()
        conds = [
            _ticket_category_case() == "投诉",
            src_expr != CARD_SCOPE,
            ServiceTicket.register_time.isnot(None),
        ]
        if ts:
            conds.append(ServiceTicket.register_time >= ts)
        if te:
            conds.append(ServiceTicket.register_time <= te)
        if source_type:
            conds.append(src_expr == source_type)

        ticket_rows = db.execute(
            select(ServiceTicket.device_sn, ServiceTicket.register_time).where(*conds)
        ).all()
        rows.extend({"device_sn": r[0], "register_time": r[1]} for r in ticket_rows)

    # ── 卡商侧: card_complaints 表(独立体系, 仅 source_type=(外)卡商 时取) ──
    if source_type == CARD_SCOPE:
        card_conds = [_CC.c.complaint_date.isnot(None)]
        if ts:
            card_conds.append(_CC.c.complaint_date >= ts)
        if te:
            card_conds.append(_CC.c.complaint_date <= te)
        card_rows = db.execute(
            select(_CC.c.device_sn, _CC.c.complaint_date).where(*card_conds)
        ).all()
        rows.extend({"device_sn": r[0], "register_time": r[1]} for r in card_rows)

    device_dims = _device_dim_groups(db, rows)

    return R(data={
        "range": {
            "start": str(ts.date()) if ts else None,
            "end": str(te.date()) if te else None,
            "source_type": source_type or "all",
        },
        "device_dims": device_dims,
        "total": len(rows),
    })

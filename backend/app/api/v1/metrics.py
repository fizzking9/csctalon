"""运营指标路由 - 指标水位/四级级别/阈值参数配置(留痕) + 指标字典(#257 B4-1)

指标口径与看板同源(metric_registry_service 复用 analytics 计算)。
指标字典(metric_dictionary)是口径的单点登记处, 回答"这个数字怎么算的";
与 MetricRegistry(预警水位)职责不同, 两者并存。
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import or_, select

from app.api.deps import CurrentUser, DBSession, ManagerUser
from app.models.metric import MetricDailySnapshot, MetricDictionary, MetricThreshold
from app.schemas.common import R
from app.services.metric_registry_service import (
    METRIC_DEFS,
    evaluate_metric,
    get_threshold,
    list_metrics_status,
)

router = APIRouter(prefix="/metrics", tags=["运营指标"])


@router.get("", response_model=R, summary="指标注册表 + 当前值/级别(水位视图)")
def list_metrics(db: DBSession, _: CurrentUser):
    items = list_metrics_status(db)
    return R(data={"items": items, "total": len(items)})


@router.get("/{code}/current", response_model=R, summary="单指标当前值 + 级别 + 近30天快照")
def metric_current(code: str, db: DBSession, _: CurrentUser):
    if code not in METRIC_DEFS:
        raise HTTPException(status_code=404, detail="指标未注册")
    cur = evaluate_metric(db, code)
    thr = get_threshold(db, code)
    snapshots = db.scalars(
        select(MetricDailySnapshot)
        .where(MetricDailySnapshot.metric_code == code)
        .order_by(MetricDailySnapshot.period_date.desc())
        .limit(30)
    ).all()
    return R(data={
        "current": cur,
        "threshold": {
            "id": thr.id if thr else None,
            "excellent_value": thr.excellent_value if thr else None,
            "warning_value": thr.warning_value if thr else None,
            "critical_value": thr.critical_value if thr else None,
        },
        "snapshots": [
            {"period_date": s.period_date, "value": s.value, "level": s.level, "data_ok": s.data_ok}
            for s in snapshots
        ],
    })


@router.put("/{code}/thresholds", response_model=R, summary="配置指标阈值(留痕: 旧版本关闭 valid_to)")
def update_thresholds(code: str, payload: dict, db: DBSession, _: ManagerUser):
    if code not in METRIC_DEFS:
        raise HTTPException(status_code=404, detail="指标未注册")
    warning_value = payload.get("warning_value")
    critical_value = payload.get("critical_value")
    if warning_value is None or critical_value is None:
        raise HTTPException(status_code=400, detail="warning_value / critical_value 必填")
    now = datetime.now(timezone.utc)
    # 关闭当前生效版本(留痕)
    cur = get_threshold(db, code)
    if cur:
        cur.valid_to = now
        db.flush()
    thr = MetricThreshold(
        metric_code=code,
        excellent_value=payload.get("excellent_value"),
        warning_value=float(warning_value),
        critical_value=float(critical_value),
        valid_from=now,
        updated_by=getattr(_, "id", None),
    )
    db.add(thr)
    db.commit()
    return R(message="阈值已更新", data={"metric_code": code, "id": thr.id})


@router.get("/{code}/thresholds/history", response_model=R, summary="阈值历史(调参留痕)")
def threshold_history(code: str, db: DBSession, _: CurrentUser):
    rows = db.scalars(
        select(MetricThreshold)
        .where(MetricThreshold.metric_code == code)
        .order_by(MetricThreshold.id.desc())
        .limit(50)
    ).all()
    return R(data={"items": [
        {
            "id": t.id,
            "excellent_value": t.excellent_value,
            "warning_value": t.warning_value,
            "critical_value": t.critical_value,
            "valid_from": t.valid_from.isoformat() if t.valid_from else None,
            "valid_to": t.valid_to.isoformat() if t.valid_to else None,
        }
        for t in rows
    ]})


# ─────────────────────────────────────────────────────────────────────
# 指标字典 (#257 B4-1) — 口径单点登记
#
# 设计说明（Design Rationale）:
#   目的: 替运营主管/新人回答「这个数字怎么算的」—— 指标语义(含什么/排什么/
#         时点/公式/来源表)此前分散在各看板 SQL 与前端文案中, 无单点登记处。
#   逻辑: 字典按 category 分域(接待/退款/投诉/现金流/绩效), code=域.名自然主键,
#         name_cn 全站唯一(同数据源命名纪律); 每条 definition 写实现指针,
#         谁改口径谁更新字典, 保证字典与代码不漂移。
#   口径: 种子口径逐条对照代码现状(today/_kpi v4 四路退款、#78 接待分层、
#         #81 三源投诉、#82 挽回率), 实现位置写在各条 remark。
#   取舍: ① 不一次性改 75+ 页面卡片引用指标 code —— 渐进迁移, 谁改动、谁挂接;
#         ② 不做指标自动计算引擎 —— 字典是口径登记处, 计算仍由看板接口执行;
#         ③ 不做前端管理页 —— v1 由 API 维护(管理员), 数据量小。
#   工单: #257 (排期 §七 B4-1, S-P1-1)
# ─────────────────────────────────────────────────────────────────────

_DICT_CATEGORIES = {"reception", "refund", "complaint", "cash", "performance"}


def _dict_item(d: MetricDictionary) -> dict:
    return {
        "code": d.code,
        "name_cn": d.name_cn,
        "definition": d.definition,
        "formula": d.formula,
        "unit": d.unit,
        "category": d.category,
        "source_tables": d.source_tables,
        "owner": d.owner,
        "is_active": d.is_active,
        "remark": d.remark,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


@router.get("/dict", response_model=R, summary="指标字典列表(口径单点登记)")
def list_metric_dict(
    db: DBSession,
    _: CurrentUser,
    category: str | None = None,
    keyword: str | None = None,
    include_inactive: bool = False,
):
    """列表查询: category 过滤(接待/退款/投诉/现金流/绩效) + keyword 模糊(code/名称/口径)。

    include_inactive=False 时不返回停用口径(默认只看现行)。
    """
    q = select(MetricDictionary)
    if not include_inactive:
        q = q.where(MetricDictionary.is_active.is_(True))
    if category:
        if category not in _DICT_CATEGORIES:
            raise HTTPException(status_code=400, detail=f"category 须为 {sorted(_DICT_CATEGORIES)} 之一")
        q = q.where(MetricDictionary.category == category)
    if keyword:
        kw = f"%{keyword.strip()}%"
        q = q.where(or_(
            MetricDictionary.code.ilike(kw),
            MetricDictionary.name_cn.ilike(kw),
            MetricDictionary.definition.ilike(kw),
        ))
    rows = db.scalars(q.order_by(MetricDictionary.category, MetricDictionary.code)).all()
    return R(data={"items": [_dict_item(d) for d in rows], "total": len(rows)})


@router.post("/dict", response_model=R, summary="登记指标口径(管理员)")
def create_metric_dict(payload: dict, db: DBSession, user: ManagerUser):
    code = (payload.get("code") or "").strip()
    name_cn = (payload.get("name_cn") or "").strip()
    definition = (payload.get("definition") or "").strip()
    category = (payload.get("category") or "").strip()
    if not code or not name_cn or not definition or not category:
        raise HTTPException(status_code=400, detail="code / name_cn / definition / category 必填")
    if "." not in code:
        raise HTTPException(status_code=400, detail="code 须为 域.名 形式(如 reception.session_count)")
    if category not in _DICT_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"category 须为 {sorted(_DICT_CATEGORIES)} 之一")
    if db.get(MetricDictionary, code):
        raise HTTPException(status_code=409, detail=f"指标 {code} 已登记")
    # name_cn 全站唯一(数据库 unique 兜底, 这里先查给出可读错误)
    dup_name = db.scalar(select(MetricDictionary).where(MetricDictionary.name_cn == name_cn))
    if dup_name:
        raise HTTPException(status_code=409, detail=f"显示名「{name_cn}」已被 {dup_name.code} 占用(全站唯一)")
    d = MetricDictionary(
        code=code, name_cn=name_cn, definition=definition,
        formula=payload.get("formula"), unit=payload.get("unit"),
        category=category, source_tables=payload.get("source_tables"),
        owner=payload.get("owner"),
        is_active=payload.get("is_active", True),
        remark=payload.get("remark"),
    )
    db.add(d)
    db.commit()
    return R(message="指标已登记", data=_dict_item(d))


@router.put("/dict/{code}", response_model=R, summary="更新指标口径(管理员; 建议在 remark 记录变更背景)")
def update_metric_dict(code: str, payload: dict, db: DBSession, user: ManagerUser):
    d = db.get(MetricDictionary, code)
    if not d:
        raise HTTPException(status_code=404, detail=f"指标 {code} 未登记")
    # code 是自然主键不可变; 其余字段按提交更新(未提交字段保持原值)
    if "name_cn" in payload:
        v = (payload["name_cn"] or "").strip()
        if not v:
            raise HTTPException(status_code=400, detail="name_cn 不能为空")
        dup = db.scalar(select(MetricDictionary).where(
            MetricDictionary.name_cn == v, MetricDictionary.code != code))
        if dup:
            raise HTTPException(status_code=409, detail=f"显示名「{v}」已被 {dup.code} 占用(全站唯一)")
        d.name_cn = v
    if "category" in payload:
        if payload["category"] not in _DICT_CATEGORIES:
            raise HTTPException(status_code=400, detail=f"category 须为 {sorted(_DICT_CATEGORIES)} 之一")
        d.category = payload["category"]
    for field in ("definition", "formula", "unit", "source_tables", "owner", "is_active", "remark"):
        if field in payload:
            setattr(d, field, payload[field])
    db.commit()
    return R(message="指标口径已更新", data=_dict_item(d))

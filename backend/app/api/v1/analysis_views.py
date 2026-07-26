"""分析视图与分类规则路由 - 自助分析引擎 API

- 视图 CRUD + execute(支持已存视图或 inline 预览)
- 分类规则 CRUD + preview
"""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DBSession
from app.models.analysis_view import AnalysisView, ClassificationRule
from app.models.data_source import DataSource
from app.schemas.analysis_view import (
    AnalysisViewCreate,
    AnalysisViewExecuteRequest,
    AnalysisViewOut,
    AnalysisViewUpdate,
    ClassificationRuleCreate,
    ClassificationRuleOut,
    ClassificationRulePreviewRequest,
    ClassificationRuleUpdate,
)
from app.schemas.common import PageBase, R
from app.services.analysis_engine import _build_classification_case, _execute_segment, _get_column, _MODEL_REGISTRY, execute_view
from sqlalchemy import case

router = APIRouter(prefix="/analysis-views", tags=["分析-视图"])


# ---------- 视图 CRUD ----------


@router.get("", response_model=R, summary="分析视图列表(分页)")
def list_views(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    preset: bool | None = Query(None, description="筛选预设/自定义"),
    status: str | None = Query(None, description="active/archived"),
    keyword: str | None = Query(None, description="名称搜索"),
):
    stmt = select(AnalysisView)
    if preset is not None:
        stmt = stmt.where(AnalysisView.is_preset.is_(preset))
    if status:
        stmt = stmt.where(AnalysisView.status == status)
    else:
        stmt = stmt.where(AnalysisView.status == "active")
    if keyword:
        stmt = stmt.where(AnalysisView.name.ilike(f"%{keyword}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(
        db.scalars(
            stmt.order_by(AnalysisView.sort_order, AnalysisView.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return R(data=PageBase(items=[AnalysisViewOut.model_validate(v) for v in items], total=total, page=page, page_size=page_size))


@router.get("/{view_id}", response_model=R, summary="视图详情")
def get_view(db: DBSession, _: CurrentUser, view_id: int):
    v = db.get(AnalysisView, view_id)
    if not v:
        raise HTTPException(404, "视图不存在")
    return R(data=AnalysisViewOut.model_validate(v))


@router.post("", response_model=R, summary="创建视图")
def create_view(db: DBSession, user: CurrentUser, payload: AnalysisViewCreate):
    v = AnalysisView(
        code=payload.code,
        name=payload.name,
        description=payload.description,
        chart_type=payload.chart_type,
        segments=payload.segments,
        merge_strategy=payload.merge_strategy,
        classification_rule_ids=payload.classification_rule_ids,
        time_range_default=payload.time_range_default,
        ratios=payload.ratios,
        is_preset=False,
        status="active",
        owner_id=user.id,
        sort_order=payload.sort_order,
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    return R(data=AnalysisViewOut.model_validate(v))


@router.put("/{view_id}", response_model=R, summary="更新视图")
def update_view(db: DBSession, _: CurrentUser, view_id: int, payload: AnalysisViewUpdate):
    v = db.get(AnalysisView, view_id)
    if not v:
        raise HTTPException(404, "视图不存在")
    data = payload.model_dump(exclude_unset=True)
    for k, val in data.items():
        setattr(v, k, val)
    db.commit()
    db.refresh(v)
    return R(data=AnalysisViewOut.model_validate(v))


@router.delete("/{view_id}", response_model=R, summary="删除视图(预设不可删)")
def delete_view(db: DBSession, _: CurrentUser, view_id: int):
    v = db.get(AnalysisView, view_id)
    if not v:
        raise HTTPException(404, "视图不存在")
    if v.is_preset:
        raise HTTPException(400, "预设视图不可删除,可改为 archived")
    db.delete(v)
    db.commit()
    return R(message="已删除")


# ---------- 视图执行 ----------


@router.post("/execute", response_model=R, summary="执行视图(已存视图或 inline 预览)")
def execute_view_endpoint(db: DBSession, _: CurrentUser, payload: AnalysisViewExecuteRequest):
    """执行分析视图

    - view_id: 已存视图ID
    - inline_config: inline 配置(ViewBuilder 实时预览用,不入库)
    - params: {start?, end?, filters_override?}
    """
    if payload.view_id:
        v = db.get(AnalysisView, payload.view_id)
        if not v:
            raise HTTPException(404, "视图不存在")
        result = execute_view(db, v, payload.params)
    elif payload.inline_config:
        config = payload.to_view_config()
        result = execute_view(db, config, payload.params)
    else:
        raise HTTPException(400, "必须提供 view_id 或 inline_config")

    return R(data=result)


# ---------- 分类规则 CRUD ----------


rules_router = APIRouter(prefix="/classification-rules", tags=["分析-分类规则"])


@rules_router.get("", response_model=R, summary="分类规则列表")
def list_rules(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    is_active: bool | None = Query(None),
):
    stmt = select(ClassificationRule)
    if is_active is not None:
        stmt = stmt.where(ClassificationRule.is_active.is_(is_active))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(
        db.scalars(stmt.order_by(ClassificationRule.id.desc()).offset((page - 1) * page_size).limit(page_size))
    )
    return R(data=PageBase(items=[ClassificationRuleOut.model_validate(r) for r in items], total=total, page=page, page_size=page_size))


@rules_router.post("", response_model=R, summary="创建分类规则")
def create_rule(db: DBSession, user: CurrentUser, payload: ClassificationRuleCreate):
    r = ClassificationRule(
        name=payload.name,
        description=payload.description,
        rules=payload.rules,
        default_label=payload.default_label,
        is_active=payload.is_active,
        owner_id=user.id,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return R(data=ClassificationRuleOut.model_validate(r))


@rules_router.put("/{rule_id}", response_model=R, summary="更新分类规则")
def update_rule(db: DBSession, _: CurrentUser, rule_id: int, payload: ClassificationRuleUpdate):
    r = db.get(ClassificationRule, rule_id)
    if not r:
        raise HTTPException(404, "规则不存在")
    data = payload.model_dump(exclude_unset=True)
    for k, val in data.items():
        setattr(r, k, val)
    db.commit()
    db.refresh(r)
    return R(data=ClassificationRuleOut.model_validate(r))


@rules_router.delete("/{rule_id}", response_model=R, summary="删除分类规则")
def delete_rule(db: DBSession, _: CurrentUser, rule_id: int):
    r = db.get(ClassificationRule, rule_id)
    if not r:
        raise HTTPException(404, "规则不存在")
    db.delete(r)
    db.commit()
    return R(message="已删除")


@rules_router.post("/preview", response_model=R, summary="预览规则效果(返回各维度值→标签映射)")
def preview_rule(db: DBSession, _: CurrentUser, payload: ClassificationRulePreviewRequest):
    """预览分类规则:对指定数据源的 target_field 应用规则,返回 distinct 值 → 标签的映射"""
    # 加载规则
    if payload.rule_id:
        rule_obj = db.get(ClassificationRule, payload.rule_id)
        if not rule_obj:
            raise HTTPException(404, "规则不存在")
        rules = [rule_obj]
    elif payload.rule:
        # inline 规则,构造临时对象
        from app.models.analysis_view import ClassificationRule as CR

        rule_obj = CR(
            name=payload.rule.name,
            rules=payload.rule.rules,
            default_label=payload.rule.default_label,
            is_active=True,
        )
        rules = [rule_obj]
    else:
        raise HTTPException(400, "必须提供 rule_id 或 rule")

    # 找数据源
    ds = db.scalar(select(DataSource).where(DataSource.code == payload.source_code))
    if not ds:
        raise HTTPException(400, f"数据源未注册: {payload.source_code}")
    model = _MODEL_REGISTRY.get(ds.table_name)
    if not model:
        raise HTTPException(400, f"模型未注册: {ds.table_name}")

    # 查询 distinct target_field 值
    target_col = _get_column(model, payload.target_field)
    rows = db.execute(
        select(target_col).where(target_col.isnot(None)).group_by(target_col).limit(payload.limit)
    ).all()

    # 构建 CASE 表达式查标签
    case_expr = _build_classification_case(model, payload.target_field, rules)
    # 用子查询方式:对每个 distinct 值应用 case
    sub = select(target_col.label("val"), case_expr.label("label")).group_by(target_col).limit(payload.limit)
    result_rows = db.execute(sub).all()

    return R(data={
        "items": [{"value": r.val, "label": r.label} for r in result_rows],
        "total": len(result_rows),
        "default_label": rules[0].default_label,
    })

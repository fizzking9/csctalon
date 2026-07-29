"""报告看板模板路由 - 可自定义编辑的看板框架定义

模板定义 section 列表(key/title/section_type/view_ids/owner_id), 实例化时复制到
WeeklyReport/Report.framework。每种 report_type 有一个默认模板(is_default),
一键生成时用。预设模板(is_preset)不可删除。

实例化(instantiate_framework): 从模板 framework 复制, 补齐实例数据字段
  summary="" / data=None / related_report_ids=[]
"""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select, update

from app.api.deps import AdminOrSupervisor, CurrentUser, DBSession
from app.models.report_template import ReportTemplate
from app.schemas.common import PageBase, R

router = APIRouter(prefix="/report-templates", tags=["报告看板模板"])

VALID_REPORT_TYPES = {"weekly", "special_audit", "custom"}
VALID_SECTION_TYPES = {"view", "text", "measures", "incidents"}
VALID_VISIBILITY = {"public", "manager", "admin"}


# ---------- 序列化与实例化 ----------


def _serialize(t: ReportTemplate) -> dict:
    return {
        "id": t.id,
        "code": t.code,
        "name": t.name,
        "description": t.description,
        "report_type": t.report_type,
        "framework": t.framework,
        "is_default": t.is_default,
        "is_preset": t.is_preset,
        "status": t.status,
        "visibility": t.visibility,
        "owner_id": t.owner_id,
        "sort_order": t.sort_order,
        "created_by": t.created_by,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def instantiate_framework(template_framework: list) -> list:
    """从模板 framework 复制为实例 framework, 补齐实例数据字段。
    模板 section: {key,title,section_type,view_ids[],owner_id,summary_placeholder}
    实例 section: {..., summary, data, related_report_ids}
    兼容旧格式 view_id(单值) → view_ids(列表)。
    """
    result = []
    for sec in template_framework or []:
        if not isinstance(sec, dict) or not sec.get("key"):
            continue
        view_ids = sec.get("view_ids")
        if view_ids is None and sec.get("view_id") is not None:
            view_ids = [sec["view_id"]]
        result.append(
            {
                "key": sec["key"],
                "title": sec.get("title") or sec["key"],
                "section_type": sec.get("section_type") or "view",
                "view_ids": list(view_ids or []),
                "owner_id": sec.get("owner_id"),
                "summary": "",
                "data": None,
                "related_report_ids": [],
            }
        )
    return result


def _normalize_template_framework(framework: list) -> list:
    """规范化模板 framework: 校验 section, 补齐 summary_placeholder。"""
    if not isinstance(framework, list):
        raise HTTPException(400, "framework 必须是数组")
    seen_keys = set()
    result = []
    for sec in framework:
        if not isinstance(sec, dict) or not sec.get("key"):
            raise HTTPException(400, "每个 section 必须含 key")
        key = sec["key"]
        if key in seen_keys:
            raise HTTPException(400, f"section key 重复: {key}")
        seen_keys.add(key)
        section_type = sec.get("section_type") or "view"
        if section_type not in VALID_SECTION_TYPES:
            raise HTTPException(400, f"无效的 section_type: {section_type}")
        view_ids = sec.get("view_ids")
        if view_ids is None and sec.get("view_id") is not None:
            view_ids = [sec["view_id"]]
        result.append(
            {
                "key": key,
                "title": sec.get("title") or key,
                "section_type": section_type,
                "view_ids": list(view_ids or []),
                "owner_id": sec.get("owner_id"),
                "summary_placeholder": sec.get("summary_placeholder") or "",
            }
        )
    return result


# ---------- 模板 CRUD ----------


@router.get("", response_model=R[PageBase], summary="模板列表(分页)")
def list_templates(
    db: DBSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    report_type: str | None = Query(None, description="类型:weekly/special_audit/custom"),
    status: str | None = Query(None, description="active/archived,默认 active"),
    keyword: str | None = Query(None, description="名称搜索"),
):
    stmt = select(ReportTemplate)
    if status:
        stmt = stmt.where(ReportTemplate.status == status)
    else:
        stmt = stmt.where(ReportTemplate.status == "active")
    if report_type:
        stmt = stmt.where(ReportTemplate.report_type == report_type)
    if keyword:
        stmt = stmt.where(ReportTemplate.name.ilike(f"%{keyword}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(
        db.scalars(
            stmt.order_by(ReportTemplate.sort_order, ReportTemplate.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return R(data=PageBase(items=[_serialize(t) for t in items], total=total, page=page, page_size=page_size))


@router.get("/default", response_model=R, summary="按类型取默认模板(一键生成用)")
def get_default_template(db: DBSession, current_user: CurrentUser, report_type: str = Query(..., description="类型")):
    """取指定类型的默认模板(供一键生成周报/专项报告时复制 framework)"""
    t = db.scalar(
        select(ReportTemplate).where(
            ReportTemplate.report_type == report_type,
            ReportTemplate.is_default.is_(True),
            ReportTemplate.status == "active",
        )
    )
    if not t:
        # 退而取该类型第一个 active 模板
        t = db.scalar(
            select(ReportTemplate).where(
                ReportTemplate.report_type == report_type,
                ReportTemplate.status == "active",
            ).order_by(ReportTemplate.sort_order, ReportTemplate.id)
        )
    if not t:
        raise HTTPException(404, f"未找到类型[{report_type}]的模板")
    return R(data=_serialize(t))


@router.get("/{template_id}", response_model=R, summary="模板详情")
def get_template(template_id: int, db: DBSession, current_user: CurrentUser):
    t = db.get(ReportTemplate, template_id)
    if not t:
        raise HTTPException(404, "模板不存在")
    return R(data=_serialize(t))


@router.get("/{template_id}/instantiate", response_model=R, summary="实例化模板框架(供创建周报/报告用)")
def instantiate_template(template_id: int, db: DBSession, current_user: CurrentUser):
    """返回模板 framework 的实例化副本(补齐 summary/data/related_report_ids 空值)"""
    t = db.get(ReportTemplate, template_id)
    if not t:
        raise HTTPException(404, "模板不存在")
    return R(data=instantiate_framework(t.framework))


@router.post("", response_model=R, summary="创建模板(管理员)")
def create_template(payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "模板名不能为空")
    report_type = payload.get("report_type") or "custom"
    if report_type not in VALID_REPORT_TYPES:
        raise HTTPException(400, f"无效的类型: {report_type}")
    framework = _normalize_template_framework(payload.get("framework") or [])
    if not framework:
        raise HTTPException(400, "framework 至少含一个 section")
    visibility = payload.get("visibility") or "manager"
    if visibility not in VALID_VISIBILITY:
        raise HTTPException(400, f"无效的可见性: {visibility}")
    code = (payload.get("code") or "").strip() or None
    if code:
        exists = db.scalar(select(ReportTemplate).where(ReportTemplate.code == code))
        if exists:
            raise HTTPException(400, f"code 已存在: {code}")
    t = ReportTemplate(
        code=code,
        name=name,
        description=payload.get("description"),
        report_type=report_type,
        framework=framework,
        is_default=False,  # 新建默认非默认模板, 需显式 set-default
        is_preset=False,
        status="active",
        visibility=visibility,
        owner_id=payload.get("owner_id"),
        sort_order=int(payload.get("sort_order") or 0),
        created_by=current_user.username,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return R(data=_serialize(t))


@router.put("/{template_id}", response_model=R, summary="更新模板(管理员)")
def update_template(template_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    t = db.get(ReportTemplate, template_id)
    if not t:
        raise HTTPException(404, "模板不存在")
    if "name" in payload:
        t.name = (payload["name"] or "").strip() or t.name
    if "description" in payload:
        t.description = payload["description"]
    if "report_type" in payload:
        rt = payload["report_type"]
        if rt not in VALID_REPORT_TYPES:
            raise HTTPException(400, f"无效的类型: {rt}")
        t.report_type = rt
    if "visibility" in payload:
        v = payload["visibility"]
        if v not in VALID_VISIBILITY:
            raise HTTPException(400, f"无效的可见性: {v}")
        t.visibility = v
    if "owner_id" in payload:
        t.owner_id = payload["owner_id"]
    if "sort_order" in payload:
        t.sort_order = int(payload["sort_order"] or 0)
    if "status" in payload:
        t.status = payload["status"]
    if "framework" in payload:
        t.framework = _normalize_template_framework(payload["framework"])
    # 预设模板的 code 不可改
    if "code" in payload and not t.is_preset:
        new_code = (payload["code"] or "").strip() or None
        if new_code and new_code != t.code:
            exists = db.scalar(select(ReportTemplate).where(ReportTemplate.code == new_code))
            if exists:
                raise HTTPException(400, f"code 已存在: {new_code}")
            t.code = new_code
    db.commit()
    db.refresh(t)
    return R(data=_serialize(t))


@router.delete("/{template_id}", response_model=R, summary="删除模板(预设软删,不可硬删)")
def delete_template(template_id: int, db: DBSession, current_user: AdminOrSupervisor):
    t = db.get(ReportTemplate, template_id)
    if not t:
        raise HTTPException(404, "模板不存在")
    if t.is_preset:
        # 预设模板不可硬删, 仅软删(归档)
        if t.status != "archived":
            t.status = "archived"
            # 取消默认标识
            if t.is_default:
                t.is_default = False
            db.commit()
        return R(message="预设模板已归档(不可硬删)")
    db.delete(t)
    db.commit()
    return R(message="已删除")


# ---------- 复制 / 设为默认 ----------


@router.post("/{template_id}/copy", response_model=R, summary="复制模板(另存为自定义)")
def copy_template(template_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    """复制模板生成新的自定义模板(可基于预设/已有模板二次定制)。
    payload: {name?, description?}
    """
    src = db.get(ReportTemplate, template_id)
    if not src:
        raise HTTPException(404, "源模板不存在")
    name = (payload.get("name") or f"{src.name} 副本").strip()
    t = ReportTemplate(
        code=None,  # 副本不带 code, 避免唯一冲突
        name=name,
        description=payload.get("description") or src.description,
        report_type=src.report_type,
        framework=_normalize_template_framework(src.framework),
        is_default=False,
        is_preset=False,  # 副本始终为自定义
        status="active",
        visibility=src.visibility,
        owner_id=current_user.id,
        sort_order=src.sort_order,
        created_by=current_user.username,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return R(data=_serialize(t))


@router.post("/{template_id}/set-default", response_model=R, summary="设为该类型默认模板")
def set_default_template(template_id: int, db: DBSession, current_user: AdminOrSupervisor):
    """设为该 report_type 的默认模板(同类型其他模板取消默认, 唯一默认)。"""
    t = db.get(ReportTemplate, template_id)
    if not t:
        raise HTTPException(404, "模板不存在")
    if t.status != "active":
        raise HTTPException(400, "归档模板不能设为默认")
    # 同类型其他模板取消默认
    db.execute(
        update(ReportTemplate)
        .where(
            ReportTemplate.report_type == t.report_type,
            ReportTemplate.id != t.id,
            ReportTemplate.is_default.is_(True),
        )
        .values(is_default=False)
    )
    t.is_default = True
    db.commit()
    db.refresh(t)
    return R(data=_serialize(t))

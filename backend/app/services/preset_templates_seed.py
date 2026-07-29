"""预设看板模板种子 - 周报/专项审计默认模板

幂等注册: 按 code upsert, 已存在的预设模板智能合并更新(保留用户配置的view_ids/owner_id)
启动时由 main.py lifespan 调用
"""

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.report_template import ReportTemplate

# 周报10项标准框架(与 weekly_report.WEEKLY_FRAMEWORK_SECTIONS 一致)
_WEEKLY_SECTIONS = [
    {"key": "measure_followup", "title": "措施跟进", "section_type": "measures"},
    {"key": "reception_trend", "title": "总体接待人均趋势", "section_type": "view"},
    {"key": "reception_summary", "title": "分项接待汇总", "section_type": "view"},
    {"key": "group_reception", "title": "各组接待量", "section_type": "view"},
    {"key": "refund_recovery", "title": "退款挽回专项", "section_type": "view"},
    {"key": "complaint", "title": "投诉专项(内+外315)", "section_type": "view"},
    {"key": "issues", "title": "问题汇总", "section_type": "text"},
    {"key": "measure_plan", "title": "措施计划(输出下周)", "section_type": "measures"},
    {"key": "cross_dept", "title": "跨部门协调(含上游打分)", "section_type": "text"},
    {"key": "incidents", "title": "异常事故", "section_type": "incidents"},
]

PRESET_TEMPLATES: list[dict] = [
    {
        "code": "preset_template_weekly",
        "name": "周报默认模板",
        "description": "原10项标准框架(措施跟进/接待趋势/退款挽回/投诉/异常等)",
        "report_type": "weekly",
        "is_default": True,
        "is_preset": True,
        "visibility": "manager",
        "framework": [
            {**s, "view_ids": [], "owner_id": None, "summary_placeholder": ""}
            for s in _WEEKLY_SECTIONS
        ],
    },
    {
        "code": "preset_template_special_audit",
        "name": "专项审计默认模板",
        "description": "专项审查报告标准框架(概述/证据/结论)",
        "report_type": "special_audit",
        "is_default": True,
        "is_preset": True,
        "visibility": "manager",
        "framework": [
            {"key": "overview", "title": "审查概述", "section_type": "text", "view_ids": [], "owner_id": None, "summary_placeholder": "审查背景/范围/目的"},
            {"key": "evidence", "title": "证据视图", "section_type": "view", "view_ids": [], "owner_id": None, "summary_placeholder": "关联数据视图"},
            {"key": "conclusion", "title": "审查结论", "section_type": "text", "view_ids": [], "owner_id": None, "summary_placeholder": "审查结论/改进建议"},
        ],
    },
]


def _merge_framework(seed_fw: list, existing_fw: list) -> list:
    """智能合并: 保留已有section的view_ids/owner_id(用户配置), 更新title/section_type结构, 新增缺失section"""
    existing_map = {s["key"]: s for s in (existing_fw or []) if isinstance(s, dict) and s.get("key")}
    result = []
    for sec in seed_fw:
        key = sec["key"]
        ex = existing_map.get(key)
        if ex:
            # 已有: 保留用户配置的view_ids/owner_id, 用种子更新title/section_type
            result.append({
                "key": key,
                "title": sec["title"],
                "section_type": sec["section_type"],
                "view_ids": ex.get("view_ids") or [],
                "owner_id": ex.get("owner_id"),
                "summary_placeholder": sec.get("summary_placeholder", ""),
            })
        else:
            # 新增: 用种子默认
            result.append(dict(sec))
    return result


def ensure_preset_templates_registered(db: Session) -> dict:
    """幂等注册预设模板: 按 code upsert
    - 不存在 → 插入
    - 存在且 is_preset=true → 智能合并更新 framework(保留用户view_ids/owner_id)
    - 存在且 is_preset=false → 跳过(用户自定义模板)
    """
    created = 0
    updated = 0
    skipped = 0
    for tpl_cfg in PRESET_TEMPLATES:
        existing = db.scalar(select(ReportTemplate).where(ReportTemplate.code == tpl_cfg["code"]))
        if not existing:
            tpl = ReportTemplate(
                code=tpl_cfg["code"],
                name=tpl_cfg["name"],
                description=tpl_cfg["description"],
                report_type=tpl_cfg["report_type"],
                framework=tpl_cfg["framework"],
                is_default=tpl_cfg["is_default"],
                is_preset=True,
                status="active",
                visibility=tpl_cfg.get("visibility", "manager"),
                sort_order=0,
                created_by="system",
            )
            db.add(tpl)
            created += 1
        elif existing.is_preset:
            # 预设模板: 智能合并更新(保留用户view_ids/owner_id), 强制同步 is_default
            # 预设模板的默认状态是系统约定, 不应被用户改动后持久化(避免一键生成取错模板)
            existing.name = tpl_cfg["name"]
            existing.description = tpl_cfg["description"]
            existing.framework = _merge_framework(tpl_cfg["framework"], existing.framework)
            # 若种子要求 is_default=true 但当前为 false, 恢复默认
            # (需先把同类型其他模板取消默认, 规避部分唯一索引)
            if tpl_cfg["is_default"] and not existing.is_default:
                db.execute(
                    update(ReportTemplate)
                    .where(
                        ReportTemplate.report_type == existing.report_type,
                        ReportTemplate.id != existing.id,
                        ReportTemplate.is_default.is_(True),
                    )
                    .values(is_default=False)
                )
                existing.is_default = True
            elif not tpl_cfg["is_default"] and existing.is_default:
                # 种子未要求默认但当前为默认: 不强制取消(用户可能有意设置)
                pass
            updated += 1
        else:
            skipped += 1
    db.commit()
    return {"presets": len(PRESET_TEMPLATES), "created": created, "updated": updated, "skipped": skipped}

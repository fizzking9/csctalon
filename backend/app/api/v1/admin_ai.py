"""AI 报告与模板管理路由 (工单 #125)

设计:
- /admin/ai-templates: 模板 CRUD + 手动触发预生成
- /admin/ai-reports: 报告列表 + 删除
- 全部 require admin 权限(ManagerUser 不足, 用 AdminUser)
- 复用 ai_report_worker.generate_for_template 做手动触发

复用 #118 LLM Provider 管理界面风格(admin CRUD + test 路由)
"""

import json
import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.api.deps import AdminUser, DBSession
from app.models.ai_report_template import (
    AiReportTemplate,
    resolve_max_tokens,
)
from app.schemas.common import R

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/ai", tags=["admin-ai"])


# ============== 模板管理 ==============

@router.get("/templates", response_model=R, summary="AI 模板列表(admin)")
def list_templates(db: DBSession, _: AdminUser):
    """列出所有 AI 报告模板(按 priority 升序)。

    #193/#204: 附带「刷新健康度」4 字段(只读聚合, 无副作用):
    last_generated_at / hit_count_7d / skip_count_7d / last_skip_verdict,
    聚合口径见 _refresh_stats()。前端列语义见 AiTemplates.vue 头部设计说明。
    """
    rows = db.query(AiReportTemplate).order_by(
        AiReportTemplate.priority, AiReportTemplate.id
    ).all()
    stats = _refresh_stats(db)
    return R(data=[_tmpl_dict(t, stats.get(t.id)) for t in rows])


def _refresh_stats(db) -> dict[int, dict]:
    """#193/#204 模板刷新健康度聚合(纯只读, 供管理端 4 列)。

    口径(与设计文档 §4.4(b) 一致):
    - last_generated_at: max(ai_reports.created_at), 仅 is_public=TRUE(成功快照)
    - hit_count_7d : 近7天命中交付次数 —— ai_reports(is_public=FALSE,
      hit_mode IN ('full','section')) 按模板计数(#193 审计修正: 以 hit_mode 为准)
    - skip_count_7d: 近7天 system_events(action=pregen_skipped) 按模板计数
      (含 no_new_events 与 throttled_20min 两类, 由 detail.reason 区分)
    - last_skip_verdict: 该模板最近一次跳过判定的证据(has_new/counts/reason/
      enforced/at), 是"采集水位证据", 不是业务结论。
    detail 为 JSON 文本, 在 Python 侧解析而非 SQL ::jsonb —— 避免个别脏
    detail 使整条聚合查询报错(逐行容错)。
    """
    stats: dict[int, dict] = {}
    for tid, mx in db.execute(text(
            "SELECT template_id, MAX(created_at) FROM ai_reports "
            "WHERE is_public = TRUE AND template_id IS NOT NULL GROUP BY template_id"
    )).all():
        stats[int(tid)] = {"last_generated_at": mx.isoformat() if mx else None}
    for tid, n in db.execute(text(
            "SELECT template_id, count(*) FROM ai_reports "
            "WHERE is_public = FALSE AND hit_mode IN ('full','section') "
            "AND template_id IS NOT NULL "
            "AND created_at > now() - interval '7 days' GROUP BY template_id"
    )).all():
        stats.setdefault(int(tid), {})["hit_count_7d"] = int(n)
    # 跳过事件量级 ≈ 每天几十条, 7 天全量取回 Python 解析(按 id 倒序, 首见即最新)
    for detail, created_at in db.execute(text(
            "SELECT detail, created_at FROM system_events "
            "WHERE category = 'ai' AND action = 'pregen_skipped' "
            "AND created_at > now() - interval '7 days' ORDER BY id DESC"
    )).all():
        try:
            d = json.loads(detail) if detail else {}
        except (TypeError, ValueError):
            continue
        tid = d.get("template_id")
        if tid is None:
            continue
        s = stats.setdefault(int(tid), {})
        s["skip_count_7d"] = s.get("skip_count_7d", 0) + 1
        if "last_skip_verdict" not in s:
            s["last_skip_verdict"] = {
                "has_new": d.get("has_new"),
                "counts": d.get("counts") or {},
                "reason": d.get("reason"),
                "enforced": d.get("enforced"),
                "at": created_at.isoformat() if created_at else None,
            }
    return stats


def _auto_refresh_on_sync(period: str | None, frequency: str | None) -> bool:
    """#130 自动识别「是否随数据同步刷新」: 日报类 True, 周报/月报 False。

    判据: 数据窗口周期 period=daily, 或调度频率 frequency in (daily, sync-triggered)
    → 时效性强, 随高频同步刷新; weekly/monthly/range → 仅按 trigger_at 每日定时。
    admin 未显式传 refresh_on_sync 时用此默认值, 之后可在模板管理页手动覆盖。

    例外(滑动周/月 2026-09-23): period=rolling_7d/rolling_30d 的模板 frequency 取
    'daily' 仅表示「每天凌晨到点生成一份」, 不是随 30 分钟同步刷新, 必须返回 False。
    """
    if (period or "").startswith("rolling_"):
        return False
    return (period == "daily") or (frequency in ("daily", "sync-triggered"))


@router.post("/templates", response_model=R, summary="新增 AI 模板(admin)")
def create_template(payload: dict, db: DBSession, _: AdminUser):
    """新增 AI 报告模板。

    payload: {code, name_cn, question_template, question_keywords?, period, frequency,
              trigger_at?, sync_offset_min?, refresh_on_sync?, priority?,
              llm_provider_code?, fallback_template_id?, max_tokens?, is_active?,
              visibility_scope?, detail_level?, required_dimensions?}

    #130: refresh_on_sync 不传时按 period/frequency 自动识别(日报类→True)。
    #152: detail_level 回答模式分级(brief/standard/deep), required_dimensions 维度清单
         (JSONB 数组, 预生成与补算按此维度全覆盖输出; 加维度只改配置)。
    """
    code = payload.get("code")
    if not code:
        raise HTTPException(400, "code 必填")
    if db.query(AiReportTemplate).filter(AiReportTemplate.code == code).first():
        raise HTTPException(400, f"code 已存在: {code}")
    # refresh_on_sync: 显式传值优先; 缺省按周期自动识别
    if "refresh_on_sync" in payload and payload["refresh_on_sync"] is not None:
        refresh_on_sync = bool(payload["refresh_on_sync"])
    else:
        refresh_on_sync = _auto_refresh_on_sync(payload.get("period"), payload.get("frequency"))
    t = AiReportTemplate(
        code=code,
        name_cn=payload["name_cn"],
        question_template=payload["question_template"],
        question_keywords=payload.get("question_keywords"),
        period=payload["period"],
        frequency=payload["frequency"],
        trigger_at=payload.get("trigger_at"),
        sync_offset_min=payload.get("sync_offset_min", 5),
        refresh_on_sync=refresh_on_sync,
        priority=payload.get("priority", 100),
        llm_provider_code=payload.get("llm_provider_code"),
        fallback_template_id=payload.get("fallback_template_id"),
        # R-P1-4: 未显式指定时按 period 给长报告分档默认(日报 2500/周·滚动周 4000/
        # 月·滚动月 5000); 显式传正整数仍优先。旧逻辑统一落 2000 会让新建的周/月
        # 详尽模板复发 R3 已修复的「长报告被 max_tokens 截断」。
        max_tokens=resolve_max_tokens(payload.get("period"),
                                      payload.get("max_tokens")),
        is_active=payload.get("is_active", True),
        visibility_scope=payload.get("visibility_scope", "public"),
        # #152 回答模式分级: detail_level 默认 standard, required_dimensions 可缺省(None)
        detail_level=payload.get("detail_level", "standard"),
        required_dimensions=payload.get("required_dimensions"),
        # #181 专项分桶: scope/format 默认 overview/brief(整体简报, 与存量行为一致)
        scope=payload.get("scope", "overview"),
        format=payload.get("format", "brief"),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    logger.info("admin ai_template created: id=%s code=%s refresh_on_sync=%s",
                t.id, t.code, t.refresh_on_sync)
    return R(data=_tmpl_dict(t))


@router.patch("/templates/{template_id}", response_model=R, summary="编辑 AI 模板(admin)")
def update_template(template_id: int, payload: dict, db: DBSession, _: AdminUser):
    """编辑 AI 报告模板(部分字段更新)。

    payload: 任意字段 subset(code/name_cn/question_template/question_keywords/period/
              frequency/trigger_at/sync_offset_min/refresh_on_sync/priority/
              llm_provider_code/fallback_template_id/max_tokens/is_active/visibility_scope/
              detail_level/required_dimensions/scope/format)

    注意(#130): 仅当显式传 refresh_on_sync 时才更新; 改 period/frequency 不重算该开关,
    以免覆盖 admin 的手动选择(自动识别只在新建缺省时生效)。
    """
    t = db.query(AiReportTemplate).get(template_id)
    if not t:
        raise HTTPException(404, "模板不存在")
    for k in ("name_cn", "question_template", "question_keywords", "period", "frequency",
              "trigger_at", "sync_offset_min", "refresh_on_sync", "priority",
              "llm_provider_code", "fallback_template_id", "max_tokens", "is_active",
              "visibility_scope", "detail_level", "required_dimensions",
              "scope", "format"):
        if k in payload:
            setattr(t, k, payload[k])
    # code 不允许修改(避免破坏 FK 与匹配)
    db.commit()
    db.refresh(t)
    logger.info("admin ai_template updated: id=%s code=%s", t.id, t.code)
    return R(data=_tmpl_dict(t))


@router.delete("/templates/{template_id}", response_model=R, summary="删除 AI 模板(admin)")
def delete_template(template_id: int, db: DBSession, _: AdminUser):
    """删除 AI 报告模板。

    注意: ai_reports.template_id ON DELETE SET NULL, 删除模板时关联报告保留(置 NULL)。
    fallback_template_id 同款(自引用 ON DELETE SET NULL)。
    """
    t = db.query(AiReportTemplate).get(template_id)
    if not t:
        raise HTTPException(404, "模板不存在")
    code = t.code
    db.delete(t)
    db.commit()
    logger.info("admin ai_template deleted: id=%s code=%s", template_id, code)
    return R(data={"id": template_id, "code": code})


@router.post("/templates/{template_id}/trigger", response_model=R, summary="手动触发预生成(admin)")
def trigger_template(template_id: int, db: DBSession, _: AdminUser):
    """手动触发指定模板的预生成(立即生成一份 is_public=True 报告)。

    同步等待 LLM 完成(可能 30-90s, 取决于模型 + max_tokens);
    失败返回 500 + 错误原因; 成功返回 report_id + total_ms。
    """
    t = db.query(AiReportTemplate).get(template_id)
    if not t:
        raise HTTPException(404, "模板不存在")
    from app.services.ai_report_worker import generate_for_template
    try:
        # #127: admin 手动触发挂 L1(优先级低于 L0 后台预生成, 高于 L2 用户实时)
        result = generate_for_template(db, t, level=1)
    except Exception as e:  # noqa: BLE001
        logger.error("admin trigger_template failed: id=%s err=%s", template_id, e)
        raise HTTPException(500, f"预生成失败: {e}") from e
    if result is None:
        raise HTTPException(500, "预生成失败(详见 system_events)")
    return R(data=result)


# ============== 报告管理 ==============

@router.get("/reports", response_model=R, summary="AI 报告列表(admin, 默认仅预生成)")
def list_admin_reports(
    db: DBSession, _: AdminUser,
    page: int = 1, page_size: int = 20,
    is_public: bool | None = None,
    template_id: int | None = None,
    include_private: bool = False,
):
    """admin 报告列表(分页, 默认仅预生成报告池 is_public=TRUE)。

    #142 (2026-09-17) 重组:
      - 默认 include_private=False → WHERE is_public=TRUE (仅预生成报告池)
      - include_private=True → WHERE 1=1 (含他人私有实时问答, admin 排查用)
      - 「AI 与分析」/system/ai-reports 视角: AI 系统预生成报告池
      - 「我的待办」/agenda AI 报告历史视角: 我自己用 AI 的轨迹(走 /api/v1/ai/reports)
      - 两个入口分工清晰, 不再对 admin 重复

    按 created_at DESC 排序, 不做 user_id 隔离(admin 全可见)。
    """
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 100:
        page_size = 20
    where = "1=1"
    binds: dict = {}
    # #142: 默认仅预生成报告池
    if not include_private:
        where += " AND is_public = TRUE"
    if is_public is not None:
        where += " AND is_public = :pub"
        binds["pub"] = is_public
    if template_id is not None:
        where += " AND template_id = :tid"
        binds["tid"] = template_id
    total = db.execute(
        text(f"SELECT COUNT(*) FROM ai_reports WHERE {where}"),  # noqa: S608
        binds,
    ).scalar() or 0
    offset = (page - 1) * page_size
    rows = db.execute(
        text(f"""SELECT id, task_id, user_id, template_id, question, period,
                  snapshot_date, snapshot_period, is_public, matched_count,
                  llm_model, llm_ms, total_ms, total_tokens, created_at
               FROM ai_reports
               WHERE {where}
               ORDER BY created_at DESC
               LIMIT :lim OFFSET :off"""),
        {**binds, "lim": page_size, "off": offset},
    ).mappings().all()
    return R(data={
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@router.get("/reports/{report_id}", response_model=R, summary="AI 报告详情(admin, 含完整 answer_md)")
def get_admin_report(report_id: int, db: DBSession, _: AdminUser):
    """admin 查看单条 AI 报告完整内容(含 answer_md 字段, 列表接口不返回 answer_md 防列表过大)。

    #141 (2026-09-17): 报告查看入口拆出独立路由 /system/ai-reports,
    列表只返摘要, 详情按需拉取 answer_md 避免一次返回 N 条 × 数 KB 报告正文。
    ⚠️ 列名是 answer_md 不是 content(见 ai_report.py ORM 模型), 历史教训:
    手写 SQL 前必须看 ORM 模型确认列名, 不能凭印象写(#116 #141 重复踩坑)。
    """
    row = db.execute(
        text("""SELECT id, task_id, user_id, template_id, question, period,
                  snapshot_date, snapshot_period, is_public, matched_count,
                  llm_model, llm_ms, total_ms, total_tokens, answer_md, created_at
               FROM ai_reports WHERE id = :rid"""),
        {"rid": report_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, f"AI 报告 #{report_id} 不存在")
    return R(data=dict(row))


@router.delete("/reports/{report_id}", response_model=R, summary="删除 AI 报告(admin)")
def delete_admin_report(report_id: int, db: DBSession, _: AdminUser):
    """删除 AI 报告(admin)。"""
    row = db.execute(
        text("SELECT id FROM ai_reports WHERE id = :rid"), {"rid": report_id}
    ).first()
    if not row:
        raise HTTPException(404, "报告不存在")
    db.execute(text("DELETE FROM ai_reports WHERE id = :rid"), {"rid": report_id})
    db.commit()
    logger.info("admin ai_report deleted: id=%s", report_id)
    return R(data={"id": report_id})


def _tmpl_dict(t: AiReportTemplate, stat: dict | None = None) -> dict:
    """模板 ORM 转 dict(供 API 响应)。stat=#193/#204 刷新健康度聚合(见 _refresh_stats)。"""
    s = stat or {}
    return {
        "id": t.id,
        "code": t.code,
        "name_cn": t.name_cn,
        "question_template": t.question_template,
        "question_keywords": t.question_keywords,
        "period": t.period,
        "frequency": t.frequency,
        "trigger_at": t.trigger_at,
        "sync_offset_min": t.sync_offset_min,
        "refresh_on_sync": t.refresh_on_sync,
        "priority": t.priority,
        "llm_provider_code": t.llm_provider_code,
        "fallback_template_id": t.fallback_template_id,
        "max_tokens": t.max_tokens,
        "is_active": t.is_active,
        "visibility_scope": t.visibility_scope,
        # #152 回答模式分级(模板配置: 预生成/补算/实时按此分级与维度)
        "detail_level": t.detail_level,
        "required_dimensions": t.required_dimensions,
        # #181 专项分桶: 专项域 + 体裁(生成按此分桶切片, 匹配按此路由)
        "scope": t.scope,
        "format": t.format,
        # #193/#204 刷新健康度 4 列(只读; "空"=暂无对应记录)
        "last_generated_at": s.get("last_generated_at"),
        "hit_count_7d": s.get("hit_count_7d", 0),
        "skip_count_7d": s.get("skip_count_7d", 0),
        "last_skip_verdict": s.get("last_skip_verdict"),
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }

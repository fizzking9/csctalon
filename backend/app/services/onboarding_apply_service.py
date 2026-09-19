"""AI 数据接入助手·确认与落地包服务（工单 #157）

设计（对应 docs/design/AI数据接入助手设计-20260919.md §4.1 / §六）：
- `confirm`：重跑校验器（规范可能已变，不能信起草时的旧报告）→ fail 必须清零；
  warn 必须逐条备注处置理由（记入 validator_report.warn_resolutions）→ status=confirmed。
- `apply`：产出**落地包 JSON**（迁移建议 + registry 种子 + 字段覆盖建议 + 缓存铁律 +
  测试清单）并置 status=applied。**不写任何业务表、不改任何代码文件** —— 真正落地仍由
  人工按 issue-workflow 走「建表迁移 → registry 种子 → CI 测试 → 凭证关闭」（设计 §1.3 非目标）。

为什么 confirm 要重跑校验而不复用起草时的 validator_report：
  起草到确认之间可能过了很久（人工核字段语义），期间 registry 可能新增了数据源
  （R1/R2/R3 唯一性随之变化）。复用旧报告 = 用过期结论放行，属规范漂移风险。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.data_source_spec import (
    SPEC_STATUS_APPLIED,
    SPEC_STATUS_CONFIRMED,
    SPEC_STATUS_DRAFT,
    DataSourceSpec,
)
from app.services.onboarding_validator import (
    build_registry_snapshot,
    is_confirmable,
    validate_spec,
)
from app.services.system_event_service import record_system_event

logger = logging.getLogger(__name__)

# 确认/落地时间戳统一用东八区（数据时间字段使用规范: 时区换算一律 Asia/Shanghai）
_TZ_CN = timezone(timedelta(hours=8))


class SpecFlowError(Exception):
    """确认/落地流程错误（校验不过、状态不允许、warn 未处置等）。"""


def spec_to_validation_input(spec: DataSourceSpec) -> dict:
    """把声明行还原成校验器入参形态（spec 主体 + field_specs + dedup_recommendation）。

    与起草服务 _merge_spec 同形 —— 校验器只认这一种入参，避免两处各造一份。
    """
    return {
        "code": spec.code,
        "table_name": spec.table_name,
        "name_cn": spec.name_cn,
        "category": spec.category,
        "sort_order": spec.sort_order,
        "time_field": spec.time_field,
        "sn_field": spec.sn_field,
        "pk_field": spec.pk_field,
        "pk_strategy": spec.pk_strategy,
        "sync_source": spec.sync_source,
        "description": spec.description,
        "field_specs": spec.field_specs or [],
        "dedup_recommendation": spec.dedup_recommendation,
    }


def rerun_validation(db: Session, spec: DataSourceSpec) -> dict:
    """重跑校验器并把最新报告写回该行，返回报告。"""
    report = validate_spec(spec_to_validation_input(spec), build_registry_snapshot(db))
    spec.validator_report = report
    db.commit()
    db.refresh(spec)
    return report


def collect_warn_resolutions(report: dict, resolutions: dict | None) -> tuple[list[dict], list[int]]:
    """把前端提交的 warn 处置理由对齐到报告里的 warn 条目。

    resolutions: {warn 下标(字符串) -> 处置理由}；返回 (结构化处置列表, 未处置的下标)。
    """
    warns = report.get("warn") or []
    provided = resolutions or {}
    resolved: list[dict] = []
    missing: list[int] = []
    for i, w in enumerate(warns):
        reason = (provided.get(str(i)) or "").strip()
        if not reason:
            missing.append(i)
            continue
        resolved.append({"rule": w.get("rule"), "detail": w.get("detail"), "resolution": reason})
    return resolved, missing


def confirm_spec(
    db: Session, spec: DataSourceSpec, *, issue_no: str | None = None,
    warn_resolutions: dict | None = None, user_id: int | None = None,
) -> DataSourceSpec:
    """确认接入声明：重跑校验 → fail 清零 + warn 全处置 → status=confirmed。

    fail 未清零或 warn 未逐条处置 → 抛 SpecFlowError（路由转 400），并记失败事件。
    """
    if spec.status not in (SPEC_STATUS_DRAFT, SPEC_STATUS_CONFIRMED):
        raise SpecFlowError(f"当前状态 '{spec.status}' 不可确认（须为 draft 或 confirmed）")

    report = rerun_validation(db, spec)
    if not is_confirmable(report):
        detail = "；".join(f"[{f.get('rule')}] {f.get('detail')}" for f in (report.get("fail") or [])[:5])
        raise SpecFlowError(f"校验未通过（fail 未清零），禁止确认：{detail}")

    resolved, missing = collect_warn_resolutions(report, warn_resolutions)
    if missing:
        raise SpecFlowError(
            f"有 {len(missing)} 条 warn 未备注处置理由（下标 {missing}）—— "
            "warn 需人工逐条说明为何可放行，禁止静默通过"
        )
    report["warn_resolutions"] = resolved

    spec.validator_report = report
    spec.status = SPEC_STATUS_CONFIRMED
    spec.issue_no = (issue_no or spec.issue_no or "").strip() or None
    spec.confirmed_by = user_id
    spec.confirmed_at = datetime.now(_TZ_CN)
    db.commit()
    db.refresh(spec)

    record_system_event(
        db, category="ai_governance", action="onboarding_confirm",
        title=f"确认数据源接入声明: {spec.name_cn}",
        summary=f"code={spec.code} issue_no={spec.issue_no or '-'} warn处置={len(resolved)}",
        status="success",
        detail=json.dumps({
            "spec_id": spec.id, "code": spec.code,
            "issue_no": spec.issue_no, "warn_resolutions": len(resolved),
        }, ensure_ascii=False),
    )
    logger.info("onboarding 确认通过 spec_id=%s code=%s issue_no=%s",
                spec.id, spec.code, spec.issue_no)
    return spec


def _pg_type(data_type: str | None) -> str:
    """data_type → PostgreSQL 列类型建议（仅供迁移建议展示，须 DBA 核）。"""
    return {
        "numeric": "numeric(18,2)",
        "amount": "bigint",  # 金额一律以「分」为整数存储（AGENTS.md 铁律）
        "timestamp": "timestamptz",
        "boolean": "boolean",
        "enum": "varchar(32)",
        "string": "text",
    }.get(data_type or "string", "text")


def build_apply_package(spec: DataSourceSpec) -> dict:
    """把已确认的声明转成落地包 JSON（纯函数，不写库、不改码）。"""
    fields = spec.field_specs or []
    migrate_cols = "\n".join(
        f"    {f.get('field_name')}  {_pg_type(f.get('data_type'))}," for f in fields
    ) or "    -- （无字段建议，需人工补全）"
    overrides = [
        {
            "field": f.get("field_name"),
            "role": f.get("semantic_role"),
            "agg": f.get("agg_func"),
            "name_cn": f.get("name_cn"),
        }
        for f in fields
    ]
    return {
        "migration": (
            "新增表迁移建议（供 DBA 核，列清单来自 field_specs）：\n"
            f"CREATE TABLE {spec.table_name or '<需人工填表名>'} (\n{migrate_cols}\n"
            "    id bigserial PRIMARY KEY,\n"
            "    created_at timestamptz NOT NULL DEFAULT now()\n);"
        ),
        "registry_seed": {
            "code": spec.code,
            "model": "<需新增 ORM 模型>",  # 模型类名人工定，AI 不产代码
            "name_cn": spec.name_cn,
            "time_field": spec.time_field,
            "sn_field": spec.sn_field,
            "pk_field": spec.pk_field,
            "pk_strategy": spec.pk_strategy,
            "sync_source": spec.sync_source,
            "category": spec.category,
            "sort_order": spec.sort_order,
            "description": spec.description,
        },
        "field_overrides": overrides,
        "cache_keys": [
            f"接入 '{spec.code}' 后，须在数据采集写入入口失效其对应缓存 pattern，"
            "Redis key 逐字符对齐 data_source_registry（见数据源接入与缓存加速规范）",
        ],
        "test_tasks": [
            "backend/tests/test_data_source_naming.py 命名断言（前缀白名单/全树唯一/禁 code 原文）",
            "backend/tests/test_data_source_tree_api.py 树接口断言（分组顺序/投影声明）",
            "backend/tests/test_onboarding_validator.py 本校验器回归",
        ],
    }


def generate_apply_package(db: Session, spec: DataSourceSpec) -> dict:
    """生成落地包并置 status=applied（只产出 JSON，不写业务表、不改代码）。"""
    if spec.status != SPEC_STATUS_CONFIRMED:
        raise SpecFlowError(f"当前状态 '{spec.status}' 不可生成落地包（须先 confirm 为 confirmed）")
    # 落地包必须建立在「当前仍通过校验」之上：确认后 registry 可能又变了
    report = rerun_validation(db, spec)
    if not is_confirmable(report):
        raise SpecFlowError("重跑校验 fail 未清零，禁止生成落地包（请先处置冲突）")

    pkg = build_apply_package(spec)
    spec.apply_package = pkg
    spec.status = SPEC_STATUS_APPLIED
    spec.applied_at = datetime.now(_TZ_CN)
    db.commit()
    db.refresh(spec)

    record_system_event(
        db, category="ai_governance", action="onboarding_apply",
        title=f"生成数据源接入落地包: {spec.name_cn}",
        summary=f"code={spec.code} 字段数={len(spec.field_specs or [])}（未写库未改码，待人工落地）",
        status="success",
        detail=json.dumps({
            "spec_id": spec.id, "code": spec.code,
            "field_count": len(spec.field_specs or []),
        }, ensure_ascii=False),
    )
    logger.info("onboarding 落地包生成 spec_id=%s code=%s", spec.id, spec.code)
    return pkg

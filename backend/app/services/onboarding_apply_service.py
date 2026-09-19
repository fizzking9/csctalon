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

from sqlalchemy import text
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


# ─────────────────────────────────────────────────────────────────────
# 代码草稿生成（#159）：把落地包变成**可粘贴的成品代码**，仍不写服务器文件
# ─────────────────────────────────────────────────────────────────────
# 设计取舍：生产容器代码是只读镜像，写进去会被下次部署覆盖 → 草稿一律以**文本**产出，
# 由开发者下载/复制到本地仓库，人工 review（revision 唯一性/列类型/口径）后提交。

def _sa_type(data_type: str | None, role: str | None) -> str:
    """data_type/角色 → SQLAlchemy 列类型表达式（迁移草稿用）。"""
    if role == "amount":
        return "sa.BigInteger()"  # 金额以「分」存整数
    return {
        "numeric": "sa.Numeric(18, 2)",
        "timestamp": "sa.DateTime(timezone=True)",
        "boolean": "sa.Boolean()",
        "enum": "sa.String(length=32)",
        "string": "sa.Text()",
    }.get(data_type or "string", "sa.Text()")


def _py_type(data_type: str | None, role: str | None) -> str:
    """data_type/角色 → Python 类型注解（ORM 草稿用）。"""
    if role == "amount":
        return "int"
    return {
        "numeric": "Decimal",
        "timestamp": "datetime",
        "boolean": "bool",
        "enum": "str",
        "string": "str",
    }.get(data_type or "string", "str")


def _pascal(code: str) -> str:
    """code → 建议的 ORM 类名（bms_order → BmsOrder）。人工可改，草稿里会提示核对。"""
    return "".join(p.capitalize() for p in (code or "NewSource").split("_") if p) or "NewSource"


def _literal(value) -> str:
    """Python 字面量渲染（None → None，字符串加引号），供 seed 草稿生成。"""
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + str(value).replace('"', '\\"') + '"'


def _build_migration_draft(spec: DataSourceSpec, down_revision: str | None,
                           revision_id: str) -> str:
    """生成完整 Alembic 迁移文件内容（可直接落盘到 backend/alembic/versions/）。"""
    fields = spec.field_specs or []
    col_lines = "\n".join(
        f'        sa.Column("{f.get("field_name")}", {_sa_type(f.get("data_type"), f.get("semantic_role"))},'
        f' nullable=True),'
        for f in fields
    ) or "        # （无字段，需人工补全）"
    table = spec.table_name or "<需人工填表名>"
    pk_hint = (
        f"\n    # 提示: 业务主键 '{spec.pk_field}' 若需唯一约束，请补 "
        f'sa.UniqueConstraint("{spec.pk_field}", name="uq_{table}_{spec.pk_field}")'
        if spec.pk_field else ""
    )
    return f'''"""add table {table} (AI 数据接入助手草稿, 工单 #159 生成)

⚠️ 提交前必须人工核对：
  1. `grep -r "{revision_id}" backend/alembic/versions/` 确认 revision id 未被占用
     （手写 id 撞车会导致 alembic 报 Cycle/Multiple heads）
  2. 列类型/可空/索引 与 field_specs 一致（本草稿默认全部 nullable=True）
  3. down_revision 是生成时刻的 head，若期间有新迁移合入需改为新 head
  4. 表名与 ORM 模型 __tablename__ 一致

Revision ID: {revision_id}
Revises: {down_revision or "<需人工填>"}
Create Date: {datetime.now(_TZ_CN).strftime("%Y-%m-%d")}
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "{revision_id}"
down_revision: Union[str, None] = {_literal(down_revision)}
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "{table}",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
{col_lines}
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),{pk_hint}
    )


def downgrade() -> None:
    op.drop_table("{table}")
'''


def _build_model_draft(spec: DataSourceSpec) -> str:
    """生成 ORM 模型类草稿（可直接落盘到 app/models/）。"""
    fields = spec.field_specs or []
    model = _pascal(spec.code or "")
    col_lines = "\n".join(
        f'    {f.get("field_name")}: Mapped[{_py_type(f.get("data_type"), f.get("semantic_role"))} | None] = '
        f'mapped_column(\n'
        f'        nullable=True, comment="{f.get("name_cn") or f.get("field_name")}",\n'
        f'    )'
        for f in fields
    ) or "    # （无字段，需人工补全）"
    imports = {"datetime"} if any(f.get("data_type") == "timestamp" for f in fields) else set()
    if any(f.get("data_type") == "numeric" and f.get("semantic_role") != "amount" for f in fields):
        imports.add("Decimal")
    import_lines = ""
    imp = []
    if "datetime" in imports:
        imp.append("from datetime import datetime")
    if "Decimal" in imports:
        imp.append("from decimal import Decimal")
    if imp:
        import_lines = "\n".join(imp) + "\n\n"
    return f'''"""<{spec.name_cn}> ORM 模型（AI 数据接入助手草稿, 工单 #159 生成）

⚠️ 提交前必须人工核对：
  1. 类名（本草稿建议 {model}）与 data_source_registry.DATA_SOURCE_SEEDS 里的 "model" 一致
  2. __tablename__ 与迁移草稿建的表名一致
  3. 列类型/索引/可空与业务实际一致（草稿默认 nullable=True）
  4. 若该表由采集写入，需在写入入口失效对应缓存 pattern（见数据源接入与缓存加速规范）
"""
{import_lines}from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class {model}(IDMixin, TimestampMixin, Base):
    """{spec.name_cn}"""

    __tablename__ = "{spec.table_name or '<需人工填表名>'}"

{col_lines}
'''


def _build_seed_draft(spec: DataSourceSpec) -> str:
    """生成 DATA_SOURCE_SEEDS 条目草稿（对齐 data_source_registry.py 现有写法）。"""
    model = _pascal(spec.code or "")
    return f'''    {{
        "code": "{spec.code}",
        "model": {model},  # ← 需与 ORM 类名一致, 并在 registry 顶部导入
        "name_cn": "{spec.name_cn}",
        "time_field": {_literal(spec.time_field)},
        "sn_field": {_literal(spec.sn_field)},
        "pk_field": {_literal(spec.pk_field)},
        "pk_strategy": {_literal(spec.pk_strategy)},
        "sync_source": {_literal(spec.sync_source)},
        "category": {_literal(spec.category)},
        "sort_order": {spec.sort_order},
        "description": {_literal(spec.description)},
    }},'''


def _build_overrides_draft(spec: DataSourceSpec) -> str:
    """生成 FIELD_OVERRIDES 条目草稿（只输出非默认项）。

    registry 约定：未列出的字段默认 role=dimension、is_filterable=True、is_pk=列主键。
    故仅输出：角色非 dimension / 声明为 pk / 明确不可筛选 的字段。
    """
    lines = [f"    # {spec.code} ({spec.name_cn})"]
    for f in spec.field_specs or []:
        role = f.get("semantic_role")
        parts = []
        if role != "dimension":
            parts.append(f'"role": "{role}"')
        if f.get("is_pk"):
            parts.append('"is_pk": True')
        if f.get("is_filterable") is False:
            parts.append('"is_filterable": False')
        if f.get("agg_func"):
            parts.append(f'"agg": "{f["agg_func"]}"')
        if f.get("name_cn") and role != "dimension":
            parts.append(f'"name_cn": "{f["name_cn"]}"')
        if not parts:
            continue  # 全默认，无需 override
        lines.append(f'    ("{spec.code}", "{f.get("field_name")}"): {{{", ".join(parts)}}},')
    if len(lines) == 1:
        lines.append("    # （无字段需要 override，全部为默认 dimension/可筛选）")
    return "\n".join(lines)


def build_apply_package(spec: DataSourceSpec, *, down_revision: str | None = None,
                        revision_id: str | None = None) -> dict:
    """把已确认的声明转成落地包（纯函数，不写库、不改码）。

    #159 起除「给人看的建议」外，另产出 4 份**可直接落盘的代码草稿文本**：
    migration_draft / model_draft / seed_draft / overrides_draft。
    down_revision 由调用方从 DB 实读当前 head 传入（保持本函数纯函数，便于测试）。
    """
    import secrets

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
    rev = revision_id or secrets.token_hex(6)
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
        # ── #159 可提交代码草稿（文本，不落服务器文件）──
        "code_drafts": {
            "revision_id": rev,
            "down_revision": down_revision,
            "migration_file": f"backend/alembic/versions/{rev}_add_{spec.table_name or 'table'}.py",
            "model_file": f"backend/app/models/{spec.code or 'new_source'}.py",
            "migration_draft": _build_migration_draft(spec, down_revision, rev),
            "model_draft": _build_model_draft(spec),
            "seed_draft": _build_seed_draft(spec),
            "overrides_draft": _build_overrides_draft(spec),
            "apply_steps": [
                f"1. 迁移: 保存 migration_draft 到 {rev}_add_{spec.table_name or 'table'}.py，"
                f'先 `grep -r "{rev}" backend/alembic/versions/` 确认 id 唯一，再 alembic upgrade head',
                f"2. 模型: 保存 model_draft 到 app/models/{spec.code or 'new_source'}.py，"
                "并在 app/models/__init__.py 导入",
                "3. 种子: 把 seed_draft 粘进 data_source_registry.DATA_SOURCE_SEEDS（含模型导入）",
                "4. 覆盖: 把 overrides_draft 粘进 data_source_registry.FIELD_OVERRIDES",
                "5. 缓存: 在数据采集写入入口失效对应缓存 pattern（Redis key 逐字符对齐）",
                "6. 测试: 跑 test_data_source_naming / test_data_source_tree_api / test_onboarding_validator",
                "7. 提交信息携带本工单号，按 issue-workflow 写关闭凭证",
            ],
        },
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


def _current_head(db: Session) -> str | None:
    """读 DB 当前 alembic head（供迁移草稿的 down_revision 用）。"""
    try:
        return db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception as e:  # noqa: BLE001  读不到（表不存在等）不阻塞落地包生成
        logger.warning("读 alembic_version 失败, 迁移草稿 down_revision 留空: %s", e)
        return None


def generate_apply_package(db: Session, spec: DataSourceSpec) -> dict:
    """生成落地包并置 status=applied（只产出 JSON/文本，不写业务表、不改代码文件）。"""
    if spec.status != SPEC_STATUS_CONFIRMED:
        raise SpecFlowError(f"当前状态 '{spec.status}' 不可生成落地包（须先 confirm 为 confirmed）")
    # 落地包必须建立在「当前仍通过校验」之上：确认后 registry 可能又变了
    report = rerun_validation(db, spec)
    if not is_confirmable(report):
        raise SpecFlowError("重跑校验 fail 未清零，禁止生成落地包（请先处置冲突）")

    pkg = build_apply_package(spec, down_revision=_current_head(db))
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

"""AI 数据治理 · 分级执行层 (工单 #165, 设计: docs/design/AI数据治理框架-20260919.md §六)

分级执行（设计 §六，红线在 L2）：

| 级别 | 动作 | 是否自动 | 依据 |
|---|---|---|---|
| L0 只读 | report_only：生成治理报告 + 留痕 | ✅ 自动 | 无副作用 |
| L1 可逆 | mark_ignored / mark_dup_residual：标行级 quality_status | ✅ 自动 | 可 restore 回滚 |
| L2 不可逆 | delete_dups / create_index / fix_time | ❌ **必须人工确认** | 删/改数据不可回滚 |

L2 执行前置（设计 §六，缺一不可）：
  1. 人工确认（`confirm_finding` 写 confirmed_by/confirmed_at）—— 未确认直接拒绝
  2. **强制备份**：把将被改动的行整行拷进 `gov_bak_<表>_<finding_id>`（CREATE TABLE AS SELECT）
  3. 执行后写凭证（影响行数 + 前后对比 + 备份表名 + 复现 SQL）→ `execute_result`
  4. 重建受影响物化宽表 + 失效缓存（数据源接入与缓存加速规范）

═══════════════════════════════════════════════════════════════════════
三条安全设计（改动前务必读懂）
═══════════════════════════════════════════════════════════════════════
① **判据必须与探测器同源**：删除 SQL 由 finding.metrics 里的
   `index_columns`（索引表达式原文）/ `index_where`（部分索引条件）拼出，并复用
   `governance_detector._any_value_cond` 生成「排除整组全 NULL」条件 —— 保证
   「删掉的行数 == finding 报出的 redundant_rows」，绝不出现「执行口径 ≠ 探测口径」。
② **数字对不上就拒绝执行**：执行前先 dry-run 数一遍待删行；与 `metrics.redundant_rows`
   不一致（数据已变 / 判据已变）→ 报错并提示重跑体检。宁可不做，不可按过期证据删数据。
③ **备份先行且行数必须相等**：备份表行数 != 待删行数 → 中止（备份不完整不得删）。

`create_index` 不在本服务自动执行：必须走 Alembic 迁移（AGENTS.md 绝对禁止第 6 条），
调用一律返回明确错误而非静默跳过。
"""

import datetime as _dt
import json
import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db_guard import assert_safe_to_destroy  # B1-2(#227): L2 破坏性操作守卫
from app.models.governance_finding import (
    FINDING_STATUS_AUTO_CLOSED,
    FINDING_STATUS_JUDGED,
    FINDING_STATUS_RESOLVED,
    GovernanceFinding,
)
from app.models.governance_rule import (
    AUTO_LEVEL_READONLY,
    DISPOSITION_CREATE_INDEX,
    DISPOSITION_DELETE_DUPS,
    DISPOSITION_FIX_TIME,
    DISPOSITION_MARK_DUP,
    DISPOSITION_MARK_IGNORED,
    DISPOSITION_MARK_PENDING,
    DISPOSITION_REPORT_ONLY,
    MANUAL_ONLY_LEVELS,
    disposition_level,
)
from app.services.governance_detector import _any_value_cond, _split_top_level
from app.services.system_event_service import record_system_event

logger = logging.getLogger(__name__)

EVENT_CATEGORY = "ai_governance"
EVENT_EXECUTE = "governance_execute"

# 备份表名前缀（备份是**真实物理表**，可人工 SELECT 复核；命名含 finding id 便于溯源）
_BACKUP_PREFIX = "gov_bak"

# 物理表 → 需要重建的物化宽表（设计 §六「重建受影响的物化表」）
# 只映射已知来源；未映射的表不重建（宁可不重建，也不无脑全量重建拖垮 DB）
_WIDE_REBUILD_MAP: dict[str, tuple[str, ...]] = {
    "refunds": ("refund_wide", "cash_flow_wide"),
    "wallet_withdrawals": ("refund_wide", "cash_flow_wide"),
    "manual_refunds": ("cash_flow_wide",),
    "merchant_refunds": ("cash_flow_wide",),
    "shanmao_refunds": ("cash_flow_wide",),   # C 路（工单 #169 补：D5 会修其 apply/audit/refund_time）
    "service_tickets": ("refund_wide",),
    "external_complaints": ("refund_wide",),
}

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ExecuteError(Exception):
    """执行失败（未研判 / L2 未确认 / 判据已变 / 备份失败 / 动作不支持自动执行）。"""


def _now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _assert_ident(name: str, what: str) -> None:
    """表名/列名白名单校验（AGENTS.md 绝对禁止第 2 条：禁止无参数化拼接 SQL）。

    表名与索引表达式无法用绑定参数（DDL/标识符不支持），故一律走**标识符白名单**：
    只允许 `[A-Za-z_][A-Za-z0-9_]*`。索引表达式来自 pg_get_indexdef（数据库自述），
    不是用户输入，但仍过白名单以防注入。
    """
    if not name or not _IDENT_RE.match(name):
        raise ExecuteError(f"非法{what}: {name!r}（只允许字母/数字/下划线）")


def _quote(ident: str) -> str:
    return f'"{ident}"'


# ─────────────────────────────────────────────────────────────────────
# 人工确认（L2 前置）
# ─────────────────────────────────────────────────────────────────────
def confirm_finding(db: Session, finding_id: int, *, operator: str,
                    note: str | None = None, params: dict | None = None,
                    commit: bool = True) -> dict:
    """人工确认 AI 建议（L2 执行的前置，设计 §六）。

    只有已研判（judged）的 finding 可确认；确认只记「谁/何时/附注」，不改 verdict
    （研判结论是 AI 的产物，人工确认是授权动作，两者不混）。

    `params` 非空 = 人工**覆盖 AI 给出的动作参数**（工单 #169，如 fix_time 的 updates 清单）：
    覆盖内容暂存进 `execute_result.pending_params`，执行时由动作体读取并记入凭证。
    **不改 `suggestion`** —— 否则「AI 原本建议了什么」就被抹掉了，无法追溯
    （设计 §六 第 4 条：AI 产物与人工决策分离）。
    """
    f = db.get(GovernanceFinding, finding_id)
    if f is None:
        raise ExecuteError(f"finding 不存在: id={finding_id}")
    if f.status in (FINDING_STATUS_RESOLVED, FINDING_STATUS_AUTO_CLOSED):
        raise ExecuteError(f"finding #{finding_id} 已终态（{f.status}），无需确认")
    if f.status != FINDING_STATUS_JUDGED:
        raise ExecuteError(f"finding #{finding_id} 尚未研判（status={f.status}），请先研判再确认")
    if params is not None and not isinstance(params, dict):
        raise ExecuteError("params 必须是对象（如 {\"updates\": [{pk, col, old, new}]}）")

    detail = {"finding_id": f.id, "operator": operator, "note": note,
              "action": (f.suggestion or {}).get("action"),
              "auto_level": disposition_level((f.suggestion or {}).get("action"))}
    if params is not None:
        # 人工覆盖：留痕里同时记「AI 原参数」与「人工覆盖后参数」，两者可对比
        detail["params_overridden"] = True
        detail["ai_params"] = (f.suggestion or {}).get("params") or {}
        detail["human_params"] = params
        f.execute_result = {**(f.execute_result or {}),
                            "pending_params": params,
                            "pending_params_by": operator,
                            "pending_params_at": _now_utc().isoformat()}

    f.confirmed_by = operator
    f.confirmed_at = _now_utc()
    record_system_event(
        db, category=EVENT_CATEGORY, action=EVENT_EXECUTE,
        title=f"治理确认: {f.table_name}/{f.target}",
        summary=f"finding #{f.id} 由 {operator} 确认{('（' + note + '）') if note else ''}",
        status="success",
        detail=json.dumps(detail, ensure_ascii=False, default=str),
        commit=False,
    )
    if commit:
        db.commit()
        db.refresh(f)
    return {"id": f.id, "confirmed_by": f.confirmed_by,
            "confirmed_at": f.confirmed_at.isoformat() if f.confirmed_at else None,
            "params_overridden": params is not None}


# ─────────────────────────────────────────────────────────────────────
# 执行入口
# ─────────────────────────────────────────────────────────────────────
def execute_finding(db: Session, finding_id: int, *, operator: str,
                    commit: bool = True) -> dict:
    """执行 finding 的建议动作（分级校验 + 副作用 + 凭证 + 留痕）。

    L0/L1 直接执行；L2 未确认 → `ExecuteError`（路由层转 400，红线）。
    返回 {"id", "status", "auto_level", "execute_result"}。
    """
    f = db.get(GovernanceFinding, finding_id)
    if f is None:
        raise ExecuteError(f"finding 不存在: id={finding_id}")
    if f.status == FINDING_STATUS_RESOLVED:
        raise ExecuteError(f"finding #{finding_id} 已执行过（{f.executed_at}）")
    if f.status == FINDING_STATUS_AUTO_CLOSED:
        raise ExecuteError(f"finding #{finding_id} 已自动收敛（问题不再命中），无需执行")
    if f.status != FINDING_STATUS_JUDGED:
        raise ExecuteError(f"finding #{finding_id} 尚未研判（status={f.status}），请先研判")

    suggestion = f.suggestion or {}
    action = suggestion.get("action") or DISPOSITION_REPORT_ONLY
    level = disposition_level(action)
    # ── 红线：L2 未人工确认不得执行 ──
    if level in MANUAL_ONLY_LEVELS and not f.confirmed_at:
        raise ExecuteError(
            f"动作 {action} 属 {level}（不可逆），必须人工确认后才能执行（设计 §六 红线）"
        )

    # 人工确认时覆盖的动作参数（工单 #169）：优先于 AI 的 suggestion.params，且不改 suggestion
    pending = (f.execute_result or {}).get("pending_params") \
        if isinstance(f.execute_result, dict) else None

    if action == DISPOSITION_REPORT_ONLY:
        result = _exec_report_only(f)
    elif action in (DISPOSITION_MARK_IGNORED, DISPOSITION_MARK_DUP, DISPOSITION_MARK_PENDING):
        result = _exec_mark_rows(db, f, action, operator=operator)
    elif action == DISPOSITION_DELETE_DUPS:
        result = _exec_delete_dups(db, f)
    elif action == DISPOSITION_CREATE_INDEX:
        raise ExecuteError(
            "建索引/约束必须走 Alembic 迁移（AGENTS.md 绝对禁止第 6 条），不提供自动执行；"
            "请按 finding 的复现 SQL 与 target 索引名编写迁移"
        )
    elif action == DISPOSITION_FIX_TIME:
        result = _exec_fix_time(db, f, params=pending)
    else:
        raise ExecuteError(f"未登记的动作: {action}")

    result.update({"action": action, "auto_level": level, "operator": operator})
    # 参数来源可追溯：AI 原参数 vs 人工确认时覆盖（凭证里两者都在）
    result["params_source"] = "human_confirm" if pending is not None else "ai_suggestion"
    if pending is not None:
        result["confirmed_params"] = pending
    # ── 后置：重建受影响宽表 + 失效缓存（L0 只读不改数据，无需重建）──
    if level != AUTO_LEVEL_READONLY:
        result["post_execute"] = _post_execute(db, f.table_name)
    f.execute_result = result
    f.executed_at = _now_utc()
    f.status = FINDING_STATUS_RESOLVED

    record_system_event(
        db, category=EVENT_CATEGORY, action=EVENT_EXECUTE,
        title=f"治理执行: {f.table_name}/{f.target} → {action}",
        summary=(f"finding #{f.id} 由 {operator} 执行 {action}（{level}），"
                 f"影响 {result.get('affected_rows', 0)} 行"),
        status="success",
        detail=json.dumps({"finding_id": f.id, **result}, ensure_ascii=False, default=str),
        commit=False,
    )
    if commit:
        db.commit()
        db.refresh(f)
    logger.info("governance 执行完成 finding=%s action=%s level=%s affected=%s by=%s",
                f.id, action, level, result.get("affected_rows"), operator)
    return {"id": f.id, "status": f.status, "auto_level": level,
            "execute_result": result}


# ─────────────────────────────────────────────────────────────────────
# L0：只读报告
# ─────────────────────────────────────────────────────────────────────
def _exec_report_only(f: GovernanceFinding) -> dict:
    """L0 只读：生成治理报告（不改任何数据），供人工按报告处置。

    报告内容 = 结论 + 证据 + 复现 SQL —— 使用者可直接把 repro_sql 贴到库上核对数字。
    """
    metrics = f.metrics or {}
    return {
        "affected_rows": int(metrics.get("redundant_rows") or metrics.get("orphan_count") or 0),
        "report": {
            "verdict": f.verdict,
            "reason": f.verdict_reason,
            "metrics": {k: v for k, v in metrics.items() if k != "curve"},
            "repro_sql": f.repro_sql,
        },
        "note": "L0 只读动作：未改动任何数据",
    }


# ─────────────────────────────────────────────────────────────────────
# L1：行级标记（复用 quality_disposition_service，可 restore 回滚）
# ─────────────────────────────────────────────────────────────────────
def _sample_ids(f: GovernanceFinding) -> list:
    """从 finding 样本里取可定位的行 id（D1 给 `_sample_ids`，D3 给 `_id`）。

    ⚠️ 只处置**样本行**（≤10 组 × 5 行）—— 这是 L1「可逆标记」的定位：
    先标样本让经营者看到影响面，全量处置需按 repro_sql 批量执行（报告里已给出）。
    """
    out: list = []
    for s in (f.samples or []):
        if not isinstance(s, dict):
            continue
        if s.get("_id") is not None:
            out.append(s["_id"])
        for i in (s.get("_sample_ids") or []):
            out.append(i)
    # 去重保序
    seen: set = set()
    uniq = [x for x in out if not (x in seen or seen.add(x))]
    return uniq


def _qc_code_for(table_name: str) -> str | None:
    """物理表名 → 质量处置表单 code（QUALITY_TABLES 里 model.__tablename__ 反查）。"""
    from app.services import quality_check as QC

    for code, cfg in QC.QUALITY_TABLES.items():
        if getattr(cfg["model"], "__tablename__", None) == table_name:
            return code
    return None


def _exec_mark_rows(db: Session, f: GovernanceFinding, action: str, *, operator: str) -> dict:
    """L1 可逆：把样本行标为 ignored / dup_residual / pending（走 quality_disposition_service）。

    三处不满足即明确报错（不静默成功 —— 「点了没反应」是禁止的）：
    ① 表未纳入行级质量处置配置（QUALITY_TABLES）② 样本无行 id ③ 该表无 quality_status 列。

    三个动作的**口径差异**（不可混，AGENTS.md 绝对禁止第 10 条）：
      · `mark_ignored`      → `ignored`，**排除出经营统计**（口径变更）
      · `mark_dup_residual` → `dup_residual`，**排除出经营统计**（重复治理专用）
      · `mark_pending`      → `pending`，**仍计入统计**，只是标「待人工核对」
                              （工单 #169；采集语义增强的默认 L1 动作，不改口径）
    """
    from app.services import quality_disposition_service as QD

    code = _qc_code_for(f.table_name)
    if code is None:
        raise ExecuteError(
            f"表 {f.table_name} 未纳入行级质量处置配置，L1 标记不可用；"
            f"请改用 report_only（只出报告）或人工处置"
        )
    ids = _sample_ids(f)
    if not ids:
        raise ExecuteError(
            f"finding #{f.id} 的样本不含行 id（_sample_ids/_id），无法定位要标记的行；"
            f"请改用 report_only 并依 repro_sql 人工处置"
        )
    marked, skipped, failed = [], [], []
    for pk in ids:
        try:
            if action == DISPOSITION_MARK_IGNORED:
                QD.quality_ignore(db, code, pk, operator=operator, reason=f"治理 finding #{f.id}")
            else:
                # dup_residual / pending 直接写 quality_status（不走 QD 的排除状态机）
                from app.services import quality_check as QC
                cfg = QC.get_table_config(code)
                r = QC._get_by_pk(db, cfg, pk)
                if action == DISPOSITION_MARK_PENDING:
                    # ── 红线：mark_pending **不得**把「已排除出经营统计」的行拉回来 ──
                    # pending 计入统计、dup_residual/ignored 排除出统计 → 覆盖即改口径
                    # （AGENTS.md 绝对禁止第 10 条）。2026-09-20 生产实测踩到：4 行原
                    # dup_residual 被标成 pending → D 路会把重复占位行重新计入（已复位）。
                    if (r.quality_status or "") in QD.EXCLUDED_STATES:
                        skipped.append({"id": pk, "quality_status": r.quality_status,
                                        "reason": "已是排除态（dup_residual/ignored），"
                                                  "mark_pending 不改经营口径"})
                        continue
                    r.quality_status = "pending"
                    r.quality_note = _append_note(
                        r.quality_note, f"治理 finding #{f.id} 标待核 by {operator}")
                else:
                    r.quality_status = "dup_residual"
                    r.quality_note = _append_note(
                        r.quality_note, f"治理 finding #{f.id} 标重复残留 by {operator}")
                db.commit()
            marked.append(pk)
        except Exception as e:  # noqa: BLE001  单行失败不影响其余（逐行回报）
            db.rollback()
            failed.append({"id": pk, "error": str(e)[:120]})
    note = (f"L1 可逆：仅标记**样本行**（共 {len(ids)} 行）；"
            f"全量处置请按 repro_sql 批量执行；可用质量页「恢复」回滚")
    if action == DISPOSITION_MARK_PENDING:
        note += "（pending 仍计入经营统计，只是标「待人工核对」——不改口径）"
        if skipped:
            note += f"；另有 {len(skipped)} 行已是排除态、按红线未改动"
    return {
        "affected_rows": len(marked),
        "marked_ids": marked,
        "skipped": skipped,
        "failed": failed,
        "qc_code": code,
        "marked_status": {"mark_ignored": "ignored", "mark_dup_residual": "dup_residual",
                          "mark_pending": "pending"}.get(action, action),
        "note": note,
        "rollback": "POST /api/v1/data-quality/disposition/{table}/{pk}/restore",
    }


def _append_note(old: str | None, add: str) -> str:
    """质量备注追加（沿用既有 ` | ` 分隔与 500 字上限，见 quality_disposition_service）。"""
    return (((old + " | ") if old else "") + add)[:500]


# ─────────────────────────────────────────────────────────────────────
# L2：删重复行（备份 → 执行 → 校验；判据与 D1 同源）
# ─────────────────────────────────────────────────────────────────────
def _dedup_recipe(f: GovernanceFinding) -> tuple[list[str], str]:
    """从 finding.metrics 取去重判据（索引表达式原文 + 部分索引条件），与 D1 同源。

    取不到即拒绝执行 —— 绝不猜判据（猜错就是删错数据）。
    """
    metrics = f.metrics or {}
    cols = metrics.get("index_columns")
    if isinstance(cols, str):
        cols = _split_top_level(cols)
    if not cols or not isinstance(cols, list):
        raise ExecuteError(
            f"finding #{f.id} 缺去重判据（metrics.index_columns），无法安全生成删除 SQL；请重跑体检"
        )
    where = metrics.get("index_where") or ""
    return [str(c) for c in cols], str(where)


def _ctid_delete_sql(table: str, cols: list[str], where: str) -> tuple[str, str, str]:
    """生成「按索引表达式去重、每组保留 1 行」的 SQL；返回 (dry_run_sql, delete_sql, pick_sql)。

    与 `governance_detector._gap_metrics` **完全同源**：
      · 分组列 = 索引表达式原文（含 date()/COALESCE() 等）
      · 部分索引带 WHERE 条件
      · `bool_or(任一列非 NULL)` 排除「整组全 NULL」（铁律 ②）
    保留哪一行：`ORDER BY ctid`（等价于保留最早写入的那一行；表无 PK 时也适用）。
    行身份统一用 `ctid`（PG 内部行标识），备份与删除同源 → 不会错行。
    """
    _assert_ident(table, "表名")
    for c in cols:  # 表达式列（date(x)/COALESCE(x,0)）不匹配纯标识符 → 只校验不含危险字符
        if any(ch in c for ch in (";", "--", "/*", "'")):
            raise ExecuteError(f"索引表达式含危险字符，拒绝执行: {c!r}")
    group_by = ", ".join(cols)
    any_val = _any_value_cond(group_by)
    where_sql = f" WHERE {where}" if where else ""
    tbl = _quote(table)
    pick = (f'SELECT ctid FROM (\n'
            f'    SELECT ctid,\n'
            f'           row_number() OVER (PARTITION BY {group_by} ORDER BY ctid) AS rn,\n'
            f'           bool_or({any_val}) OVER (PARTITION BY {group_by}) AS has_value\n'
            f'    FROM {tbl}{where_sql}\n'
            f') x WHERE x.rn > 1 AND x.has_value')
    return (f'SELECT count(*) FROM (\n{pick}\n) y',
            f'DELETE FROM {tbl} WHERE ctid IN (\n{pick}\n)',
            pick)


def _backup_rows(db: Session, table: str, finding_id: int, pick_sql: str,
                 *, params: dict | None = None) -> tuple[str, int]:
    """把将被改动/删除的行整行备份到 `gov_bak_<表>_<finding_id>`（CREATE TABLE AS SELECT）。

    备份行数必须等于待改动行数，否则由调用方中止（备份不完整不得改数据）。
    备份表可人工 `SELECT * FROM gov_bak_...` 复核，也是回滚依据（INSERT ... SELECT 回灌）。

    `params` 供 `pick_sql` 使用绑定参数（`fix_time` 按 id 定位，不能像删重复那样纯 ctid 子查询）。
    """
    _assert_ident(table, "表名")
    bak = f"{_BACKUP_PREFIX}_{table}_{finding_id}"
    _assert_ident(bak, "备份表名")
    # B1-2(#227): DROP 属破坏性操作, 必须先过 db_guard 守卫
    assert_safe_to_destroy(db, reason=f"governance _backup_rows DROP gov_bak_{table}_{finding_id}")
    db.execute(text(f'DROP TABLE IF EXISTS {_quote(bak)}'))
    db.execute(text(f'CREATE TABLE {_quote(bak)} AS SELECT * FROM {_quote(table)} '
                    f'WHERE ctid IN (\n{pick_sql}\n)'), params or {})
    rows = db.execute(text(f'SELECT count(*) FROM {_quote(bak)}')).scalar() or 0
    # ⚠️ 不 rollback：PG 的 DDL 是事务性的，rollback 会把刚建好的备份表一起撤掉。
    #    备份与改动在同一事务里 → 要么都提交、要么都不提交（原子），无需额外处理。
    return bak, int(rows)


def _exec_delete_dups(db: Session, f: GovernanceFinding) -> dict:
    """L2 删重复行：判据同源 → dry-run 对数 → 备份 → 删除 → 前后对比。"""
    # B1-2(#227): DELETE 属破坏性操作, 必须先过 db_guard 守卫
    assert_safe_to_destroy(db, reason=f"governance _exec_delete_dups DELETE FROM {f.table_name}")
    if f.detector != "unique_index_dup":
        raise ExecuteError(
            f"delete_dups 仅适用于 unique_index_dup 探测器（当前 {f.detector}）；"
            f"行数突变类 finding 请用 report_only"
        )
    cols, where = _dedup_recipe(f)
    dry_sql, del_sql, pick_sql = _ctid_delete_sql(f.table_name, cols, where)
    expected = int((f.metrics or {}).get("redundant_rows") or 0)

    to_delete = int(db.execute(text(dry_sql)).scalar() or 0)
    if to_delete == 0:
        raise ExecuteError("待删行数为 0：重复可能已被治理（请重跑体检确认后关闭本 finding）")
    # ── 安全设计②：数字对不上就拒绝执行（判据/数据已变，不得按过期证据删数据）──
    if expected and to_delete != expected:
        raise ExecuteError(
            f"待删行数({to_delete}) 与 finding 报出的冗余行数({expected}) 不一致 —— "
            f"数据或判据已变化，请重跑体检后再执行"
        )

    before_total = int(db.execute(text(f'SELECT count(*) FROM {_quote(f.table_name)}')).scalar() or 0)
    bak, bak_rows = _backup_rows(db, f.table_name, f.id, pick_sql)
    # ── 安全设计③：备份行数必须等于待删行数 ──
    if bak_rows != to_delete:
        raise ExecuteError(f"备份行数({bak_rows}) ≠ 待删行数({to_delete})，已中止（备份不完整不得删）")

    affected = db.execute(text(del_sql)).rowcount
    db.commit()
    after_total = int(db.execute(text(f'SELECT count(*) FROM {_quote(f.table_name)}')).scalar() or 0)
    if affected != to_delete or after_total != before_total - to_delete:
        logger.error("governance delete_dups 数字异常: affected=%s to_delete=%s before=%s after=%s",
                     affected, to_delete, before_total, after_total)
        raise ExecuteError(
            f"删除后核对失败（affected={affected}, 期望 {to_delete}; 表行数 {before_total}→{after_total}）"
            f"，备份表 {bak} 保留可回滚"
        )
    return {
        "affected_rows": int(affected),
        "before_rows": before_total,
        "after_rows": after_total,
        "backup_table": bak,
        "backup_rows": bak_rows,
        "sql": del_sql,
        "rollback": f'INSERT INTO "{f.table_name}" SELECT * FROM "{bak}"',
        "dedup_cols": cols,
        "dedup_where": where or None,
    }


# ─────────────────────────────────────────────────────────────────────
# L2：修时间值（备份 → 逐行 UPDATE → old/new 对照；工单 #169）
# ─────────────────────────────────────────────────────────────────────
def _fix_updates(f: GovernanceFinding, params: dict | None) -> list[dict]:
    """取待修正清单：人工确认时的覆盖优先，其次 AI 的 `suggestion.params.updates`。

    清单为空即拒绝执行 —— 绝不猜「该改成什么」（猜错就是改错数据）。
    """
    src = params if params is not None else ((f.suggestion or {}).get("params") or {})
    updates = (src or {}).get("updates")
    if not isinstance(updates, list) or not updates:
        raise ExecuteError(
            f"finding #{f.id} 无待修正清单（params.updates = [{{pk, col, old, new}}]），"
            f"无法安全生成 UPDATE；请在「确认」时给出 updates 或重跑研判"
        )
    out: list[dict] = []
    for i, u in enumerate(updates):
        if not isinstance(u, dict):
            raise ExecuteError(f"updates[{i}] 不是对象: {u!r}")
        if u.get("pk") is None or not u.get("col") or u.get("new") is None:
            raise ExecuteError(f"updates[{i}] 缺 pk/col/new（三者必填）: {u!r}")
        out.append({"pk": u["pk"], "col": str(u["col"]), "old": u.get("old"), "new": u["new"]})
    return out


def _column_type(db: Session, table: str, col: str) -> str:
    """列类型（information_schema 权威）—— 决定 `new` 值怎么解析，弄错会二次偏移时区。"""
    dtype = db.execute(text(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t AND column_name=:c"
    ), {"t": table, "c": col}).scalar()
    if not dtype:
        raise ExecuteError(f"列不存在: {table}.{col}")
    if dtype not in ("timestamp with time zone", "timestamp without time zone", "date"):
        raise ExecuteError(
            f"{table}.{col} 不是日期/时间列（{dtype}），fix_time 只修时间值"
        )
    return dtype


def _coerce_value(raw, dtype: str):
    """把人工/AI 给的 `new` 值转成与**列类型**匹配的 Python 值。

    时区铁律（AGENTS.md / docs/standards/数据时间字段使用规范.md）：
      · `timestamptz`：naive 串视为**北京时间** → UTC（走 `upsert._to_datetime`，与采集同源）
      · `timestamp`（naive 列）：本身就是本地时间 → 原样解析，**不做时区换算**
        （若在这里转 UTC，存进去会整体偏 8 小时）
      · `date`：只取日期部分，**不做时区换算**（否则北京时间 00:00 会被减一天）
    """
    s = str(raw).strip().replace("/", "-")
    if not s:
        raise ExecuteError("new 值不得为空")
    if dtype == "date":
        try:
            return _dt.date.fromisoformat(s[:10])
        except ValueError:
            raise ExecuteError(f"new 值不是合法日期: {raw!r}（期望 YYYY-MM-DD）") from None
    if dtype == "timestamp without time zone":
        try:
            return _dt.datetime.fromisoformat(s)
        except ValueError:
            raise ExecuteError(f"new 值不是合法时间: {raw!r}（期望 YYYY-MM-DD HH:MM:SS）") from None
    from app.services.upsert import _to_datetime

    v = _to_datetime(raw)
    if v is None:
        raise ExecuteError(f"new 值无法解析为时间: {raw!r}")
    return v


def _norm_ts(v) -> str:
    """时间值归一为可比较的字符串：`/`→`-`、空格分隔→`T`（`str(datetime)` 与 `isoformat()`
    只差这个分隔符，不归一的话「同值不同写法」会被判成不一致，正常执行全被误拒）。"""
    s = v.isoformat() if isinstance(v, (_dt.datetime, _dt.date)) else str(v).strip()
    return s.replace("/", "-").replace(" ", "T")


def _value_matches(stored, expected) -> bool:
    """库里现值与 `old` 是否一致（拒绝按过期证据改数据）。

    容忍「日期串 vs 带时刻串」的表示差异（`2099-12-31` vs `2099-12-31T02:00:00+00:00`）：
    只要一方是纯日期且日期部分相同即视为一致 —— 否则 AI 给的 old 与库里的值
    永远对不上，会把正常执行全部误拒。
    """
    if expected is None:
        return True  # AI 未给 old → 不比（凭证里仍记库中实际值）
    a, b = _norm_ts(stored), _norm_ts(expected)
    if a == b:
        return True
    return bool(len(a) >= 10 and len(b) >= 10 and a[:10] == b[:10]
                and (len(a) == 10 or len(b) == 10))


def _exec_fix_time(db: Session, f: GovernanceFinding, *, params: dict | None) -> dict:
    """L2 修时间值（工单 #169，设计 §3.2）：备份 → 逐行 UPDATE → 凭证（old/new 对照）。

    安全设计（沿用 #165 三条，红线不变）：
      ① **判据来自 finding 的参数**，不由执行层猜（无 updates → 拒绝执行）；
      ② **过期证据拒绝执行**：库中现值与 `old` 不一致 → 中止（数据已变，请重跑体检）；
      ③ **备份先行且行数必须相等**：备份行数 != 待改行数 → 中止（备份不完整不得改）。
    表名/列名走 `_assert_ident` 白名单，值一律绑定参数（AGENTS.md 绝对禁止第 2 条）。
    """
    # B1-2(#227): 批量 UPDATE 属破坏性操作, 必须先过 db_guard 守卫
    assert_safe_to_destroy(db, reason=f"governance _exec_fix_time UPDATE {f.table_name}")
    if f.detector != "impossible_date":
        raise ExecuteError(
            f"fix_time 仅适用于 impossible_date 探测器（当前 {f.detector}）；"
            f"其它探测器的日期问题请先确认判据再人工处置"
        )
    updates = _fix_updates(f, params)
    table = f.table_name
    _assert_ident(table, "表名")
    has_id = bool(db.execute(text(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t AND column_name='id'"
    ), {"t": table}).scalar())
    if not has_id:
        raise ExecuteError(f"表 {table} 无 id 列，无法按 pk 定位行，fix_time 不可用")

    # 同一行同一列只允许一条修正（重复项会让备份行数 ≠ 待改行数，也会造成「改两次」）
    seen: set[tuple] = set()
    for u in updates:
        key = (str(u["pk"]), u["col"])
        if key in seen:
            raise ExecuteError(f"updates 中 (pk={u['pk']}, col={u['col']}) 重复，请合并后再执行")
        seen.add(key)
        _assert_ident(u["col"], "列名")
        _column_type(db, table, u["col"])   # 列必须存在且是时间列

    pks = list(dict.fromkeys(u["pk"] for u in updates))
    cols = sorted({u["col"] for u in updates})
    params_map = {f"pk{i}": pk for i, pk in enumerate(pks)}
    placeholders = ", ".join(":" + k for k in params_map)
    col_list = ", ".join(_quote(c) for c in cols)
    pick_sql = f'SELECT ctid FROM "{table}" WHERE id IN ({placeholders})'

    # ── 安全设计②：先核对库中现值与 old（拒绝按过期证据改数据）──
    cur_rows = db.execute(text(
        f'SELECT id, {col_list} FROM "{table}" WHERE id IN ({placeholders})'
    ), params_map).mappings().all()
    found = {r["id"]: r for r in cur_rows}
    missing = [pk for pk in pks if pk not in found]
    if missing:
        raise ExecuteError(f"以下行不存在（可能已删除），请重跑体检: {missing[:10]}")
    stale = []
    for u in updates:
        stored = found[u["pk"]][u["col"]]
        if not _value_matches(stored, u["old"]):
            stale.append({"pk": u["pk"], "col": u["col"], "expected": u["old"],
                          "stored": str(stored)})
    if stale:
        raise ExecuteError(
            f"库中现值与建议的 old 不一致（{len(stale)} 条，如 {stale[0]}）—— "
            f"数据已变化，请重跑体检/研判后再执行"
        )

    # ── 安全设计③：备份先行，行数必须相等 ──
    bak, bak_rows = _backup_rows(db, table, f.id, pick_sql, params=params_map)
    if bak_rows != len(pks):
        raise ExecuteError(
            f"备份行数({bak_rows}) ≠ 待改行数({len(pks)})，已中止（备份不完整不得改数据）"
        )

    # ── 逐行 UPDATE（列名白名单 + 值绑定参数）──
    before: dict[tuple, str] = {}
    affected = 0
    for u in updates:
        dtype = _column_type(db, table, u["col"])
        before[(u["pk"], u["col"])] = str(found[u["pk"]][u["col"]])
        affected += db.execute(text(
            f'UPDATE "{table}" SET "{u["col"]}" = :new WHERE id = :pk'
        ), {"new": _coerce_value(u["new"], dtype), "pk": u["pk"]}).rowcount
    db.commit()

    # ── 凭证：old/new 对照以**库中实际值**为准（人工可 SELECT 复核）──
    after_rows = db.execute(text(
        f'SELECT id, {col_list} FROM "{table}" WHERE id IN ({placeholders})'
    ), params_map).mappings().all()
    after = {r["id"]: r for r in after_rows}
    changes = [{
        "pk": u["pk"], "col": u["col"],
        "old": before[(u["pk"], u["col"])],
        "new": str(after[u["pk"]][u["col"]]),
        "old_expected": u["old"],
    } for u in updates]
    if affected != len(updates):
        raise ExecuteError(
            f"UPDATE 影响行数({affected}) ≠ 待改条数({len(updates)})，"
            f"备份表 {bak} 保留可回滚，请人工核对"
        )
    return {
        "affected_rows": int(affected),
        "backup_table": bak,
        "backup_rows": bak_rows,
        "changes": changes,
        "sql": (f'UPDATE "{table}" SET "<col>" = <new> WHERE id = <pk> '
                f'-- 共 {len(updates)} 条，明细见 changes'),
        "rollback": f'-- 按备份表回灌（人工执行）：\n'
                    f'UPDATE "{table}" t SET "{cols[0]}" = b."{cols[0]}" '
                    f'FROM "{bak}" b WHERE t.id = b.id',
    }


# ─────────────────────────────────────────────────────────────────────
# 后置：重建受影响宽表 + 失效缓存（数据源接入与缓存加速规范）
# ─────────────────────────────────────────────────────────────────────
def _post_execute(db: Session, table_name: str) -> dict:
    """重建受影响的物化宽表 + 失效缓存；任何一步失败都不影响「数据已改」的事实，
    故只记录结果（不抛异常），由凭证与留痕体现真实状态。"""
    out: dict[str, Any] = {"wide_rebuilt": [], "wide_skipped": None, "cache": None}
    wide = _WIDE_REBUILD_MAP.get(table_name)
    if wide:
        from app.services.cash_flow_wide_builder import build_cash_flow_wide
        from app.services.wide_table_builder import build_refund_wide

        for name in wide:
            try:
                if name == "refund_wide":
                    stats = build_refund_wide(db)
                else:
                    stats = build_cash_flow_wide(db)
                out["wide_rebuilt"].append({"table": name,
                                            "stats": {k: v for k, v in (stats or {}).items()
                                                      if isinstance(v, (int, float, str))}})
            except Exception as e:  # noqa: BLE001  重建失败只记录
                logger.error("governance 宽表重建失败 %s: %s", name, e)
                out["wide_rebuilt"].append({"table": name, "error": str(e)[:200]})
    else:
        out["wide_skipped"] = f"{table_name} 未映射到物化宽表，跳过重建（如确需重建请手动执行）"

    try:
        from app.services.cache_warmer import rebuild_cache

        # invalidate_only：只失效不预热 —— 执行路径上不做全量预热（耗时且易与同步撞车），
        # 下次访问自然回填（cache_warmer.cached_call 语义）。
        out["cache"] = rebuild_cache("all", invalidate_only=True)
    except Exception as e:  # noqa: BLE001  缓存失效失败只记录（不阻塞执行结果落库）
        logger.error("governance 缓存失效失败: %s", e)
        out["cache"] = {"ok": False, "error": str(e)[:200]}
    return out

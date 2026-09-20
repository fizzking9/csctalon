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

`create_index` / `fix_time` 不在本服务自动执行：前者必须走 Alembic 迁移（AGENTS.md 绝对禁止
第 6 条），后者属第二期采集语义增强（设计 §七）；调用一律返回明确错误而非静默跳过。
"""

import datetime as _dt
import json
import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

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
                    note: str | None = None, commit: bool = True) -> dict:
    """人工确认 AI 建议（L2 执行的前置，设计 §六）。

    只有已研判（judged）的 finding 可确认；确认只记「谁/何时/附注」，不改 verdict
    （研判结论是 AI 的产物，人工确认是授权动作，两者不混）。
    """
    f = db.get(GovernanceFinding, finding_id)
    if f is None:
        raise ExecuteError(f"finding 不存在: id={finding_id}")
    if f.status in (FINDING_STATUS_RESOLVED, FINDING_STATUS_AUTO_CLOSED):
        raise ExecuteError(f"finding #{finding_id} 已终态（{f.status}），无需确认")
    if f.status != FINDING_STATUS_JUDGED:
        raise ExecuteError(f"finding #{finding_id} 尚未研判（status={f.status}），请先研判再确认")

    f.confirmed_by = operator
    f.confirmed_at = _now_utc()
    record_system_event(
        db, category=EVENT_CATEGORY, action=EVENT_EXECUTE,
        title=f"治理确认: {f.table_name}/{f.target}",
        summary=f"finding #{f.id} 由 {operator} 确认{('（' + note + '）') if note else ''}",
        status="success",
        detail=json.dumps({"finding_id": f.id, "operator": operator, "note": note,
                           "action": (f.suggestion or {}).get("action"),
                           "auto_level": disposition_level((f.suggestion or {}).get("action"))},
                          ensure_ascii=False),
        commit=False,
    )
    if commit:
        db.commit()
        db.refresh(f)
    return {"id": f.id, "confirmed_by": f.confirmed_by,
            "confirmed_at": f.confirmed_at.isoformat() if f.confirmed_at else None}


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

    if action == DISPOSITION_REPORT_ONLY:
        result = _exec_report_only(f)
    elif action in (DISPOSITION_MARK_IGNORED, DISPOSITION_MARK_DUP):
        result = _exec_mark_rows(db, f, action, operator=operator)
    elif action == DISPOSITION_DELETE_DUPS:
        result = _exec_delete_dups(db, f)
    elif action == DISPOSITION_CREATE_INDEX:
        raise ExecuteError(
            "建索引/约束必须走 Alembic 迁移（AGENTS.md 绝对禁止第 6 条），不提供自动执行；"
            "请按 finding 的复现 SQL 与 target 索引名编写迁移"
        )
    elif action == DISPOSITION_FIX_TIME:
        raise ExecuteError("fix_time 属第二期采集语义增强（设计 §七），本期未实现自动执行")
    else:
        raise ExecuteError(f"未登记的动作: {action}")

    result.update({"action": action, "auto_level": level, "operator": operator})
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
    """L1 可逆：把样本行标为 ignored / dup_residual（走 quality_disposition_service）。

    三处不满足即明确报错（不静默成功 —— 「点了没反应」是禁止的）：
    ① 表未纳入行级质量处置配置（QUALITY_TABLES）② 样本无行 id ③ 该表无 quality_status 列。
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
    marked, failed = [], []
    for pk in ids:
        try:
            if action == DISPOSITION_MARK_IGNORED:
                QD.quality_ignore(db, code, pk, operator=operator, reason=f"治理 finding #{f.id}")
            else:
                # dup_residual 是「重复治理专用」标记，直接写 quality_status
                from app.services import quality_check as QC
                cfg = QC.get_table_config(code)
                r = QC._get_by_pk(db, cfg, pk)
                r.quality_status = "dup_residual"
                r.quality_note = ((r.quality_note + " | ") if r.quality_note else "") + \
                    f"治理 finding #{f.id} 标重复残留 by {operator}"
                db.commit()
            marked.append(pk)
        except Exception as e:  # noqa: BLE001  单行失败不影响其余（逐行回报）
            db.rollback()
            failed.append({"id": pk, "error": str(e)[:120]})
    return {
        "affected_rows": len(marked),
        "marked_ids": marked,
        "failed": failed,
        "qc_code": code,
        "note": (f"L1 可逆：仅标记**样本行**（共 {len(ids)} 行）；"
                 f"全量处置请按 repro_sql 批量执行；可用质量页「恢复」回滚"),
        "rollback": "POST /api/v1/data-quality/disposition/{table}/{pk}/restore",
    }


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


def _backup_rows(db: Session, table: str, finding_id: int, pick_sql: str) -> tuple[str, int]:
    """把将被删除的行整行备份到 `gov_bak_<表>_<finding_id>`（CREATE TABLE AS SELECT）。

    备份行数必须等于待删行数，否则由调用方中止（备份不完整不得删）。
    备份表可人工 `SELECT * FROM gov_bak_...` 复核，也是回滚依据（INSERT ... SELECT 回灌）。
    """
    _assert_ident(table, "表名")
    bak = f"{_BACKUP_PREFIX}_{table}_{finding_id}"
    _assert_ident(bak, "备份表名")
    db.execute(text(f'DROP TABLE IF EXISTS {_quote(bak)}'))
    db.execute(text(f'CREATE TABLE {_quote(bak)} AS SELECT * FROM {_quote(table)} '
                    f'WHERE ctid IN (\n{pick_sql}\n)'))
    rows = db.execute(text(f'SELECT count(*) FROM {_quote(bak)}')).scalar() or 0
    # ⚠️ 不 rollback：PG 的 DDL 是事务性的，rollback 会把刚建好的备份表一起撤掉。
    #    备份与删除在同一事务里 → 要么都提交、要么都不提交（原子），无需额外处理。
    return bak, int(rows)


def _exec_delete_dups(db: Session, f: GovernanceFinding) -> dict:
    """L2 删重复行：判据同源 → dry-run 对数 → 备份 → 删除 → 前后对比。"""
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

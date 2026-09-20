"""AI 数据治理框架 · 探测层 D1: 唯一索引重复 (工单 #162)

设计: docs/design/AI数据治理框架-20260919.md §四（探测层）

探测层是**确定性**的（不含 AI）：只负责「找出结构性可疑 + 备齐可审计证据」，
判断「是不是真问题」交给研判层（工单 #164）。

═══════════════════════════════════════════════════════════════════════
D1 的两条铁律（两轮真实误报换来的，改动前务必读懂）
═══════════════════════════════════════════════════════════════════════

**铁律 ①：逐字符复现索引真实语义**
    列清单取 `pg_get_indexdef` 括号内**原文**（含 `date(x)` / `COALESCE(...)` 等表达式），
    部分索引必须带上 `WHERE indpred`。
    ✘ 用 `indkey` 取裸列名分组会误报：`manual_refunds` 曾凭空报出「3062 行重复」
      （因 `date(register_time)` 的截断未被复现，按原始时间戳分组多出分组）。

**铁律 ②：重复的判据 = NULL 视为相等（`GROUP BY` 语义），但要排除「整组全 NULL」**
    - PG **普通**唯一索引用 `=` 比较 → 含 NULL 的行**永不冲突**（NULL≠NULL），
      即这些行**根本不受约束保护**；而 `GROUP BY` 把 NULL 归为同一组 →
      报出的正是「索引拦不住」的那些行。
    - 但要再分两种「含 NULL」的组：
      · **整组所有列都 NULL**（`ai_reports.task_id` 176 行全 NULL、`dashboards.code` 2 行全 NULL）
        → **设计如此**（该行在索引语义下无信息，无法判定是否同一实体）→ **排除**
      · **部分列有值、仅某列 NULL**（`wallet_withdrawals`：仅 `device_sn` NULL，其余 5 列有值）
        → ⚠️ **真缺口**（非 NULL 列足以判定「同一笔」，索引却拦不住）—— C-P1-7 本质

    ⚠️ 曾把「组内全为 NULL」一律当豁免 → 会在**未治理环境**把 wallet 的 15601 行真重复
       静默跳过。排除条件必须精确到「**整组**全 NULL」，不能放宽为「组内含 NULL」。

产出（每条 finding 三件套，保证可审计、拒绝黑盒结论）：
    metrics    量化证据（重复组数 / 冗余行数 / 覆盖行数 / 占比 / 缺口类型）
    samples    样本行（**脱敏**，≤10 条）
    repro_sql  复现 SQL（人工可直接在库上执行，核对 metrics 数字）
"""

import datetime as _dt
import decimal as _decimal
import hashlib
import logging
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.governance_finding import (
    DETECTOR_UNIQUE_INDEX_DUP,
    FINDING_STATUS_OPEN,
    SEVERITY_HIGH,
    SEVERITY_WARN,
    GovernanceFinding,
)

logger = logging.getLogger(__name__)

# 时区（AGENTS.md 铁律：时区换算走 Asia/Shanghai）
_TZ_CN = _dt.timezone(_dt.timedelta(hours=8))

# 缺口类型（metrics.gap_kind）
GAP_CONSTRAINT_VIOLATED = "constraint_violated"  # 索引已 NULLS NOT DISTINCT 却有重复 → 约束未生效
GAP_PROTECTION_GAP = "protection_gap"            # 普通/部分索引（无 NND）→ 这些行不受约束保护

# 严重度阈值（v1 启发式；最终以研判层 verdict 为准，见设计 §5.2）
_HIGH_REDUNDANT_ROWS = 100    # 冗余行数 ≥ 100 → high
_HIGH_RATIO = 0.05            # 或占比 ≥ 5% → high

# 脱敏：列名命中这些片段的字符串值不落原文（保留「是否相等」信息即可供研判）
# 注意含常见缩写（acct/sn/no），避免因列名写法不同而漏脱敏
_SENSITIVE_HINTS = (
    "account", "acct", "phone", "mobile", "id_card", "idcard", "name", "device_sn",
    "order_no", "token", "password", "secret", "email", "address", "bank", "card", "sn",
)

_SAMPLE_LIMIT_DEFAULT = 10


# ─────────────────────────────────────────────────────────────
# 索引定义解析（铁律 ①）
# ─────────────────────────────────────────────────────────────
def _split_top_level(s: str) -> list[str]:
    """按顶层逗号切分（括号内逗号不切），把列清单拆成单项。

    例: "a, date(b), COALESCE(c, 0)" → ["a", "date(b)", "COALESCE(c, 0)"]
    """
    out: list[str] = []
    depth, cur = 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def parse_indexdef(ddl: str) -> tuple[str, str]:
    """从 `CREATE UNIQUE INDEX ...` 取出 (列清单原文, WHERE 条件)。

    形如: CREATE UNIQUE INDEX ix ON public.t USING btree (a, date(b)) WHERE (c IS NULL)
    列清单 = `USING <method> (` 之后与其配对 `)` 之间的**原文**（保留表达式，铁律 ①）。

    返回 ("", "") 表示解析失败（调用方应跳过该索引）。
    """
    m = re.search(r"USING\s+\w+\s*\(", ddl, re.I)
    if not m:
        return "", ""
    start = m.end() - 1  # 指向 `(`
    depth, i = 0, start
    while i < len(ddl):
        if ddl[i] == "(":
            depth += 1
        elif ddl[i] == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    cols = ddl[start + 1:i]
    where = ""
    mw = re.search(r"\bWHERE\b\s+(.+)$", ddl[i:], re.I | re.S)
    if mw:
        where = mw.group(1).strip()
    return cols, where


def _any_value_cond(cols: str) -> str:
    """「任一索引列非 NULL」条件（判「整组是否全 NULL」用，铁律 ②）。"""
    return " OR ".join(f"{c} IS NOT NULL" for c in _split_top_level(cols))


def candidate_indexes(db: Session, *, tables: list[str] | None = None) -> list[dict]:
    """public 下**含可空列或表达式列**的唯一索引清单（其余索引无 NULL 去重风险）。

    `tables` 非空时只扫这些表（供定向体检/单测用）。
    """
    params: dict = {}
    tbl_filter = ""
    if tables:
        tbl_filter = " AND t.relname = ANY(:tables)"
        params["tables"] = tables

    rows = db.execute(text(f"""
        SELECT t.relname AS tbl, ic.relname AS idx,
               i.indpred IS NOT NULL AS partial,
               i.indnullsnotdistinct AS nnd,
               pg_get_indexdef(i.indexrelid) AS ddl
        FROM pg_index i
        JOIN pg_class ic ON ic.oid = i.indexrelid
        JOIN pg_class t ON t.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE i.indisunique AND n.nspname = 'public' AND NOT i.indisprimary
              {tbl_filter}
        ORDER BY 1, 2
    """), params).mappings().all()

    # 含可空列的索引（表达式列无法判 NOT NULL → 由调用方按 has_expr 保守纳入）
    nullable = {
        (r.tbl, r.idx)
        for r in db.execute(text(f"""
            SELECT t.relname AS tbl, ic.relname AS idx
            FROM pg_index i
            JOIN pg_class ic ON ic.oid = i.indexrelid
            JOIN pg_class t ON t.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
            WHERE i.indisunique AND n.nspname = 'public' AND NOT i.indisprimary
                  AND k.ord > 0 AND NOT a.attnotnull
                  {tbl_filter}
        """), params).all()
    }

    out: list[dict] = []
    for r in rows:
        cols, where = parse_indexdef(r["ddl"])
        if not cols:
            logger.warning("governance D1: 索引定义解析失败, 跳过 %s.%s", r["tbl"], r["idx"])
            continue
        if (r["tbl"], r["idx"]) not in nullable and "(" not in cols:
            continue  # 全列 NOT NULL 且无表达式 → 无 NULL 去重风险
        out.append({"table_name": r["tbl"], "index_name": r["idx"], "cols": cols,
                    "where": where, "is_partial": r["partial"],
                    "nulls_not_distinct": r["nnd"], "indexdef": r["ddl"]})
    return out


# ─────────────────────────────────────────────────────────────
# 指标与复现 SQL
# ─────────────────────────────────────────────────────────────
def build_repro_sql(table_name: str, cols: str, where: str) -> str:
    """生成可人工执行的复现 SQL（与 metrics 同源，数字必须对得上）。

    `bool_or(任一列 IS NOT NULL)` 即铁律 ② 的「排除整组全 NULL」条件 —— 与 metrics 完全一致，
    避免出现「SQL 跑出来的数 ≠ finding 里的数」这种不可审计的情况。
    """
    any_val = _any_value_cond(cols)
    sql = ('SELECT count(*) AS dup_groups, coalesce(sum(c - 1), 0) AS redundant_rows\n'
           'FROM (\n'
           f'    SELECT count(*) AS c, bool_or({any_val}) AS has_value\n'
           f'    FROM "{table_name}"\n')
    if where:
        sql += f"    WHERE {where}\n"
    sql += (f'    GROUP BY {cols}\n'
            '    HAVING count(*) > 1\n'
            ') x\n'
            'WHERE x.has_value')
    return sql


def _gap_metrics(db: Session, table_name: str, cols: str, where: str) -> dict:
    """统计真重复（已排除整组全 NULL，铁律 ②）+ 覆盖率指标。

    `total_rows` = 该索引**覆盖**的行数（部分索引只算 WHERE 命中的行）——
    占比 `ratio` 的分母用它才合理（否则部分索引的占比会被全表行数稀释）。
    """
    any_val = _any_value_cond(cols)
    where_sql = f" WHERE {where}" if where else ""

    total = db.execute(
        text(f'SELECT count(*) FROM "{table_name}"{where_sql}')
    ).scalar() or 0

    row = db.execute(text(
        f'SELECT count(*) AS dup_groups, coalesce(sum(c - 1), 0) AS redundant_rows\n'
        f'FROM (\n'
        f'    SELECT count(*) AS c, bool_or({any_val}) AS has_value\n'
        f'    FROM "{table_name}"{where_sql}\n'
        f'    GROUP BY {cols}\n'
        f'    HAVING count(*) > 1\n'
        f') x\n'
        f'WHERE x.has_value'
    )).one()

    dup_groups, redundant = int(row.dup_groups), int(row.redundant_rows)
    return {
        "dup_groups": dup_groups,
        "redundant_rows": redundant,
        "total_rows": int(total),
        "ratio": round(redundant / total, 6) if total else 0.0,
    }


def _severity(metrics: dict, gap_kind: str) -> str:
    """原始严重度（v1 启发式，仅参考；最终以研判 verdict 为准）。

    - `constraint_violated`（索引已 NND 却仍有重复）→ 恒 high：约束本该拦住，说明有脏数据
    - `protection_gap` → 按冗余规模分档
    """
    if gap_kind == GAP_CONSTRAINT_VIOLATED:
        return SEVERITY_HIGH
    if (metrics["redundant_rows"] >= _HIGH_REDUNDANT_ROWS
            or metrics["ratio"] >= _HIGH_RATIO):
        return SEVERITY_HIGH
    return SEVERITY_WARN


# ─────────────────────────────────────────────────────────────
# 样本（脱敏）
# ─────────────────────────────────────────────────────────────
def _is_sensitive(col: str) -> bool:
    low = col.lower()
    return any(h in low for h in _SENSITIVE_HINTS)


def _json_safe(val):
    """把样本值转成可落 JSONB 的形式（datetime/Decimal 等直落会 `TypeError`）。

    JSONB 落库失败会让整条 finding 丢证据，故所有样本值统一过这道转换。
    """
    if val is None or isinstance(val, (bool, int, float, str)):
        return val
    if isinstance(val, (_dt.datetime, _dt.date, _dt.time)):
        return val.isoformat()
    if isinstance(val, _dt.timedelta):
        return str(val)
    if isinstance(val, (bytes, bytearray, memoryview)):
        return bytes(val).hex()
    if isinstance(val, _decimal.Decimal):
        return float(val)
    return str(val)


def mask_value(col: str, val):
    """按列名脱敏：敏感列的字符串值 → 确定性哈希（保留「是否相等」信息，不泄露原文）。

    为什么用确定性哈希而非打星号：AI 研判需要看出「这些行的该列是否相同」，
    哈希对相同输入给出相同输出 → 组内一致性可判，但原文不可还原。
    非敏感列（数字/日期/短枚举）原样返回，保留判断所需信息。
    返回值统一经 `_json_safe`（datetime/Decimal 直接落 JSONB 会报错）。
    """
    if val is None:
        return None
    if _is_sensitive(col):
        return "#" + hashlib.sha256(str(val).encode("utf-8")).hexdigest()[:8]
    return _json_safe(val)


def collect_samples(db: Session, table_name: str, cols: str, where: str,
                    *, limit: int = _SAMPLE_LIMIT_DEFAULT) -> list[dict]:
    """取重复组样本（脱敏，≤limit 条）。

    每组给出：组键各列值（脱敏）+ 该组行数 `_dup_count` + 组内最小 `_sample_ids`（便于人工定位）。
    表无 `id` 列时省略 `_sample_ids`。

    ⚠️ `HAVING ... AND bool_or(任一列非 NULL)` 与 `_gap_metrics` 的过滤条件**必须一致**：
    否则样本会取自 metrics 已排除的「整组全 NULL」组，出现「样本与 metrics 对不上」——
    那样 AI 研判会被误导，人工核对 repro_sql 也会发现数字不符。
    """
    col_list = _split_top_level(cols)
    has_id = db.execute(text(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = 'id'"
    ), {"t": table_name}).scalar()
    id_agg = ", array_agg(id ORDER BY id) AS sample_ids" if has_id else ""

    where_sql = f" WHERE {where}" if where else ""
    rows = db.execute(text(
        f'SELECT {cols}, count(*) AS dup_count{id_agg}\n'
        f'FROM "{table_name}"{where_sql}\n'
        f'GROUP BY {cols}\n'
        f'HAVING count(*) > 1 AND bool_or({_any_value_cond(cols)})\n'
        f'ORDER BY dup_count DESC\n'
        f'LIMIT :limit'
    ), {"limit": limit}).mappings().all()

    out: list[dict] = []
    for r in rows:
        d = dict(r)
        sample: dict = {}
        for c in col_list:
            sample[c] = mask_value(c, d.get(c))
        sample["_dup_count"] = int(d.get("dup_count") or 0)
        ids = d.get("sample_ids")
        if ids:
            sample["_sample_ids"] = list(ids)[:5]
        out.append(sample)
    return out


# ─────────────────────────────────────────────────────────────
# 扫描（核心入口）
# ─────────────────────────────────────────────────────────────
def scan_unique_index_dups(db: Session, *, tables: list[str] | None = None,
                           limit_samples: int = _SAMPLE_LIMIT_DEFAULT) -> list[dict]:
    """D1 扫描：返回「存在真重复」的唯一索引清单（不落库，纯探测）。

    每个元素含 metrics / samples / repro_sql / severity_raw，可直接交研判层。
    """
    findings: list[dict] = []
    for cand in candidate_indexes(db, tables=tables):
        try:
            metrics = _gap_metrics(db, cand["table_name"], cand["cols"], cand["where"])
        except Exception as e:  # noqa: BLE001  单个索引失败不影响其余（表达式可能不支持）
            # 必须 rollback：PG 事务一旦出错即中止，不 rollback 后续查询会全部级联报错
            db.rollback()
            logger.warning("governance D1: 指标计算失败, 跳过 %s.%s: %s",
                           cand["table_name"], cand["index_name"], e)
            continue

        if metrics["redundant_rows"] <= 0:
            continue  # 无真重复（含「整组全 NULL」被排除的情形）→ 不产 finding

        gap_kind = (GAP_CONSTRAINT_VIOLATED if cand["nulls_not_distinct"]
                    else GAP_PROTECTION_GAP)
        metrics = {**metrics, "gap_kind": gap_kind,
                   "index_columns": _split_top_level(cand["cols"]),
                   "index_where": cand["where"] or None,
                   "is_partial": cand["is_partial"],
                   "nulls_not_distinct": cand["nulls_not_distinct"]}

        try:
            samples = collect_samples(db, cand["table_name"], cand["cols"],
                                      cand["where"], limit=limit_samples)
        except Exception as e:  # noqa: BLE001  样本失败不阻塞 finding（证据降级而非丢弃）
            db.rollback()
            logger.warning("governance D1: 样本采集失败 %s.%s: %s",
                           cand["table_name"], cand["index_name"], e)
            samples = []

        findings.append({
            "detector": DETECTOR_UNIQUE_INDEX_DUP,
            "table_name": cand["table_name"],
            "target": cand["index_name"],
            "severity_raw": _severity(metrics, gap_kind),
            "metrics": metrics,
            "samples": samples,
            "repro_sql": build_repro_sql(cand["table_name"], cand["cols"], cand["where"]),
        })
    return findings


def persist_findings(db: Session, findings: list[dict]) -> list[GovernanceFinding]:
    """把探测结果落库为 `governance_findings`（status=open，待研判）。

    同一 `(detector, table_name, target)` **已有 open finding 时不重复落库**（更新其证据即可）——
    否则定时体检每轮都会堆一条重复记录（同 #166 的教训：无节流的重复留痕会淹没真实变化）。
    已 resolved/ignored 的历史 finding 不影响新 finding 落库（问题复现应重新暴露）。
    """
    saved: list[GovernanceFinding] = []
    now = _dt.datetime.now(_dt.timezone.utc)

    for f in findings:
        existing = db.query(GovernanceFinding).filter(
            GovernanceFinding.detector == f["detector"],
            GovernanceFinding.table_name == f["table_name"],
            GovernanceFinding.target == f["target"],
            GovernanceFinding.status == FINDING_STATUS_OPEN,
        ).order_by(GovernanceFinding.detected_at.desc()).first()

        if existing is not None:
            # 同一未处置问题：刷新证据与探测时刻，不新增行
            existing.severity_raw = f["severity_raw"]
            existing.metrics = f["metrics"]
            existing.samples = f["samples"]
            existing.repro_sql = f["repro_sql"]
            existing.detected_at = now
            saved.append(existing)
            continue

        row = GovernanceFinding(
            detector=f["detector"],
            table_name=f["table_name"],
            target=f["target"],
            severity_raw=f["severity_raw"],
            metrics=f["metrics"],
            samples=f["samples"],
            repro_sql=f["repro_sql"],
            detected_at=now,
            status=FINDING_STATUS_OPEN,
        )
        db.add(row)
        saved.append(row)

    db.commit()
    for row in saved:
        db.refresh(row)
    return saved


def run_d1_scan(db: Session, *, tables: list[str] | None = None,
                persist: bool = True, limit_samples: int = _SAMPLE_LIMIT_DEFAULT) -> dict:
    """D1 体检入口：扫描 → （默认）落库。返回汇总，供接口/调度层使用（工单 #163/#165）。

    返回: {"detector", "scanned", "found", "persisted", "findings": [...]}
    """
    findings = scan_unique_index_dups(db, tables=tables, limit_samples=limit_samples)
    persisted = persist_findings(db, findings) if persist else []
    summary = {
        "detector": DETECTOR_UNIQUE_INDEX_DUP,
        "scanned": len(candidate_indexes(db, tables=tables)),
        "found": len(findings),
        "persisted": len(persisted),
        "findings": findings,
    }
    logger.info("governance D1 扫描完成: 候选 %s 个, 发现 %s 个, 落库 %s 个",
                summary["scanned"], summary["found"], summary["persisted"])
    return summary


# ═══════════════════════════════════════════════════════════════════════
# D2: 表级行数突变（工单 #163，设计 §4.1）
#
# 时间列权威来源 = `data_sources.time_field`（数据源注册表已声明，含时区换算规则
# 所在表）。对每个 active 数据源：本期窗口 count vs 上期同长度窗口 count → 增幅比。
# 产出 metrics：{cur_rows, prev_rows, growth_ratio, window_days, period,
#                 curve:[{date,count}]（覆盖本期+上期，便于定位突变日）}
#     curve 覆盖两期而非只本期：drop 类 finding 的本期正是掉下来的那段（可能全空），
#     只看本期会看不出「原本有数据」。
# 跳水（减半）同报，避免只盯增长漏掉「整批数据丢失」。
# ═══════════════════════════════════════════════════════════════════════
DETECTOR_ROW_COUNT_JUMP = "row_count_jump"

# D2 默认参数（v1 启发式；研判层可覆盖）
_D2_WINDOW_DAYS = 7          # 对比窗口长度（自然日）
_D2_RATIO_THRESHOLD = 1.5    # 本期/上期 ≥ 1.5 → 增长异常
_D2_DROP_THRESHOLD = 0.5     # 本期/上期 ≤ 0.5 → 跳水异常（整批缺失）
# 绝对量下限：两窗口的最大行数低于此值直接跳过。
# 为什么必须要有（2026-09-20 生产首次体检实证）：只有比值没有绝对量，
# 「2 → 6」「1 → 15」这类**个位数波动**会被判成 3 倍/15 倍增长 —— 对
# `card_complaints`(投诉登记)、`device_tags`(标签字典) 这种低频/配置表，
# 绝对量小、比值天然剧烈，纯比值判据会持续产出噪音（人工核验 3 条里 2 条是噪音）。
_D2_MIN_ABS_ROWS = 20
# 报 high 还需**绝对变化量**达标：否则「25 → 50」这种小表 2 倍也会被标 high，
# 严重度虚高会让真正的量变（数千行级）淹没在告警里。
_D2_HIGH_ABS_DELTA = 100


def _now_cn() -> _dt.datetime:
    """当前北京时间（AGENTS.md 铁律：时区换算走 Asia/Shanghai）。"""
    return _dt.datetime.now(_TZ_CN)


def _table_exists(db: Session, table: str) -> bool:
    return bool(db.execute(text(
        "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=:t"
    ), {"t": table}).scalar())


def _table_time_column(db: Session, table: str) -> tuple[str, str] | None:
    """取表的时间列 → (列名, 数据类型)。

    优先 `data_sources.time_field`（数据源注册表已声明），否则回退该表第一个 timestamp 列。
    返回类型是为了生成**正确的北京日期表达式**（见 `_date_expr`）—— 类型不同表达式不同，
    弄错会让窗口边界整体偏 8 小时。
    """
    r = db.execute(text(
        "SELECT time_field FROM data_sources WHERE table_name=:t AND time_field IS NOT NULL"
    ), {"t": table}).scalar()
    if r:
        dtype = db.execute(text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=:t AND column_name=:c"
        ), {"t": table, "c": r}).scalar()
        if dtype:
            return r, dtype
    row = db.execute(text(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t "
        "AND data_type IN ('timestamp with time zone','timestamp without time zone') "
        "ORDER BY ordinal_position LIMIT 1"
    ), {"t": table}).mappings().first()
    return (row["column_name"], row["data_type"]) if row else None


def _date_expr(time_col: str, data_type: str) -> str:
    """生成「按北京时区取日期」的 SQL 表达式（AGENTS.md 时区铁律）。

    - `timestamptz`：`AT TIME ZONE 'Asia/Shanghai'` 得到北京本地时间，再 `::date` ✅
      （生产 21 个数据源时间列全是 timestamptz）
    - `timestamp`（naive）：**不能**用 AT TIME ZONE —— 它先按会话时区解释再转，
      结果又受会话 TimeZone 影响（容器是 UTC 时会整体偏 8 小时）。naive 列本身就是
      本地时间，直接 `::date`
    - `date`：原样
    """
    if data_type == "timestamp with time zone":
        return f'("{time_col}" AT TIME ZONE \'Asia/Shanghai\')::date'
    if data_type == "date":
        return f'"{time_col}"'
    return f'"{time_col}"::date'


def _datasource_tables(db: Session) -> list[str]:
    """active 数据源对应的物理表（排除 placeholder 占位与 system 本系统表）。"""
    rows = db.execute(text(
        "SELECT table_name FROM data_sources "
        "WHERE status != 'placeholder' AND source_type != 'system' "
        "AND table_name IS NOT NULL"
    )).scalars().all()
    return [t for t in rows if _table_exists(db, t)]


def _daily_counts(db: Session, table: str, date_expr: str, *, n_days: int,
                  now: _dt.datetime) -> list[dict]:
    """近 n 天按日 count（北京时区）。返回 [{date, count}, ...] 升序。"""
    start = (now - _dt.timedelta(days=n_days - 1)).strftime("%Y-%m-%d")
    rows = db.execute(text(
        f'SELECT {date_expr} AS d, count(*) AS n'
        f' FROM "{table}"'
        f' WHERE {date_expr} >= :start'
        f' GROUP BY 1 ORDER BY 1'
    ), {"start": start}).mappings().all()
    return [{"date": str(r["d"]), "count": int(r["n"])} for r in rows]


def scan_row_count_jump(db: Session, *, window_days: int = _D2_WINDOW_DAYS,
                        ratio_gt: float = _D2_RATIO_THRESHOLD,
                        ratio_lt: float = _D2_DROP_THRESHOLD,
                        tables: list[str] | None = None) -> list[dict]:
    """D2 行数突变探测：active 数据源中，本期 vs 上期窗口行数增幅/跳水超阈值者。

    对比口径：本期 = 最近 window_days 天（含今天，按**北京时区日期**）；
    上期 = 期前的 window_days 天。growth_ratio = cur / prev（prev=0 时特判为异常）。
    返回 findings（不落库），每条含 metrics.curve 按日曲线。

    ⚠️ 窗口计数与 repro_sql **必须用同一个日期表达式**（`_date_expr`），
    否则「metrics 数字 ≠ 人工跑 repro_sql 的数字」，违背可审计原则（同 D1 的 samples 一致性教训）。

    ⚠️ 只读探测，**不得无条件 rollback** —— 调用方可能在事务中（单测的临时表就在这里），
    无条件 rollback 会把调用方未提交的 DDL/数据一并回滚。仅出错时才 rollback 清理中止事务。
    """
    srcs = tables if tables is not None else _datasource_tables(db)
    findings: list[dict] = []
    now = _now_cn()

    for table in srcs:
        if not _table_exists(db, table):
            continue
        tc = _table_time_column(db, table)
        if not tc:
            continue
        time_col, dtype = tc
        date_expr = _date_expr(time_col, dtype)
        start_cur = (now - _dt.timedelta(days=window_days - 1)).date()
        start_prev = start_cur - _dt.timedelta(days=window_days)
        try:
            cur = db.execute(text(
                f'SELECT count(*) FROM "{table}" WHERE {date_expr} >= :s'
            ), {"s": start_cur.isoformat()}).scalar() or 0
            prev = db.execute(text(
                f'SELECT count(*) FROM "{table}" WHERE {date_expr} >= :s AND {date_expr} < :e'
            ), {"s": start_prev.isoformat(), "e": start_cur.isoformat()}).scalar() or 0
        except Exception as e:  # noqa: BLE001  字段类型特殊/表结构异常, 跳过该表
            db.rollback()
            logger.warning("governance D2: 表 %s 统计失败, 跳过: %s", table, e)
            continue

        # 绝对量下限：低频/配置表（投诉登记、标签字典）个位数波动比值天然剧烈 → 噪音，跳过
        if max(cur, prev) < _D2_MIN_ABS_ROWS:
            continue

        # 增幅/跳水判定（prev=0 且 cur>0 视为「从无到有」= 增长异常边界）
        if prev > 0:
            ratio = cur / prev
            if ratio >= ratio_gt:
                gap_kind = "growth"
            elif ratio <= ratio_lt:
                gap_kind = "drop"
            else:
                continue  # 在阈值内 → 正常波动，不产 finding
            metrics = {"cur_rows": int(cur), "prev_rows": int(prev),
                       "growth_ratio": round(ratio, 4), "window_days": window_days,
                       "window_period": "day", "gap_kind": gap_kind}
        elif cur > 0:
            # 上期 0、本期有数据：从无到有（新增采集源/补采），比值无意义
            metrics = {"cur_rows": int(cur), "prev_rows": 0, "growth_ratio": None,
                       "window_days": window_days, "window_period": "day",
                       "gap_kind": "growth_from_zero"}
        else:
            continue  # 两窗口都 0，无信息

        # 曲线覆盖**本期+上期**（2×window_days）：只看本期时，drop 类 finding 的曲线是空的
        # （本期正是掉下来的那段），看不出「原本有数据」→ 证据不足。覆盖两期才能定位突变日。
        try:
            metrics["curve"] = _daily_counts(db, table, date_expr,
                                             n_days=window_days * 2, now=now)
        except Exception:  # noqa: BLE001  曲线失败不阻塞 finding（证据降级而非丢弃）
            db.rollback()
            metrics["curve"] = []

        # 严重度：从无到有 → high；否则需**比值翻倍 且 绝对变化量达标**（仅参考，最终以研判为准）
        is_high = (metrics["gap_kind"] == "growth_from_zero"
                   or (metrics["growth_ratio"] is not None
                       and metrics["growth_ratio"] >= 2.0
                       and abs(metrics["cur_rows"] - metrics["prev_rows"]) >= _D2_HIGH_ABS_DELTA))
        findings.append({
            "detector": DETECTOR_ROW_COUNT_JUMP,
            "table_name": table,
            "target": time_col,
            "severity_raw": SEVERITY_HIGH if is_high else SEVERITY_WARN,
            "metrics": metrics,
            "samples": [{"period": "cur", "start": start_cur.isoformat(), "count": int(cur)},
                        {"period": "prev", "start": start_prev.isoformat(), "count": int(prev)}],
            "repro_sql": build_d2_repro_sql(table, date_expr, window_days),
        })
    return findings


def build_d2_repro_sql(table: str, date_expr: str, window_days: int) -> str:
    """D2 复现 SQL：本期/上期 count 对比。

    `date_expr` 由 `_date_expr` 按列类型生成 —— 与 metrics 计数用**同一个表达式**，
    保证人工跑出来的数字与 finding 里的完全一致。
    """
    return (
        f'-- 本期(近{window_days}天)\n'
        f'SELECT count(*) FROM "{table}"\n'
        f'WHERE {date_expr} >= CURRENT_DATE - {window_days - 1};\n\n'
        f'-- 上期(前{window_days}天)\n'
        f'SELECT count(*) FROM "{table}"\n'
        f'WHERE {date_expr} < CURRENT_DATE - {window_days - 1}\n'
        f'  AND {date_expr} >= CURRENT_DATE - {window_days * 2};'
    )


# ═══════════════════════════════════════════════════════════════════════
# D3: 外键孤儿（工单 #163，设计 §4.1）
#
# 枚举 public 下所有 FOREIGN KEY 约束，用 `NOT EXISTS` 找「引用列非空但主表无对应主键」的行。
# 产出 metrics：{orphan_count, fk_constr, fk_validated, ref_cols, ref_table, pkey_cols}
#     samples：孤儿样本（脱敏，含行 id 便于人工核对是否真丢）。
# 用 `NOT EXISTS` 而非 `LEFT JOIN`：避开「多列 FK + NULL 参与」的等值 NULL 比较坑。
#
# ⚠️ 为什么要额外报「NOT VALID 外键」（2026-09-20 生产实证）：
#     PG 会**强制**校验已生效的 FK，所以 `convalidated=true` 的 FK 上孤儿恒为 0 ——
#     生产 100 个 FK 全是 validated，若只查孤儿则本探测器永远报 0（有检测等于没检测）。
#     真正的风险在 `NOT VALID`（建约束时不校验存量数据，典型于给脏数据补约束）：
#     约束看似存在、实际**不拦新增**、存量孤儿也留着 —— 与 D1 的「保护缺口」同一类问题。
#     故：有孤儿 → 报；无孤儿但 NOT VALID → 也报（gap_kind=unvalidated_constraint）。
# ═══════════════════════════════════════════════════════════════════════
DETECTOR_FK_ORPHAN = "fk_orphan"

# 缺口类型（metrics.gap_kind）
GAP_FK_ORPHAN = "orphan"                            # 存在孤儿行
GAP_FK_UNVALIDATED = "unvalidated_constraint"       # 约束 NOT VALID（未生效）


def _fk_constraints(db: Session, *, child_tables: list[str] | None = None) -> list[dict]:
    """public 下 FK 约束清单（含 `convalidated` 生效标志）。

    `child_tables` 限定**从哪些引用表**（即可能含孤儿的表）枚举 FK —— 供单测定向使用；
    None = 全部 FK。过滤按 child 表（不是 parent）：孤儿行长在 child 上。
    """
    params: dict = {}
    filter_sql = ""
    if child_tables:
        filter_sql = " AND conf.relname = ANY(:child_tables)"
        params["child_tables"] = child_tables
    rows = db.execute(text(f"""
        SELECT con.conname AS name,
               conf.relname AS child_table,
               parent.relname AS parent_table,
               con.convalidated AS validated,
               (SELECT array_agg(b.attname ORDER BY ord)
                FROM unnest(con.conkey) WITH ORDINALITY AS k(att, ord)
                JOIN pg_attribute b ON b.attrelid = con.conrelid AND b.attnum = k.att
               ) AS child_col_names,
               (SELECT array_agg(b.attname ORDER BY ord)
                FROM unnest(con.confkey) WITH ORDINALITY AS k(att, ord)
                JOIN pg_attribute b ON b.attrelid = con.confrelid AND b.attnum = k.att
               ) AS parent_col_names
        FROM pg_constraint con
        JOIN pg_class conf ON conf.oid = con.conrelid
        JOIN pg_class parent ON parent.oid = con.confrelid
        JOIN pg_namespace ns ON ns.oid = conf.relnamespace
        JOIN pg_namespace pns ON pns.oid = parent.relnamespace
        WHERE con.contype = 'f' AND ns.nspname = 'public' AND pns.nspname = 'public'
              {filter_sql}
        ORDER BY conf.relname, con.conname
    """), params).mappings().all()
    return [dict(r) for r in rows]


def scan_fk_orphans(db: Session, *, child_tables: list[str] | None = None,
                    limit_samples: int = _SAMPLE_LIMIT_DEFAULT) -> list[dict]:
    """D3 外键探测：孤儿行（引用列非空但主表无对应主键）+ NOT VALID（约束未生效）。

    `child_tables` 限定从哪些引用表反查（供单测定向用）；None = 全部 FK。

    ⚠️ 只读探测，**不得无条件 rollback** —— 调用方可能在事务中（单测临时表），
    会把调用方未提交的 DDL/数据一并回滚。仅出错时才 rollback 清理中止事务。
    """
    findings: list[dict] = []
    for fk in _fk_constraints(db, child_tables=child_tables):
        child, parent = fk["child_table"], fk["parent_table"]
        child_cols, parent_cols = fk["child_col_names"], fk["parent_col_names"]
        if not child_cols or not parent_cols:
            continue
        join_on = " AND ".join(
            f'c."{cc}" = p."{pc}"' for cc, pc in zip(child_cols, parent_cols))
        # 引用列全非空才判孤儿：PG 默认 MATCH SIMPLE 语义下「任一列为 NULL」的行不作检查，
        # 加 IS NOT NULL 既贴合语义、又避免把业务正常的 NULL 误报成孤儿
        nonnull = " AND ".join(f'c."{cc}" IS NOT NULL' for cc in child_cols)
        where_clause = f" WHERE {nonnull}" if nonnull else ""
        orphan_pred = (f'{where_clause} AND NOT EXISTS '
                       f'(SELECT 1 FROM "{parent}" p WHERE {join_on})')

        try:
            orphan = db.execute(text(
                f'SELECT count(*) FROM "{child}" c{orphan_pred}'
            )).scalar() or 0
        except Exception as e:  # noqa: BLE001  类型不匹配/结构特殊, 跳过该约束
            db.rollback()
            logger.warning("governance D3: 孤儿统计失败 c=%s->p=%s: %s", child, parent, e)
            continue

        validated = bool(fk["validated"])
        if orphan <= 0 and validated:
            continue  # 无孤儿且约束已生效 → 健康，不产 finding

        # 样本：孤儿行（引用列脱敏）+ 行 id（便于人工到库里核对是否真丢）
        samples: list[dict] = []
        if orphan > 0:
            select_list = ", ".join(f'c."{c}"' for c in child_cols)
            has_id = db.execute(text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name=:t AND column_name='id'"
            ), {"t": child}).scalar()
            id_cols = ", c.id AS _id" if has_id else ""
            try:
                sample_rows = db.execute(text(
                    f'SELECT {select_list}{id_cols} FROM "{child}" c{orphan_pred} '
                    f"ORDER BY 1 LIMIT :lim"
                ), {"lim": limit_samples}).mappings().all()
            except Exception:  # noqa: BLE001  样本失败不阻塞 finding（证据降级而非丢弃）
                db.rollback()
                sample_rows = []
            for r in sample_rows:
                d = {cc: mask_value(cc, r.get(cc)) for cc in child_cols}
                if r.get("_id") is not None:
                    d["_id"] = r["_id"]
                samples.append(d)

        gap_kind = GAP_FK_ORPHAN if orphan > 0 else GAP_FK_UNVALIDATED
        findings.append({
            "detector": DETECTOR_FK_ORPHAN,
            "table_name": child,
            "target": f"{fk['name']}→{parent}",
            "severity_raw": SEVERITY_HIGH if orphan > 0 else SEVERITY_WARN,
            "metrics": {"orphan_count": int(orphan), "fk_constr": fk["name"],
                        "fk_validated": validated, "gap_kind": gap_kind,
                        "child_table": child, "parent_table": parent,
                        "ref_cols": child_cols, "ref_table": parent,
                        "pkey_cols": parent_cols},
            "samples": samples,
            "repro_sql": build_d3_repro_sql(child, parent, child_cols, parent_cols),
        })
    return findings


def build_d3_repro_sql(child: str, parent: str, child_cols: list[str],
                       parent_cols: list[str]) -> str:
    """D3 复现 SQL：NOT EXISTS 找孤儿。"""
    join_on = " AND ".join(f'c."{cc}" = p."{pc}"' for cc, pc in zip(child_cols, parent_cols))
    nonnull = " AND ".join(f'c."{cc}" IS NOT NULL' for cc in child_cols)
    return (
        f'SELECT count(*) FROM "{child}" c\n'
        f'WHERE {nonnull}\n'
        f'  AND NOT EXISTS (SELECT 1 FROM "{parent}" p WHERE {join_on});'
    )

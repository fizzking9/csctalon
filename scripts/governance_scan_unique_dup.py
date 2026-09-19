#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""唯一索引重复扫描（治理框架 D1 探测器原型）

用途
----
扫描「可空列进唯一键 → 去重失效」的同类风险，把重复行按「索引能否拦住」定性为
**保护缺口** 或 **真违约**。2026-09-20 用于 C-P1-7 的全库排查。

> ⚠️ 该轮排查的**初版判据有误**（把「组内全为 NULL」当豁免 → 会把 wallet 这类真 bug 静默跳过），
> 已在铁律 ② 修正为「NULL 视为相等的重复 + 索引非 NND/未覆盖 = 保护缺口」。
> 生产环境因 #161 已治理（wallet 走部分+NND 索引），故两次判据下结论都是「无缺口」；
> 但在**未治理的环境**（如本地开发库）差异显著 —— 见下方实测。

设计依据见 docs/design/AI数据治理框架-20260919.md §4.1（D1 探测器）。

两条铁律（两轮误报教训，务必遵守）
----------------------------------
① **逐字符复现索引真实语义**
   列清单必须取 `pg_get_indexdef` 括号内原文（含 `date(x)` / `COALESCE(...)` 等表达式），
   部分索引必须带上 `WHERE indpred`。
   ✘ 错误做法：用 `indkey` 取裸列名分组 → `manual_refunds` 曾凭空报出「3062 行重复」
     （因 `date(register_time)` 截断未被复现，按原始时间戳分组多出分组）。

② **「重复」的判据 = NULL 视为相等（`GROUP BY` 原生语义），且排除「整组全 NULL」**
   这是本脚本最易搞反的地方，搞反会**静默漏掉真 bug**：

   - `GROUP BY cols HAVING count(*)>1` 中，PG 把 NULL 归为同一组（NULL 相等）
     → 报出的是「**业务语义上重复**」的行集 = 唯一索引**本该保护**的那些行
   - 而 PG **普通**唯一索引用 `=` 比较：含 NULL 的行**永不冲突**（NULL≠NULL）
     → 这些行**根本不受约束保护** —— 这正是 C-P1-7 的 bug 本质（唯一约束形同虚设）

   **但要再分两种「含 NULL」的组**（2026-09-20 生产实证）：
   | 组形态 | 例 | 判定 |
   |---|---|---|
   | **整组所有列都 NULL** | `ai_reports.task_id` 176 行全 NULL（规则层秒回无 task_id，见模型注释）、`dashboards.code` 2 行全 NULL | **设计如此**（该行在索引语义下无信息，无法判定是否同一实体）→ 报告但不计入缺口 |
   | **部分列有值、部分 NULL** | `wallet_withdrawals`：account_id/op_type/op_time/operator/op_amount 有值、仅 device_sn NULL | ⚠️ **真缺口**（非 NULL 列足以判定「同一笔」，索引却拦不住） |

   判定表：
   | 重复组（已排除全 NULL 组） | 索引保护状态 | 结论 |
   |---|---|---|
   | 0 | 任意 | 无重复 |
   | >0 | 已 NND（或部分+NND） | ⚠️ **真违约**（约束应拦住却没拦住，多为建约束前遗留脏数据） |
   | >0 | 普通 / 部分（无 NND） | ⚠️ **保护缺口**（这些行不受约束；需研判业务是否允许多个 NULL） |

   已防护的典型：`cs_records.uq_cs_record_business`（NND）、
   `wallet_withdrawals.uq_wallet_withdrawals_null_sn`（部分 + NND，见 #161）。

用法
----
    # 扫描（列出全部含可空列的唯一索引 + 重复计数）
    python scripts/governance_scan_unique_dup.py

    # 甄别（对报出重复的索引，逐个重复组分 NULL 组 / 非 NULL 组）
    python scripts/governance_scan_unique_dup.py --triage

容器内执行（生产只读排查）：
    docker cp scripts/governance_scan_unique_dup.py csctalon-backend:/tmp/ && \
    docker exec -w /app -e PYTHONPATH=/app csctalon-backend python /tmp/governance_scan_unique_dup.py
"""

import argparse
import re
import sys

from sqlalchemy import text


# ─────────────────────────────────────────────────────────────
# 索引定义解析（铁律 ①：复现真实语义）
# ─────────────────────────────────────────────────────────────
def _parse_indexdef(ddl: str) -> tuple[str, str]:
    """从 CREATE UNIQUE INDEX 语句取出 (列清单, WHERE 条件)。

    形如: CREATE UNIQUE INDEX ix ON public.t USING btree (a, date(b)) WHERE (c IS NULL)
    列清单 = USING btree 之后第一个顶层 `(` 与其配对 `)` 之间的**原文**（保留表达式）。
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


def _candidate_indexes(db) -> list[dict]:
    """public 下含可空列（或含表达式列）的唯一索引清单。"""
    rows = db.execute(text("""
        SELECT t.relname AS tbl, ic.relname AS idx,
               i.indpred IS NOT NULL AS partial,
               i.indnullsnotdistinct AS nnd,
               pg_get_indexdef(i.indexrelid) AS ddl
        FROM pg_index i
        JOIN pg_class ic ON ic.oid = i.indexrelid
        JOIN pg_class t ON t.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE i.indisunique AND n.nspname = 'public' AND NOT i.indisprimary
        ORDER BY 1, 2
    """)).mappings().all()

    # 含可空列者（表达式列无法判 NOT NULL → 由调用方按 has_expr 保守纳入）
    nullable = {
        (r.tbl, r.idx)
        for r in db.execute(text("""
            SELECT t.relname AS tbl, ic.relname AS idx
            FROM pg_index i
            JOIN pg_class ic ON ic.oid = i.indexrelid
            JOIN pg_class t ON t.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
            WHERE i.indisunique AND n.nspname = 'public' AND NOT i.indisprimary
              AND k.ord > 0 AND NOT a.attnotnull
        """)).all()
    }

    out = []
    for r in rows:
        cols, where = _parse_indexdef(r["ddl"])
        if not cols:
            continue
        if (r["tbl"], r["idx"]) not in nullable and "(" not in cols:
            continue  # 全列 NOT NULL 且无表达式 → 无 NULL 去重风险
        out.append({"tbl": r["tbl"], "idx": r["idx"], "cols": cols, "where": where,
                    "partial": r["partial"], "nnd": r["nnd"], "ddl": r["ddl"]})
    return out


def _split_top_level(s: str) -> list[str]:
    """按顶层逗号切分（括号内逗号不切），用于把列清单拆成单项。

    例: "a, date(b), COALESCE(c, 0)" → ["a", "date(b)", "COALESCE(c, 0)"]
    """
    out, depth, cur = [], 0, ""
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


def _any_null_cond(cols: str) -> str:
    """生成「任一索引列为 NULL」的条件（多列用 OR，与 GROUP BY 的 NULL 相等语义对齐）。"""
    return " OR ".join(f"{c} IS NULL" for c in _split_top_level(cols))


def _dup_stats(db, tbl: str, cols: str, where: str) -> tuple[int, int]:
    """按索引语义统计 (重复组数, 冗余行数)。

    注意 `GROUP BY` 把 NULL 归为同一组（NULL 相等），这正是判据（见文件头铁律 ②）。
    """
    sql = f'SELECT count(*) AS g, coalesce(sum(c - 1), 0) AS redundant FROM (SELECT count(*) c FROM "{tbl}"'
    if where:
        sql += f" WHERE {where}"
    sql += f" GROUP BY {cols} HAVING count(*) > 1) x"
    row = db.execute(text(sql)).one()
    return int(row.g), int(row.redundant)


def _real_gap_stats(db, tbl: str, cols: str, where: str) -> tuple[int, int]:
    """排除「整组所有列都 NULL」后的真重复 (组数, 行数)。

    依据铁律 ②：仅「部分列有值」的重复才构成保护缺口；整组全 NULL 属设计如此。
    判「组内是否有值」用 `bool_or(任一列 IS NOT NULL)`（对 `date(x)` 等表达式列亦合法）。
    """
    any_val = " OR ".join(f"{c} IS NOT NULL" for c in _split_top_level(cols))
    sql = (f'SELECT count(*) AS g, coalesce(sum(c - 1), 0) AS redundant FROM ('
           f'SELECT count(*) AS c, bool_or({any_val}) AS has_val FROM "{tbl}"')
    if where:
        sql += f" WHERE {where}"
    sql += f" GROUP BY {cols} HAVING count(*) > 1) x WHERE x.has_val"
    row = db.execute(text(sql)).one()
    return int(row.g), int(row.redundant)


def scan(db) -> list[dict]:
    """扫描模式：列出候选索引与重复计数，按「保护缺口 / 真违约」分类。

    判定依据见文件头铁律 ②。
    """
    cands = _candidate_indexes(db)
    print(f"public 下含可空列/表达式的唯一索引: {len(cands)} 个（重复计数已排除「整组全 NULL」）\n")

    gaps: list[dict] = []   # 保护缺口（普通/部分 无 NND，却有真重复）
    for c in cands:
        kind = ("部分" if c["partial"] else "普通") + ("+NND" if c["nnd"] else "")
        try:
            total_redundant = _dup_stats(db, c["tbl"], c["cols"], c["where"])[1]
            real_groups, real_redundant = _real_gap_stats(db, c["tbl"], c["cols"], c["where"])
        except Exception as e:  # noqa: BLE001  个别索引表达式特殊，单独报告
            # 必须 rollback: PG 事务一旦出错即中止, 不 rollback 后续查询会全部级联报错
            db.rollback()
            print(f"  {c['tbl']}.{c['idx']:<44} {kind:<9} ERR {str(e)[:50]}")
            continue

        if real_redundant == 0:
            note = ("全 NULL 组" if total_redundant and not real_redundant else (
                "已防护(NULLS NOT DISTINCT)" if c["nnd"] else (
                    "部分索引(仅覆盖部分行)" if c["partial"] else "可空但暂无重复")))
            if total_redundant and not real_redundant and not c["nnd"]:
                note += "（设计如此, 排除）"
            print(f"  {c['tbl']}.{c['idx']:<44} {kind:<9} {total_redundant:>6} 行   {note}")
            continue

        # 有真重复（部分列有值）：按「索引能否拦住这些行」定性
        if c["nnd"]:
            tag = "⚠️ 真违约(约束应拦住却存在重复)"
        else:
            tag = "⚠️ 保护缺口(这些行不受约束)"
            gaps.append({**c, "kind": kind, "groups": real_groups, "redundant": real_redundant})
        print(f"  {c['tbl']}.{c['idx']:<44} {kind:<9} {real_redundant:>6} 行   {tag}")

    print("\n" + "=" * 100)
    if gaps:
        print(f"⚠️  {len(gaps)} 个唯一索引存在**保护缺口**（重复行不受约束保护）：\n")
        for x in gaps:
            print(f"  · {x['tbl']} / {x['idx']}  [{x['kind']}]")
            print(f"      列: {x['cols']}" + (f"  WHERE {x['where']}" if x["where"] else ""))
            print(f"      重复 {x['groups']} 组 / {x['redundant']} 行")
        print("\n  → 建议: 参照 #161 方案（部分唯一索引 / NULLS NOT DISTINCT）+ 先清脏数据；")
        print("     若业务确允许多个 NULL，则属「设计如此」→ 由研判层登记规则豁免（勿静默跳过）")
    else:
        print("✅ 未发现保护缺口（所有含可空列的唯一索引均已 NND/部分索引防护，或暂无重复）")
    return gaps


def triage(db) -> None:
    """甄别模式：对报出重复的索引，逐个重复组标注「组内是否含 NULL」+ 索引能否拦住。

    用途：把「保护缺口」与「真违约」落到具体行，便于研判「业务是否允许多个 NULL」。
    """
    cands = _candidate_indexes(db)
    found = False
    for c in cands:
        try:
            _, redundant = _dup_stats(db, c["tbl"], c["cols"], c["where"])
        except Exception:  # noqa: BLE001
            continue
        if not redundant:
            continue
        found = True
        print(f"\n{'=' * 88}\n表 {c['tbl']} / 索引 {c['idx']}"
              f"  [{'部分' if c['partial'] else '普通'}{'+NND' if c['nnd'] else ''}]"
              f"\n  列: {c['cols']}" + (f"  WHERE {c['where']}" if c["where"] else "")
              + f"\n  重复 {redundant} 行")
        # 组键: 多列拼成可读文本(NULL 显式标 <NULL>), 避免多列 AS 单别名只显示末列
        key_expr = " || '|' || ".join(
            f"coalesce({c}::text, '<NULL>')" for c in _split_top_level(c["cols"]))
        sql = (f'SELECT {key_expr} AS v, count(*) AS c, '
               f'count(*) FILTER (WHERE {_any_null_cond(c["cols"])}) AS nulls FROM "{c["tbl"]}"')
        if c["where"]:
            sql += f" WHERE {c['where']}"
        sql += f" GROUP BY {c['cols']} HAVING count(*) > 1 ORDER BY c DESC"
        for r in db.execute(text(sql)).mappings().all():
            has_null = r["nulls"] > 0
            print(f"  值={r['v']!r:<40} 行数={r['c']:<6} "
                  + ("组内含 NULL（普通唯一索引拦不住 → 缺口）" if has_null
                     else "组内无 NULL（真违约）"))
        if c["nnd"]:
            print("  注: 该索引已 NULLS NOT DISTINCT → 存在重复即约束未生效(建约束前遗留脏数据)")
    if not found:
        print("✅ 无重复组，无需甄别")


def main() -> int:
    ap = argparse.ArgumentParser(description="唯一索引重复扫描 / 甄别（治理框架 D1 原型）")
    ap.add_argument("--triage", action="store_true",
                    help="甄别模式：逐个重复组分 NULL 组 / 非 NULL 组")
    args = ap.parse_args()

    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        if args.triage:
            triage(db)
        else:
            scan(db)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

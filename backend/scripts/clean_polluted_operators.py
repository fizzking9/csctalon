"""清理写进"人名字段"的时间戳污染值(2026-08-26)

背景: BMS 退费单导出「操作人」列历史曾混入时间戳(如 "2026-04-26 16:00:37"),
旧数据导入时 upsert._sanitize_name_field 只拦纯数字不拦时间戳, 导致 refunds.operator 等
被污染。现已在 _sanitize_name_field 加 _DATETIME_LIKE_RE 防再写入; 本脚本一次性清理存量。

策略: 对 "应该为人名" 的字段, 把形如 ^YYYY[-/]MM[-/]DD 的值置 NULL
  (时间戳不可能是合法人名, 置空不回退)。退款分析「退款人」已改为审核人优先,
  置空 operator 后无审核人时回退显示为系统自动, 比显示时间戳更干净。

用法:
  python scripts/clean_polluted_operators.py            # dry-run 只打印数量
  python scripts/clean_polluted_operators.py --apply    # 执行更新
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from app.core.database import SessionLocal
from sqlalchemy import text
from sqlalchemy.orm import Session

# 时间戳/日期类模式: 以 YYYY[-/]MM[-/]DD 开头(显式字符类, 兼容 PG POSIX 正则)
_PAT = r"^[0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2}"
# 备注/算式类污染: 含括号或四则符号
_JUNK_CHARS = r"[【】÷＝=\u002a]"
# 超长护栏(人名字段不应超过)
_MAX_NAME_LEN = 24

# 应为人名的字段 (table, column) —— 全为白名单, 无注入风险
_PERSON_FIELDS: list[tuple[str, str]] = [
    ("refunds", "operator"),
    ("refunds", "auditor"),
    ("refund_wide", "operator"),
    ("refund_wide", "auditor"),
    ("wallet_withdrawals", "operator"),
    ("cs_records", "operator"),
    ("manual_refunds", "refund_operator"),
    ("manual_refunds", "registrant"),
    ("service_tickets", "handler"),
    ("service_tickets", "registrant"),
    ("shanmao_refunds", "operator"),
    ("shanmao_refunds", "auditor"),
]


@dataclass
class RowResult:
    table: str
    column: str
    exists: bool = False
    matched: int = 0
    updated: int = 0


def column_exists(db: Session, table: str, column: str) -> bool:
    return bool(
        db.execute(
            text("SELECT 1 FROM information_schema.columns WHERE table_name=:t AND column_name=:c"),
            {"t": table, "c": column},
        ).first()
    )


def main() -> int:
    apply = "--apply" in sys.argv
    results: list[RowResult] = []
    # 命中判定: 时间戳开头 OR 含备注/算式字符 OR 超长
    where = '("{col}" ~ :pat OR "{col}" ~ :jk OR length(btrim("{col}")) > :ml)'

    with SessionLocal() as db:
        for table, column in _PERSON_FIELDS:
            res = RowResult(table=table, column=column)
            if not column_exists(db, table, column):
                results.append(res)
                continue
            res.exists = True
            cond = where.format(col=column)
            params = {"pat": _PAT, "jk": _JUNK_CHARS, "ml": _MAX_NAME_LEN}
            res.matched = int(
                db.execute(
                    text(f'SELECT count(*) FROM "public"."{table}" WHERE "{column}" IS NOT NULL AND {cond}'),
                    params,
                ).scalar()
                or 0
            )
            if apply and res.matched:
                upd = db.execute(
                    text(
                        f'UPDATE "public"."{table}" SET "{column}" = NULL '
                        f'WHERE "{column}" IS NOT NULL AND {cond}'
                    ),
                    params,
                )
                res.updated = upd.rowcount or 0
            results.append(res)
        if apply:
            db.commit()

    print(f"判定: 时间戳开头 || 含备注/算式字符(【】÷=*) || 超长>{_MAX_NAME_LEN}")
    print(f"标识 待清理(总命中)  已清理  表.列")
    total = 0
    for r in results:
        if not r.exists:
            print(f"  -  (跳过,无此列)                       {r.table}.{r.column}")
            continue
        total += r.matched
        mark = "已执行" if apply else "模拟"
        print(f"  {mark}  {r.matched:>9}    {r.updated:>6}   {r.table}.{r.column}")
    print(f"\n汇总: 命中(待处理) {total} 行; 本次操作: {'执行更新' if apply else '仅模拟(dry-run, 未写库)'}")
    if not apply:
        print("要真正执行请追加 --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
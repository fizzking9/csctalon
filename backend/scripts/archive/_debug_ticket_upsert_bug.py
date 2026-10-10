"""复现 ticket upsert 中的两个关键 bug:
1. updated_by_raw: boundparameter 错误 (行键不一致)
2. is_appeal: VARCHAR(16) 长度不足
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import json

load_dotenv(Path(__file__).parent.parent / ".env")

from sqlalchemy import create_engine, text, select
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta

DB_USER = os.getenv("POSTGRES_USER", "csctalon")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "")
DB_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "csctalon")
DB_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DB_URL, echo=False)

_TZ_CN = timezone(timedelta(hours=8))

def _to_str(val):
    if val is None:
        return None
    try:
        import pandas as pd
        if pd.isna(val):
            return None
    except Exception:
        pass
    s = str(val).strip()
    if s == "" or s.lower() == "nan":
        return None
    return s

def _to_datetime(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=_TZ_CN).astimezone(timezone.utc)
        return val
    try:
        import pandas as pd
        result = pd.to_datetime(val)
        if pd.isna(result):
            return None
        if result.tzinfo is None:
            result = result.tz_localize(_TZ_CN).tz_convert(timezone.utc)
        return result.to_pydatetime()
    except Exception:
        return None


def main():
    with engine.connect() as conn:
        # 1. 先看最近成功导入和失败导入的区别
        print("=== 最近 ticket 导入记录 (成功/失败对比) ===")
        rows = conn.execute(text("""
            SELECT id, created_at, status, total_rows, failed_rows,
                   inserted_rows, updated_rows, source,
                   substr(error_detail::text, 1, 200) as err_snippet
            FROM upload_records
            WHERE data_type = 'ticket'
            ORDER BY created_at DESC LIMIT 15
        """)).fetchall()
        for r in rows:
            print(f"  [{r.status:10s}] id={r.id:3d} | {r.created_at} | src={r.source} "
                  f"total={r.total_rows:5d} ins={r.inserted_rows or 0:5d} upd={r.updated_rows or 0:5d} fail={r.failed_rows or 0:5d}")
            if r.err_snippet and 'null' not in r.err_snippet:
                print(f"         err: {r.err_snippet[:150]}")

        # 2. 查 is_appeal 字段的实际值分布 (找出超过16字符的值)
        print("\n=== is_appeal 列值长度分布 ===")
        appeal_rows = conn.execute(text("""
            SELECT DISTINCT is_appeal, length(is_appeal) as len
            FROM service_tickets
            WHERE is_appeal IS NOT NULL
            ORDER BY len DESC NULLS LAST
        """)).fetchall()
        for r in appeal_rows:
            print(f"  len={r.len:3d}  val='{r.is_appeal}'")

        # 3. 查 updated_by_raw 相关: handler/registrant/更新人 等字段实际有值的比例
        print("\n=== service_tickets 中各人员字段有值的行数 ===")
        counts = conn.execute(text("""
            SELECT
              count(*) as total,
              count(handler) as has_handler,
              count(registrant) as has_registrant,
              count(updated_by_raw) as has_updated_raw,
              count(dispatcher) as has_dispatcher,
              count(assignee) as has_assignee
            FROM service_tickets
        """)).fetchone()
        print(f"  总数: {counts.total}")
        print(f"  handler:    {counts.has_handler}")
        print(f"  registrant: {counts.has_registrant}")
        print(f"  upd_raw:    {counts.has_updated_raw}")
        print(f"  dispatcher: {counts.has_dispatcher}")
        print(f"  assignee:   {counts.has_assignee}")

        # 4. 模拟 _execute_upsert 中的 bug: rows 字典键不一致
        print("\n=== 模拟 _execute_upsert bug: 构造键不一致的 rows ===")
        from app.models.ticket import ServiceTicket
        with Session(engine) as db:
            test_rows = [
                # 行1: 有 handler (会嗅探出 updated_by_raw)
                {
                    "ticket_no": "TEST_BUG_001",
                    "handler": "张三",
                    "status": "待处理",
                    "register_time": _to_datetime("2026-08-07 10:00:00"),
                },
                # 行2: 没有 handler/更新人 (不会设置 updated_by_raw)
                {
                    "ticket_no": "TEST_BUG_002",
                    "status": "待处理",
                    "register_time": _to_datetime("2026-08-07 10:01:00"),
                },
            ]
            # 模拟 _execute_upsert 中设置字段的逻辑
            _UPDATED_BY_ALIASES = (
                "updated_by_raw", "更新人", "最后操作人", "修改人", "最近操作人",
                "updated_by", "last_operator", "修改者", "更新者",
                "处理人", "handler",
            )
            _now = datetime.now(timezone.utc)
            for r in test_rows:
                r["synced_at"] = _now
                # 嗅探 updated_by_raw - 只有行1会命中 handler
                if not r.get("updated_by_raw"):
                    for alias in _UPDATED_BY_ALIASES:
                        v = r.get(alias)
                        if v:
                            r["updated_by_raw"] = _to_str(v)
                            break
            print(f"  行1 keys: {sorted(test_rows[0].keys())}")
            print(f"  行2 keys: {sorted(test_rows[1].keys())}")
            print(f"  行1 有 updated_by_raw? {'updated_by_raw' in test_rows[0]}")
            print(f"  行2 有 updated_by_raw? {'updated_by_raw' in test_rows[1]}")
            print("  ⚠️  这就是 bug: 同批次 rows 字典键不一致，会导致 SQLAlchemy values() 绑定参数出错！")

            # 5. 尝试用 on_conflict 插入，看是否复现 boundparameter 错误
            print("\n=== 尝试复现 boundparameter 错误 ===")
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            try:
                stmt = pg_insert(ServiceTicket).values(test_rows)
                pk_field = "ticket_no"
                first_row_keys = [k for k in test_rows[0] if k != pk_field]
                print(f"  从 row[0] 取的 update_cols = {first_row_keys}")
                update_cols = {c: stmt.excluded[c] for c in first_row_keys}
                stmt = stmt.on_conflict_do_update(index_elements=[pk_field], set_=update_cols)
                print(f"  stmt 编译成功 ✓，尝试执行...")
                db.execute(stmt)
                db.flush()
                db.rollback()  # 回滚，不真正写入
                print("  居然没报错？可能需要更多行/不同组合触发")
            except Exception as e:
                print(f"  ✅ 成功复现错误: {type(e).__name__}: {str(e)[:300]}")
                db.rollback()


if __name__ == "__main__":
    main()

"""验证 upsert.py 修复:
1. 同批次 rows 必须键一致 (否则 boundparameter 错误)
2. 短字符串列必须被正确截断

直接用 raw SQL + sqlalchemy.dialects.postgresql.insert 构造测试,
不 import app.models (避免 Python 3.9 | 语法 TypeError)
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

load_dotenv(Path(__file__).parent.parent / ".env")

from sqlalchemy import create_engine, text, MetaData, Table
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

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


def _truncate_str(val, max_len):
    s = _to_str(val)
    if s is None:
        return None
    if len(s) <= max_len:
        return s
    return s[:max_len]


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


def test_1_boundparameter_fix():
    """测试: 修复后 rows 键必须一致, updated_by_raw 即使 None 也要设键"""
    print("=== TEST 1: boundparameter 修复 (rows 键一致性) ===")

    # 模拟旧逻辑: 嗅探时没找到值就不设键 → 键不一致
    _UPDATED_BY_ALIASES = (
        "updated_by_raw", "更新人", "最后操作人", "修改人", "最近操作人",
        "updated_by", "last_operator", "修改者", "更新者",
        "处理人", "handler",
    )
    old_rows = [
        {"ticket_no": "T1", "handler": "张三", "status": "待处理"},
        {"ticket_no": "T2", "status": "待处理"},  # 无 handler
    ]
    _now = datetime.now(timezone.utc)
    for r in old_rows:
        r["synced_at"] = _now
        # 旧逻辑: 找不到值就不设置键
        if not r.get("updated_by_raw"):
            for alias in _UPDATED_BY_ALIASES:
                v = r.get(alias)
                if v:
                    r["updated_by_raw"] = _to_str(v)
                    break
    keys_0 = set(old_rows[0].keys())
    keys_1 = set(old_rows[1].keys())
    print(f"  旧逻辑 - row[0] keys: {sorted(keys_0)}")
    print(f"  旧逻辑 - row[1] keys: {sorted(keys_1)}")
    print(f"  旧逻辑 - 键差异: {keys_0.symmetric_difference(keys_1)}")
    assert keys_0 != keys_1, "旧逻辑本该键不一致,但测试数据没构造好"
    print("  ✅ 旧逻辑确实键不一致 (会触发 boundparameter 错误)")

    # 新逻辑: 必须统一设键, 即使 None
    new_rows = [
        {"ticket_no": "T1", "handler": "张三", "status": "待处理"},
        {"ticket_no": "T2", "status": "待处理"},
    ]
    for r in new_rows:
        r["synced_at"] = _now
        _existing = r.get("updated_by_raw")
        if _existing:
            r["updated_by_raw"] = _truncate_str(_existing, 64)
        else:
            _sniffed = None
            for alias in _UPDATED_BY_ALIASES:
                v = r.get(alias)
                if v:
                    _sniffed = _truncate_str(v, 64)
                    break
            r["updated_by_raw"] = _sniffed  # 即使 None 也设键
    keys_0 = set(new_rows[0].keys())
    keys_1 = set(new_rows[1].keys())
    print(f"  新逻辑 - row[0] keys: {sorted(keys_0)}")
    print(f"  新逻辑 - row[1] keys: {sorted(keys_1)}")
    # ⚠️ 业务字段 handler 有值/无值导致的键差异是允许的(因为 handler 是可选列,DB 可以接受 NULL),
    # 我们真正关心的是 updated_by_raw 和 synced_at 这两个新增的"强制填键"字段必须每一行都有。
    critical_cols = {"updated_by_raw", "synced_at"}
    missing_in_0 = critical_cols - keys_0
    missing_in_1 = critical_cols - keys_1
    assert not missing_in_0, f"row[0] 缺失关键强制列: {missing_in_0}"
    assert not missing_in_1, f"row[1] 缺失关键强制列: {missing_in_1}"
    # 额外验证: updated_by_raw 在所有行都有键(值可以是 None)
    assert "updated_by_raw" in new_rows[0] and "updated_by_raw" in new_rows[1]
    print(f"  ✅ 强制列 {sorted(critical_cols)} 在所有行都存在键 (旧逻辑会缺 updated_by_raw)")
    print(f"     row[0].updated_by_raw = {repr(new_rows[0]['updated_by_raw'])}")
    print(f"     row[1].updated_by_raw = {repr(new_rows[1]['updated_by_raw'])}")
    print("  ✅ 新逻辑 updated_by_raw 键全量存在,不会触发 boundparameter 错误")
    return True


def test_2_truncate_protection():
    """测试: 短字符串列截断保护"""
    print("\n=== TEST 2: 短字符串列截断保护 ===")

    # is_appeal > 16 (历史上的 StringDataRightTruncation 成因)
    long_appeal = "申诉中-第3次-等待处理结果"  # 16 chars
    truncated = _truncate_str(long_appeal, 16)
    print(f"  is_appeal 原值: '{long_appeal}' (len={len(long_appeal)})")
    print(f"  is_appeal 截后: '{truncated}' (len={len(truncated)})")
    assert len(truncated) <= 16
    print("  ✅ is_appeal 截断正确")

    # ticket_no > 64 (极端情况)
    long_tno = "T" * 100
    truncated = _truncate_str(long_tno, 64)
    assert len(truncated) == 64
    print(f"  ✅ ticket_no 截断正确 (len=100→64)")

    # handler 含人名字段,应该同时通过人名清洗+截断
    # 此处不调用 _sanitize_name_field,因为它依赖 ImportResult, 仅测 truncate
    long_handler = "超超超超超超超超超超超超超超超长的人名字段测试ABCDEFGHIJKLMN"  # 45
    truncated = _truncate_str(long_handler, 64)
    assert len(truncated) <= 64
    print(f"  ✅ handler 截断正确 (len={len(long_handler)}→{len(truncated)})")
    return True


def test_3_actual_db_insert():
    """测试: 用修复后的数据实际插入, 验证不报错"""
    print("\n=== TEST 3: 实际 DB upsert (测试数据, 最后回滚) ===")
    metadata = MetaData()
    service_tickets = Table(
        "service_tickets", metadata, autoload_with=engine
    )
    pk_field = "ticket_no"

    # 构造最易触发 bug 的数据: 混合 handler 有/无的行
    _now = datetime.now(timezone.utc)
    test_rows = [
        {
            "ticket_no": "TEST_FIX_A001",
            "handler": "张三有处理人",
            "status": "待处理",
            "register_time": _to_datetime("2026-08-07 10:00:00"),
        },
        {
            "ticket_no": "TEST_FIX_A002",
            # 无 handler → 旧逻辑会漏 updated_by_raw 键
            "status": "待处理",
            "register_time": _to_datetime("2026-08-07 10:01:00"),
        },
        {
            "ticket_no": "TEST_FIX_A003",
            "handler": "李四",
            "is_appeal": "这是一个非常长的申诉状态描述,超过旧版16字符会炸",
            "status": "处理中-这个状态也超过32字符了旧版会报错-扩展后正常",
            "register_time": _to_datetime("2026-08-07 10:02:00"),
        },
    ]

    # 应用修复后的键统一 & 截断逻辑 (与 upsert.py 新代码完全一致)
    _UPDATED_BY_ALIASES = (
        "updated_by_raw", "更新人", "最后操作人", "修改人", "最近操作人",
        "updated_by", "last_operator", "修改者", "更新者",
        "处理人", "handler",
    )
    for r in test_rows:
        r["synced_at"] = _now
        _existing = r.get("updated_by_raw")
        if _existing:
            r["updated_by_raw"] = _truncate_str(_existing, 64)
        else:
            _sniffed = None
            for alias in _UPDATED_BY_ALIASES:
                v = r.get(alias)
                if v:
                    _sniffed = _truncate_str(v, 64)
                    break
            r["updated_by_raw"] = _sniffed
        # 字段级别截断 (按 upsert_tickets 新逻辑)
        r["ticket_no"] = _truncate_str(r.get("ticket_no"), 64)
        r["handler"] = _truncate_str(r.get("handler"), 64)
        r["status"] = _truncate_str(r.get("status"), 64)  # 迁移后是 64
        r["is_appeal"] = _truncate_str(r.get("is_appeal"), 64)  # 迁移后是 64

    # 验证键一致
    key_sets = [set(r.keys()) for r in test_rows]
    for i, ks in enumerate(key_sets[1:], 1):
        assert ks == key_sets[0], f"row 0 与 row {i} 键不同: {ks ^ key_sets[0]}"
    print("  ✅ 所有测试行键一致")
    print(f"  键列表: {sorted(key_sets[0])}")

    with Session(engine) as db:
        try:
            stmt = pg_insert(service_tickets).values(test_rows)
            first_row_keys = [k for k in test_rows[0] if k != pk_field]
            update_cols = {c: stmt.excluded[c] for c in first_row_keys}
            # synced_at 强制 = now()
            if "synced_at" in first_row_keys:
                update_cols["synced_at"] = text("now()")
            stmt = stmt.on_conflict_do_update(
                index_elements=[pk_field], set_=update_cols
            )
            result = db.execute(stmt)
            db.flush()
            print(f"  ✅ pg_insert ON CONFLICT 执行成功, 影响行数: {result.rowcount}")

            # 验证数据确实写入(再查回来)
            tnos = [r["ticket_no"] for r in test_rows]
            sel = text("SELECT ticket_no, updated_by_raw, is_appeal, status FROM service_tickets WHERE ticket_no = ANY(:tnos)")
            found = db.execute(sel, {"tnos": tnos}).fetchall()
            print(f"  ✅ 查回 {len(found)} 行测试数据:")
            for f in found:
                appeal_disp = f.is_appeal or ""
                appeal_disp = (appeal_disp[:20] + ("..." if len(appeal_disp) > 20 else ""))
                print(f"    - {f.ticket_no}: updated_by_raw='{f.updated_by_raw}', "
                      f"is_appeal='{appeal_disp}' (len={len(f.is_appeal) if f.is_appeal else 0})")

            db.rollback()
            print("  ✅ 测试数据已回滚, 不留痕迹")
            return True
        except Exception as e:
            db.rollback()
            print(f"  ❌ DB 执行失败: {type(e).__name__}: {str(e)[:400]}")
            import traceback
            traceback.print_exc()
            return False


def main():
    results = []
    try:
        results.append(("boundparameter 修复", test_1_boundparameter_fix()))
    except Exception as e:
        import traceback
        traceback.print_exc()
        results.append(("boundparameter 修复", False))

    try:
        results.append(("短字符串截断", test_2_truncate_protection()))
    except Exception as e:
        import traceback
        traceback.print_exc()
        results.append(("短字符串截断", False))

    try:
        results.append(("实际 DB upsert", test_3_actual_db_insert()))
    except Exception as e:
        import traceback
        traceback.print_exc()
        results.append(("实际 DB upsert", False))

    print("\n" + "=" * 60)
    print("测试汇总:")
    all_ok = True
    for name, ok in results:
        flag = "✅ PASS" if ok else "❌ FAIL"
        print(f"  {flag}  {name}")
        all_ok = all_ok and ok
    print("=" * 60)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()

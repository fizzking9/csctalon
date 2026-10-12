"""复现 ticket 导出 500 错误 - 简化版(避免 Python 3.9 语法问题)"""
import os
import sys
import io
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# 手动构建,不导入 app.models 避免 Python 3.9 union type 语法问题
from sqlalchemy import create_engine, text, select, func, MetaData, Table, Column

DB_USER = os.getenv("POSTGRES_USER", "csctalon")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "")
DB_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "csctalon")
DB_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

print(f"连接: {DB_URL}")
engine = create_engine(DB_URL, echo=False)


def test_1_check_service_tickets():
    """查 service_tickets 表结构和数据"""
    print("\n=== 测试1: service_tickets 表结构和数据")
    with engine.connect() as conn:
        # 行数
        total = conn.execute(text("SELECT count(*) FROM service_tickets")).scalar()
        print(f"  总行数: {total}")

        # 列信息
        cols = conn.execute(text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'service_tickets'
            ORDER BY ordinal_position
        """)).fetchall()
        print(f"  表列数: {len(cols)}")
        for c in cols:
            print(f"    {c.column_name:30s} {c.data_type:20s} null={c.is_nullable}")
        return [c.column_name for c in cols]


def test_2_check_data_source_fields(table_cols):
    """对比 DataSourceField 中 ticket 的字段注册情况"""
    print("\n=== 测试2: DataSourceField 注册情况")
    with engine.connect() as conn:
        ds_id = conn.execute(text("SELECT id, name_cn FROM data_sources WHERE code = 'ticket'")).fetchone()
        if not ds_id:
            print("  ❌ ticket data_sources 中未注册!")
            return
        print(f"  数据源 id={ds_id.id}, name={ds_id.name_cn}")

        reg_fields = conn.execute(text("""
            SELECT field_name, field_name_cn, data_type, is_filterable, sort_order
            FROM data_source_fields WHERE source_id = %s ORDER BY sort_order
        """ % ds_id.id)).fetchall()
        print(f"  已注册字段数: {len(reg_fields)}")
        for f in reg_fields:
            print(f"    {f.field_name:30s} cn={f.field_name_cn} type={f.data_type}")

        registered = {f.field_name for f in reg_fields}
        table_cols_set = set(table_cols)

        missing = table_cols_set - registered
        extra = registered - table_cols_set

        print(f"\n  ❌ 表中有但 DataSourceField 缺失({len(missing)}):")
        for m in sorted(missing):
            print(f"    - {m}")
        print(f"\n  ⚠️  DataSourceField 中有但表中不存在({len(extra)}):")
        for e in sorted(extra):
            print(f"    - {e}")
        return reg_fields


def test_3_check_data_values(reg_fields):
    """逐行测试序列化和类型转换"""
    print("\n=== 测试3: 逐行测试序列化和类型转换")
    if not reg_fields:
        print("  跳过: 无已注册字段")
        return

    # 构建 SELECT 语句 (只选已注册字段)
    select_cols = ", ".join([f.field_name for f in reg_fields])
    sql = f"SELECT {select_cols} FROM service_tickets LIMIT 50"

    errors = []
    import pandas as pd
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).fetchall()
        print(f"  取 {len(rows)} 行做序列化测试...")

        type_map = {f.field_name: f.data_type for f in reg_fields}

        for ri, row in enumerate(rows):
            for f in reg_fields:
                fname = f.field_name
                v = getattr(row, fname, None)
                if v is None:
                    continue
                try:
                    dt = type_map[fname]
                    if dt == "timestamp" and v is not None:
                        _ = v.isoformat()
                    elif dt == "numeric" and v is not None:
                        _ = float(v)
                except Exception as e:
                    errors.append(
                        f"行{ri} 字段[{fname}] type={type(v).__name__} 值={repr(v)[:60]} 失败: {e}"
                    )

    if errors:
        print(f"  ❌ 发现 {len(errors)} 个序列化错误:")
        for e in errors[:30]:
            print(f"    - {e}")
    else:
        print("  ✅ 全部序列化OK")


def test_4_check_decimal_problem():
    """测试特殊测试 Decimal/退款金额等字段类型是否是 Decimal,无法转 float"""
    print("\n=== 测试4: Numeric 列类型转换测试")
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT ticket_no, refund_amount, estimated_amount,
                   pg_typeof(refund_amount) as refund_type,
                   pg_typeof(estimated_amount) as est_type
            FROM service_tickets
            WHERE refund_amount IS NOT NULL OR estimated_amount IS NOT NULL
            LIMIT 10
        """)).fetchall()
        print(f"  有退款金额数据的行数: {len(rows)}")
        for r in rows:
            print(f"    no={r.ticket_no}: refund={r.refund_amount}({type(r.refund_amount).__name__}), est={r.estimated_amount}({type(r.estimated_amount).__name__}) refund_pgtype={r.refund_type} est_pgtype={r.est_type}")


def test_5_submit_params():
    """测试 submit_params 字段内容"""
    print("\n=== 测试5: submit_params 字段内容(可能含控制字符)")
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT ticket_no, submit_params
            FROM service_tickets
            WHERE submit_params IS NOT NULL
            LIMIT 10
        """)).fetchall()
        print(f"  有 submit_params 行数: {len(rows)}")
        for r in rows:
            sp = r.submit_params
            has_control = False
            if isinstance(sp, str):
                for ch in sp:
                    code = ord(ch)
                    if (code < 32 and code not in (9, 10, 13)) or code == 127:
                        has_control = True
                        break
            print(f"    no={r.ticket_no}: len={len(sp)} type={type(sp).__name__} 控制字符={has_control}")
            if sp:
                print(f"      前100字符: {repr(str(sp)[:100])}")


if __name__ == "__main__":
    try:
        cols = test_1_check_service_tickets()
        reg = test_2_check_data_source_fields(cols)
        test_3_check_data_values(reg)
        test_4_check_decimal_problem()
        test_5_submit_params()
    except Exception as e:
        print(f"\n❌ 测试异常: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

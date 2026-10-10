"""测试全量导出 28224 行 ticket 是否会 500"""
import os
import io
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from sqlalchemy import create_engine, text

DB_USER = os.getenv("POSTGRES_USER", "csctalon")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "")
DB_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "csctalon")
DB_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

print(f"连接: {DB_URL}")
engine = create_engine(DB_URL, echo=False)


def simulate_export():
    """完整模拟 data_sources.export_records 的逻辑"""
    import pandas as pd

    print("\n=== 全量导出模拟 ===")

    with engine.connect() as conn:
        # 1. 拿 DataSourceField 注册信息
        ds_id = conn.execute(text("SELECT id FROM data_sources WHERE code='ticket'")).scalar()
        fields = conn.execute(text(f"""
            SELECT field_name, field_name_cn, data_type
            FROM data_source_fields WHERE source_id={ds_id} ORDER BY sort_order
        """)).fetchall()
        field_dicts = [
            {"field_name": f.field_name, "field_name_cn": f.field_name_cn, "data_type": f.data_type}
            for f in fields
        ]
        print(f"已注册字段: {len(field_dicts)} 个")

        # 2. 查询总行数（和 _build_browse_query 中一样先查count）
        total = conn.execute(text("SELECT count(*) FROM service_tickets")).scalar()
        print(f"总行数: {total}, EXPORT_MAX_ROWS=200000, 不会超限")

        # 3. 取所有行
        select_cols = ", ".join([f["field_name"] for f in field_dicts])
        sql = f"SELECT {select_cols} FROM service_tickets"
        print(f"执行 SQL 获取全部数据...")
        rows = conn.execute(text(sql)).fetchall()
        print(f"实际取回: {len(rows)} 行")

        # 4. 逐行序列化(精确模拟 export_records 的转换逻辑)
        print("开始序列化转换...")
        records = []
        errors = []
        type_map = {f["field_name"]: f["data_type"] for f in field_dicts}

        for ri, row in enumerate(rows):
            rec = {}
            for fd in field_dicts:
                fname = fd["field_name"]
                try:
                    v = getattr(row, fname, None)
                    if fd["data_type"] == "timestamp" and v is not None:
                        rec[fname] = v.isoformat()
                    elif fd["data_type"] == "numeric" and v is not None:
                        try:
                            rec[fname] = float(v)
                        except Exception as e:
                            errors.append(f"R{ri} [{fname}] numeric转float: {e} val={repr(v)[:60]} type={type(v).__name__}")
                            rec[fname] = None
                    else:
                        rec[fname] = v
                except Exception as e:
                    errors.append(f"R{ri} [{fname}] 访问: {e}")
                    rec[fname] = None
            records.append(rec)

        if errors:
            print(f"❌ 序列化错误: {len(errors)} 个")
            for e in errors[:50]:
                print(f"  - {e}")
        else:
            print("✅ 序列化全部OK")

        # 5. 构建 DataFrame
        print("构建 DataFrame...")
        columns = [fd["field_name"] for fd in field_dicts]
        headers = [fd["field_name_cn"] or fd["field_name"] for fd in field_dicts]
        df = pd.DataFrame(records, columns=columns) if columns else pd.DataFrame()
        if not df.empty:
            df.columns = headers
        print(f"DataFrame 形状: {df.shape}")
        print(f"DataFrame 列:\n{df.dtypes}")

        # 6. 先检查单元格内容(在 clean_df_for_excel 之前)
        print("\n检查 object/string 列的控制字符...")
        bad_cells = 0
        for col in df.columns:
            series = df[col]
            if series.dtype == object or pd.api.types.is_string_dtype(series):
                n_bad = 0
                for idx, val in series.items():
                    if isinstance(val, str):
                        for ch in val:
                            code = ord(ch)
                            if (code < 32 and code not in (9, 10, 13)) or code == 127:
                                n_bad += 1
                                bad_cells += 1
                                if bad_cells <= 5:
                                    print(f"  ⚠️  行{idx} 列[{col}] 含控制字符 code={code}: repr={repr(val)[:80]}")
                                break
                if n_bad:
                    print(f"  列 [{col}]: 含控制字符的单元格 {n_bad} 个")
        print(f"控制字符单元格总数: {bad_cells}")

        # 7. 写 Excel (使用 clean_df_for_excel)
        print("\n开始写 Excel (openpyxl)...")
        # 动态 import
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "excel_utils",
            Path(__file__).parent.parent / "app" / "utils" / "excel.py"
        )
        excel_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(excel_mod)

        try:
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                excel_mod.clean_df_for_excel(df).to_excel(writer, index=False, sheet_name="数据")
            buf.seek(0)
            data = buf.read()
            size = len(data)
            print(f"✅ Excel 写入成功! size={size/1024:.1f}KB, rows={len(rows)}")
        except Exception as e:
            print(f"❌ Excel 写入失败: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    try:
        simulate_export()
    except Exception as e:
        print(f"\n❌ 脚本异常: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

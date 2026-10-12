"""复现 ticket 导出 500 错误的测试脚本"""
import os
import sys
from pathlib import Path

# 确保后端目录在路径中
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from app.core.database import Base
from app.models.ticket import ServiceTicket
from app.models.data_source import DataSource, DataSourceField

# 构建数据库连接
DB_USER = os.getenv("POSTGRES_USER", "csctalon")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "")
DB_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "csctalon")

DB_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
print(f"连接数据库: {DB_URL}")
engine = create_engine(DB_URL, echo=False)

def test_1_check_ticket_table():
    """检查 service_tickets 表结构和数据"""
    print("\n=== 测试1: 检查 service_tickets 表和数据 ===")
    with Session(engine) as db:
        total = db.scalar(select(func.count()).select_from(ServiceTicket)) or 0
        print(f"service_tickets 总行数: {total}")

        # 检查列
        cols = [c.key for c in ServiceTicket.__table__.columns]
        print(f"模型列数: {len(cols)}, 列名: {cols}")

        # 拿一行数据看看
        row = db.scalar(select(ServiceTicket).limit(1))
        if row:
            print(f"样例行 - ticket_no: {row.ticket_no}")
            for c in cols:
                v = getattr(row, c, None)
                print(f"  {c}: {type(v).__name__} = {repr(v)[:80]}")

def test_2_check_data_source_fields():
    """检查 data_source_fields 中 ticket 的字段注册情况"""
    print("\n=== 测试2: 检查 DataSourceField 注册情况 ===")
    with Session(engine) as db:
        ds = db.scalar(select(DataSource).where(DataSource.code == "ticket"))
        if not ds:
            print("❌ ticket 数据源未注册!")
            return
        print(f"数据源: {ds.code} / {ds.name_cn} / id={ds.id}")

        fields = list(db.scalars(
            select(DataSourceField)
            .where(DataSourceField.source_id == ds.id)
            .order_by(DataSourceField.sort_order)
        ))
        print(f"已注册字段数: {len(fields)}")

        model_cols = {c.key for c in ServiceTicket.__table__.columns}
        registered_cols = {f.field_name for f in fields}

        missing = model_cols - registered_cols
        extra = registered_cols - model_cols

        print(f"\n模型中有但 DataSourceField 缺失的列({len(missing)}):")
        for m in sorted(missing):
            print(f"  ❌ {m}")

        print(f"\nDataSourceField 中有但模型没有的列({len(extra)}):")
        for e in sorted(extra):
            print(f"  ⚠️  {e}")

        return [f.field_name for f in fields]

def test_3_simulate_export_logic():
    """模拟 data_sources.export_records 的导出流程"""
    print("\n=== 测试3: 模拟导出逻辑 (逐行测试序列化) ===")
    with Session(engine) as db:
        ds = db.scalar(select(DataSource).where(DataSource.code == "ticket"))
        if not ds:
            print("❌ 数据源不存在")
            return

        field_defs = list(db.scalars(
            select(DataSourceField).where(DataSourceField.source_id == ds.id).order_by(DataSourceField.sort_order)
        ))
        field_dicts = [
            {"field_name": f.field_name, "field_name_cn": f.field_name_cn,
             "data_type": f.data_type, "semantic_role": f.semantic_role,
             "is_filterable": f.is_filterable}
            for f in field_defs
        ]
        print(f"使用 {len(field_dicts)} 个已注册字段做导出")

        # 查询
        rows = list(db.scalars(select(ServiceTicket).limit(50)))
        print(f"测试 {len(rows)} 行数据序列化...")

        errors = []
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
                            errors.append(f"行{ri} 字段[{fname}] numeric转float失败: {e}, 原始值={type(v).__name__}:{repr(v)[:60]}")
                            rec[fname] = None
                    else:
                        rec[fname] = v
                except Exception as e:
                    errors.append(f"行{ri} 字段[{fname}] 访问失败: {e}")

        if errors:
            print(f"❌ 发现 {len(errors)} 个序列化错误:")
            for e in errors[:20]:
                print(f"  - {e}")
        else:
            print("✅ 全部50行序列化无错误")

def test_4_test_excel_export():
    """测试写 Excel 会不会报错"""
    print("\n=== 测试4: 测试 Excel 写入 (非法字符) ===")
    import io
    import pandas as pd
    from app.utils.excel import clean_df_for_excel

    with Session(engine) as db:
        ds = db.scalar(select(DataSource).where(DataSource.code == "ticket"))
        field_defs = list(db.scalars(
            select(DataSourceField).where(DataSourceField.source_id == ds.id).order_by(DataSourceField.sort_order)
        ))
        field_dicts = [
            {"field_name": f.field_name, "field_name_cn": f.field_name_cn, "data_type": f.data_type}
            for f in field_defs
        ]
        rows = list(db.scalars(select(ServiceTicket).limit(200)))
        print(f"取 {len(rows)} 行构建 DataFrame...")

        records = []
        for row in rows:
            rec = {}
            for fd in field_dicts:
                fname = fd["field_name"]
                v = getattr(row, fname, None)
                if fd["data_type"] == "timestamp" and v is not None:
                    rec[fname] = v.isoformat()
                elif fd["data_type"] == "numeric" and v is not None:
                    try:
                        rec[fname] = float(v)
                    except:
                        rec[fname] = None
                else:
                    rec[fname] = v
            records.append(rec)

        columns = [fd["field_name"] for fd in field_dicts]
        headers = [fd["field_name_cn"] or fd["field_name"] for fd in field_dicts]
        df = pd.DataFrame(records, columns=columns) if columns else pd.DataFrame()
        if not df.empty:
            df.columns = headers

        print(f"DataFrame 尺寸: {df.shape}")
        print(f"DataFrame 列类型:\n{df.dtypes}")

        # 写 Excel
        try:
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                clean_df_for_excel(df).to_excel(writer, index=False, sheet_name="数据")
            buf.seek(0)
            size = len(buf.read())
            print(f"✅ Excel 写入成功, 大小={size} 字节")
        except Exception as e:
            print(f"❌ Excel 写入失败: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    try:
        test_1_check_ticket_table()
        test_2_check_data_source_fields()
        test_3_simulate_export_logic()
        test_4_test_excel_export()
    except Exception as e:
        print(f"\n❌ 测试脚本异常: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

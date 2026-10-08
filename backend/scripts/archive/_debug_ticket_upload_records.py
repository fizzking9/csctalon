"""查询 upload_records 表，找服务请求管理(ticket)的导入失败记录"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import json

load_dotenv(Path(__file__).parent.parent / ".env")

from sqlalchemy import create_engine, text

DB_USER = os.getenv("POSTGRES_USER", "csctalon")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "")
DB_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "csctalon")
DB_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DB_URL, echo=False)


def main():
    with engine.connect() as conn:
        # 1. 先查有多少上传记录，尤其是 ticket 类型的
        total = conn.execute(text("SELECT count(*) FROM upload_records")).scalar()
        print(f"upload_records 总数: {total}")

        by_type = conn.execute(text("""
            SELECT data_type, status, count(*), max(created_at) as last
            FROM upload_records GROUP BY data_type, status ORDER BY data_type, status
        """)).fetchall()
        print("\n按 data_type/state 统计:")
        for r in by_type:
            print(f"  {r.data_type:20s} state={r.status:10s} count={r.count:5d} last={r.last}")

        # 2. 重点: ticket 类型的 失败 记录
        print("\n=== 服务请求管理(ticket) 失败记录 ===")
        failed = conn.execute(text("""
            SELECT id, created_at, filename, status, total_rows, failed_rows,
                   error_detail, file_size, uploaded_by, source
            FROM upload_records
            WHERE data_type = 'ticket' AND status IN ('failed', 'error') OR (failed_rows > 0 AND data_type='ticket')
            ORDER BY created_at DESC LIMIT 20
        """)).fetchall()
        if not failed:
            # 放宽条件: 只看最近 ticket 记录
            print("无 failed 状态记录，展示最近 20 条 ticket 上传记录:")
            failed = conn.execute(text("""
                SELECT id, created_at, filename, status, total_rows, inserted_rows, updated_rows, failed_rows,
                       error_detail, file_size, uploaded_by, source
                FROM upload_records
                WHERE data_type = 'ticket'
                ORDER BY created_at DESC LIMIT 20
            """)).fetchall()

        for r in failed:
            print(f"\n--- id={r.id} | {r.created_at} ---")
            print(f"  文件名: {r.filename}")
            print(f"  状态: {r.status} | 文件大小: {r.file_size} bytes")
            print(f"  操作人: {r.uploaded_by} | 来源: {r.source}")
            rows_info = f"total={r.total_rows}"
            if hasattr(r, 'inserted_rows'):
                rows_info += f" inserted={r.inserted_rows} updated={r.updated_rows}"
            rows_info += f" failed={r.failed_rows}"
            print(f"  行数: {rows_info}")
            if r.error_detail:
                print(f"  错误详情: ")
                try:
                    detail = r.error_detail if isinstance(r.error_detail, dict) else json.loads(r.error_detail)
                    if "errors" in detail:
                        for err in detail["errors"][:20]:
                            print(f"    - {err}")
                    if "error" in detail:
                        print(f"    错误: {detail['error']}")
                except Exception as e:
                    print(f"    (无法解析JSON) {repr(r.error_detail)[:500]}")


if __name__ == "__main__":
    main()

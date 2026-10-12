"""一次性验证: 用 01样本数据/315投诉处理登记表.xlsx 模拟 workbuddy 推送"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from app.core.database import SessionLocal
from app.services.complaint_sync import sync_complaints_from_payload

XLSX = "/Users/tanzk/dev/csctalon/01样本数据/315投诉处理登记表.xlsx"

xl = pd.ExcelFile(XLSX, engine="calamine")


def sheet_to_payload(name: str) -> list[dict]:
    df = xl.parse(name, dtype=str)
    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
    return df.to_dict("records")


payload = {
    "source_file": "315投诉处理登记表.xlsx",
    "external_complaints": sheet_to_payload("监督局-315投诉"),
    "card_complaints": sheet_to_payload("卡商群投诉"),
}

db = SessionLocal()
try:
    stats = sync_complaints_from_payload(db, payload)
    import json

    print(json.dumps({k: (v if k != "errors" else "见DB") for k, v in stats.items() if k != "errors"}, ensure_ascii=False, indent=2))
except Exception as e:
    import traceback

    traceback.print_exc()
    print("LAST_ERROR:", repr(e)[:3000])
finally:
    db.close()

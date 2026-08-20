"""验证投诉 API: summary 补充统计 + supplementary 明细 + detail 关联合并
注意: 直接调用路由函数时, Query(None) 默认值是 FieldInfo, 需显式传参。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.v1.complaints import complaint_detail, complaint_summary, complaint_supplementary
from app.core.database import SessionLocal

db = SessionLocal()
try:
    s = complaint_summary(db, None)
    suppl = s.data.get("supplementary", {})
    print("SUMMARY supplementary:", suppl)

    su = complaint_supplementary(db, None, start=None, end=None, source_type=None, matched=None, limit=5)
    print(f"\nSUPPLEMENTARY total={su.data['total']}")
    for it in su.data["items"][:5]:
        print("  ", it["source"], it["complaint_date"], it.get("complainant"), it.get("device_sn"), "关联:", it.get("matched_ticket_no"))

    su2 = complaint_supplementary(db, None, start=None, end=None, source_type="external", matched=True, limit=3)
    print("\nSUPPLEMENTARY external+已关联 total:", su2.data["total"])
    for it in su2.data["items"][:3]:
        print("  ", it["complaint_date"], it.get("complainant"), "SN:", it.get("device_sn"), "工单:", it.get("matched_ticket_no"))

    d = complaint_detail(db, None, start=None, end=None, source_type="外部投诉", limit=5, include_supplementary=True)
    print("\nDETAIL external total:", d.data["total"])
    for it in d.data["items"][:5]:
        sup = it.get("supplementary", [])
        print("  ", it["no"], "SN:", it["device_sn"], "补充:", len(sup),
              "首条:", (sup[0].get("source"), sup[0].get("complaint_date")) if sup else None)
finally:
    db.close()

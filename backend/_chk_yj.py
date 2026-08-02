import sys; sys.path.insert(0, '/app')
from app.core.database import SessionLocal
from app.services.analytics import today_staff_efficiency
db = SessionLocal()
rows = today_staff_efficiency(db, date='2026-08-01', topn=100)
for r in rows:
    if r['name'] == '叶珍凤':
        print(r)
        break

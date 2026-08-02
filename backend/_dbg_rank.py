from app.core.database import SessionLocal
from app.services.analytics import today_staff_efficiency
db = SessionLocal()
rows = today_staff_efficiency(db, date='2026-08-01', topn=40)
print(f"总 {len(rows)} 条")
print("\n前10条 rank/is_virtual/name/HR大类:")
for r in rows[:10]:
    print(f"  rank={r.get('rank')!r:<6} is_virt={r.get('is_virtual',False)!r:<6} name={r['name']:<7} group={r['group']:<8} HRcat={(r.get('hr_category') or '-'):<6}")
print("\n后15条:")
for r in rows[-15:]:
    print(f"  rank={r.get('rank')!r:<6} is_virt={r.get('is_virtual',False)!r:<6} name={r['name']:<7} group={r['group']:<8} HRcat={(r.get('hr_category') or '-'):<6}")
print("\n所有 is_virtual=True 的: ",[r['name'] for r in rows if r.get('is_virtual')])
print("所有 rank==None 的: ", [r['name'] for r in rows if r.get('rank') is None])

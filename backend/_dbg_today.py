from app.core.database import SessionLocal
from app.services.analytics import today_staff_efficiency
db = SessionLocal()
rows = today_staff_efficiency(db, date="2026-08-02", topn=20)
print(f"Total: {len(rows)}")
print(f"\nTop 12: rank / is_virtual / name / dept / grp / pos / HRcat / mgmt / work_score / eff_score")
for r in rows[:12]:
    print(f"  rank={r.get('rank')!r:<6} is_v={r.get('is_virtual',None)!r:<6} name={r['name']:<7} "
          f"dept={r.get('department')!r:<15} grp={r['group']!r:<9} pos={r.get('position')!r:<7} "
          f"HRcat={(r.get('hr_category') or '-'):<6} mgmt={r.get('is_management')} "
          f"ws={r['work_score']:<8.2f} es={r['eff_score']:<8.2f} "
          f"sess(咨询/套餐/退款/投诉)=({r['sp_zixun']},{r['sp_taocan']},{r['sp_tuikuan']},{r['sp_tousu']}) "
          f"ppl=(%d,%d,%d,%d)"%(r['pp_zixun'],r['pp_taocan'],r['pp_tuikuan'],r['pp_tousu']))
# 第一条 breakdown 详情
if rows:
    r = rows[0]
    print(f"\nbreakdown 第一名 {r['name']}:")
    for c,v in r.get('breakdown',{}).items():
        print(f"  {c}: ",v)

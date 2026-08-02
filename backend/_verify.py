from app.core.database import SessionLocal
from app.services.analytics import today_staff_efficiency
db = SessionLocal()
r = today_staff_efficiency(db, date="2026-08-01", topn=30)
print(f"Total records: {len(r)}")
print("Top25 (工作量分rank / name / pos / group / work_score / eff_score / HRcat / mgmt / virtual?):")
for d in r[:25]:
    flag = "[VIRTUAL]" if d.get("is_virtual") else ""
    rk = d.get("rank")
    print(f"  #{str(rk or '-'):>3} {d['name']:<6} {(d.get('position') or '-'):<4} {d['group']:<8} work={d['work_score']:<8.2f} eff={d['eff_score']:<8.2f} HRcat={(d.get('hr_category') or '-'):<6} mgmt={d.get('is_management')} {flag}")
print("\n==典型案例 breakdown==")
for nm in ("何琴","田兴月","覃克泳","王霞","吴晓龙","董梦玲","荣海伦","温佳琳","许中"):
    d = next((x for x in r if x["name"]==nm), None)
    if not d: continue
    print(f"\n[{nm}] rank={d.get('rank')} work={d['work_score']} eff={d['eff_score']} HR_sp={(d.get('current_specialty') or '-')} HRcat={d.get('hr_category')} mgmt={d.get('is_management')}")
    for c,v in d.get("breakdown",{}).items():
        own_mark = "OWN " if v["is_own"] else "XFER"
        print(f"   {c:<6}: sessions={v['sessions']:<4} people={v['people']:<4} w={v['weight']} coef={v['coef']:<5} [{own_mark}] work={v['work_contrib']:<7} eff={v['eff_contrib']:<7}")

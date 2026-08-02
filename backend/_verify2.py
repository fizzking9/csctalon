from app.core.database import SessionLocal
from app.services.analytics import today_staff_efficiency
db = SessionLocal()
r = today_staff_efficiency(db, date='2026-08-01', topn=50)
# 投诉专项组 人: 苏紫依 / 刘景山 / 还有吗
for nm in ["苏紫依","刘景山","吴晓龙","叶珍凤","董梦玲","朱海洋","王霞","何琴"]:
    d = next((x for x in r if x["name"]==nm), None)
    if not d: continue
    print(f"\n[{nm}] rank={d.get('rank')} work={d['work_score']} eff={d['eff_score']} HR={d.get('hr_category')} is_management={d.get('is_management')}")
    for c,v in d.get("breakdown",{}).items():
        own_mark = " [本职-豁免]" if v["is_own"] else " [转接×0.2]"
        print(f"   {c:<6}: sessions={v['sessions']:<4} people={v['people']:<4} w={v['weight']} coef={v['coef']:<5}{own_mark} work={v['work_contrib']:<7} eff={v['eff_contrib']:<7}")

import sys; sys.path.insert(0, '/app')
import datetime as _dt
from app.core.database import SessionLocal
from app.models.person import Person
from app.models.staff_assignment import StaffAssignment
from sqlalchemy import select

db = SessionLocal()

TODAY = _dt.date(2026, 8, 2)
NOW   = _dt.datetime.now()

# 现在 Person 4 个组长 specialty 都 = '组长', 改为专项名 (按截图分组的主专项属性)
# 董梦玲组 = 咨询接待为主, 吴晓龙组 = 咨询(兼投诉处理多), 叶珍凤组 = 咨询+退款, 朱海洋组 = 套餐+退款
UPDATE = [
    ("董梦玲", "咨询接待组主管",  "董梦玲组"),  # 7个咨询 + 组长
    ("吴晓龙", "投诉接待+咨询",    "吴晓龙组"),  # 自己做投诉多
    ("叶珍凤", "咨询接待+退款挽回","叶珍凤组"),
    ("朱海洋", "套餐专项+退款挽回","朱海洋组"),
]
sa_created = 0; sa_termed = 0; per_up = 0
for nm, newsp, _ in UPDATE:
    p = db.execute(select(Person).where(Person.display_name == nm)).scalars().first()
    if not p:
        print(f"❌ 找不到 {nm}"); continue
    oldsp = p.specialty; oldgrp = p.group_name
    p.specialty = newsp
    p.updated_at = NOW
    per_up += 1
    print(f"✅ {nm}: specialty {oldsp!r} -> {newsp!r}")
    # SCD2
    old_sa = db.execute(select(StaffAssignment).where(StaffAssignment.person_id == p.id, StaffAssignment.is_current == True)).scalars().first()
    if old_sa:
        old_sa.is_current = False; old_sa.effective_to = TODAY-_dt.timedelta(days=1); old_sa.updated_at=NOW
        old_sa.change_reason='组长specialty更新为专项名 20260802'
        sa_termed += 1
    new_sa = StaffAssignment(
        person_id = p.id, position=p.position, group_name=p.group_name, specialty=newsp,
        effective_from=TODAY, effective_to=_dt.date(9999,12,31), is_current=True,
        source='manual', change_reason='组长specialty更新为专项名 20260802',
    )
    new_sa.created_at = NOW; new_sa.updated_at = NOW
    db.add(new_sa); sa_created += 1

db.commit()
print(f"\n✅ Person 更新 {per_up} 条; SA 终止 {sa_termed} 条 新建 {sa_created} 条")

# 校验
from app.services.analytics import today_staff_efficiency
ts = today_staff_efficiency(db, date='2026-08-01', topn=30)
print("\n=== 今日人效Top 4组长 专项显示验证 ===")
for r in ts:
    if r['name'] in ('董梦玲','吴晓龙','叶珍凤','朱海洋'):
        top2 = sorted([(r['sp_zixun'],'咨询接待'),(r['sp_taocan'],'套餐专项'),(r['sp_tuikuan'],'退款挽回'),(r['sp_tousu'],'投诉专员')], reverse=True)[:2]
        actual = '+'.join(n for v,n in top2 if v>0) or '—'
        print(f"  {r['name']:<6} pos={r['position']} grp={r['group']:<6} HR预设专项=[{r['current_specialty'] or '-':<14}] 实际行为专项Top2 = {actual}")

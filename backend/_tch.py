import sys; sys.path.insert(0, '/app')
import datetime as _dt
from app.core.database import SessionLocal
from app.models.person import Person
from app.models.staff_assignment import StaffAssignment
from app.services.analytics import today_staff_efficiency
from sqlalchemy import select

db = SessionLocal()

# 1. 查唐彩慧 Person
p = db.execute(select(Person).where(Person.display_name == '唐彩慧')).scalars().first()
print(f"唐彩慧 Person id={p.id} dep={p.department} 原 group=[{p.group_name or '空'}] pos=[{p.position or '空'}] sp=[{p.specialty or '空'}]")

TODAY = _dt.date(2026, 8, 2)
NOW   = _dt.datetime.now()

# 2. UPDATE Person
p.group_name = '朱海洋组'
p.position = p.position or '客服'
p.specialty = '退款挽回'  # 和卢宁/黄亚康/徐晓萱 保持一致
p.updated_at = NOW

# 3. StaffAssignment SCD2
old_sa = db.execute(select(StaffAssignment).where(
    StaffAssignment.person_id == p.id, StaffAssignment.is_current == True,
)).scalars().first()
if old_sa:
    old_sa.is_current = False
    old_sa.effective_to = TODAY - _dt.timedelta(days=1)
    old_sa.updated_at = NOW
    old_sa.change_reason = '唐彩慧 20260802 归入朱海洋组退款挽回'
    print(f"✅ 旧SA id={old_sa.id} 已终止 effective_to={old_sa.effective_to}")

new_sa = StaffAssignment(
    person_id = p.id,
    position = p.position,
    group_name = '朱海洋组',
    specialty = '退款挽回',
    effective_from = TODAY,
    effective_to = _dt.date(9999,12,31),
    is_current = True,
    source = 'manual',
    change_reason = '唐彩慧 20260802 归入朱海洋组退款挽回',
)
new_sa.created_at = NOW
new_sa.updated_at = NOW
db.add(new_sa)

db.commit()
print("✅ Person+SCD2 提交完毕")

# 4. 验证: 8/1 人效 Top20 唐彩慧 group=朱海洋组
ts = today_staff_efficiency(db, date='2026-08-01', topn=30)
for i, r in enumerate(ts, 1):
    if r['name'] == '唐彩慧':
        print(f"\n✅ #16 唐彩慧 验证结果: pos={r['position']} group=[{r['group']}] 预期=朱海洋组")
        print(f"     工作分={r['work_score']} 退款挽回={r['sp_tuikuan']} 符合预期 ✅" if r['group']=='朱海洋组' else f"     ❌ 分组错误! group={r['group']}")
        break

# 再扫一遍 Top30: 是否还有未分组(除用户自助)
print("\n=== 8/1 Top30 最终检查: group全部填好? ===")
miss = []
for i, r in enumerate(ts[:30], 1):
    g = r['group']
    if (not g) or g=='未分组':
        if r['name'] != '用户自助':
            miss.append((i, r['name'], r['position'], r['group']))
            print(f"  ⚠️ #{i} [{r['name']:<8}] group=[{g or '空!'}]")
if not miss:
    print("  ✅ Top30 中除了系统用户自助 所有人员都已确定分组!")

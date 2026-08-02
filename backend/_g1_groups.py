import sys; sys.path.insert(0, '/app')
from app.core.database import SessionLocal
from app.models.person import Person, PersonAlias
from app.models.staff_assignment import StaffAssignment
from sqlalchemy import select, func, and_

db = SessionLocal()

# G1. Person表在职人员 按 department+group+position 聚合
print("="*90)
print("G1. Person表(在职 is_active=True 非系统非离职 非用户自助) 按 部门→组→岗位 聚合")
print("="*90)

persons = db.execute(select(Person).where(
    Person.is_active == True,
    Person.employment_status.in_(['active','probation','regular']),
    Person.role != 'user_self_service',  # 排除用户自助桶
    Person.display_name.notin_(['系统自动','用户自助']),
)).scalars().all()
print(f"在职真人 total={len(persons)}")

from collections import defaultdict
by_dep_grp = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
grp_count = defaultdict(lambda: {'客服':0,'组长':0,'主管':0,'其他':0,'未标':0})
dep_count = defaultdict(int)
for p in persons:
    dep = p.department or '未设置部门'
    grp = p.group_name or '未分组'
    dep_count[dep] += 1
    pos_map = {'客服':'客服','组长':'组长','主管':'主管'}
    pos_key = pos_map.get(p.position or '', '未标') if p.position else '未标'
    if not p.position:
        grp_count[(dep,grp)]['未标'] += 1
    elif pos_key == '未标':
        grp_count[(dep,grp)]['其他'] += 1
    else:
        grp_count[(dep,grp)][pos_key] += 1
    by_dep_grp[dep][grp][pos_key].append((p.display_name, p.position or '-', p.specialty or '-', p.id))

for dep in sorted(by_dep_grp.keys()):
    print(f"\n【部门】{dep} ({dep_count[dep]}人)")
    for grp in sorted(by_dep_grp[dep].keys()):
        pos = grp_count[(dep,grp)]
        tags = []
        if pos['主管']>0: tags.append(f"主管×{pos['主管']}")
        if pos['组长']>0: tags.append(f"组长×{pos['组长']}")
        if pos['客服']>0: tags.append(f"客服×{pos['客服']}")
        if pos['未标']>0: tags.append(f"岗位未标×{pos['未标']}")
        if pos['其他']>0: tags.append(f"其他岗×{pos['其他']}")
        print(f"  └─ 组[{grp}] {sum(pos.values()):<3}人  {', '.join(tags)}")
        # 列出该组的 主管/组长优先 然后组员
        for pk in ['主管','组长','客服','未标','其他']:
            ppl = sorted(by_dep_grp[dep][grp][pk], key=lambda x:x[0])
            for nm, ps, sp, pid in ppl:
                sp_str = f"[专项:{sp}]" if sp and sp!='-' else ''
                print(f"       ★{pk[:1]}  {nm:<10} (pos={ps or '未标'})  id={pid:<4} {sp_str}")

# G2. StaffAssignment SCD 最新任职 vs Person 镜像 差异
print("\n\n"+"="*90)
print("G2. StaffAssignment.is_current 最新任职(共多少条) vs Person 镜像 一致性检查 (只看person在在职内)")
print("="*90)
sas = db.execute(select(StaffAssignment).where(
    StaffAssignment.is_current == True,
)).scalars().all()
print(f"StaffAssignment current = {len(sas)} 条")

diff_position, diff_specialty, diff_group = 0, 0, 0
for sa in sas:
    p = db.get(Person, sa.person_id)
    if not p or not p.is_active: continue
    # 比较 position
    if (sa.position or None) != (p.position or None):
        diff_position += 1
        print(f"  ⚠️ position不一致 person[{p.display_name}] Person={p.position!r} vs SA={sa.position!r}")
    if (sa.specialty or None) != (p.specialty or None):
        diff_specialty += 1
        print(f"  ⚠️ specialty不一致 person[{p.display_name}] Person={p.specialty!r} vs SA={sa.specialty!r}")
    if (sa.group_name or None) != (p.group_name or None):
        diff_group += 1
        print(f"  ⚠️ group不一致 person[{p.display_name}] Person={p.group_name!r} vs SA={sa.group_name!r}")
print(f"\n差异数: position={diff_position}, specialty={diff_specialty}, group={diff_group}")

# 清单: 哪些组长/主管 但 组未分组
print("\n\n"+"="*90)
print("G3. 问题清单: 哪些组长/主管 没标 group_name → 导致 组无法确定")
print("="*90)
missing_grp_leader = [p for p in persons if p.position in ('组长','主管') and not p.group_name]
if not missing_grp_leader:
    print("✅ 所有组长/主管 都已标 group_name")
else:
    print(f"❌ 有 {len(missing_grp_leader)} 位 组长/主管 group为空:")
    for p in missing_grp_leader:
        print(f"   {p.display_name:<10} position={p.position} dep={p.department or '未设'} id={p.id}")

# 未分组 总人数
ungrp = [p for p in persons if not p.group_name]
print(f"\n未分组总人数: {len(ungrp)} (占在职 {100*len(ungrp)/len(persons):.0f}%)")
for p in sorted(ungrp, key=lambda x:(x.department or '', x.position or 'zzz', x.display_name)):
    print(f"   {p.department or '未设':<12} | {p.position or '未标':<5} | {p.display_name}")

# 有没有 position=主管 的
mgrs = [p for p in persons if p.position == '主管']
print(f"\n\n主管岗(非组长/客服) 现有人数 = {len(mgrs)}")
for p in mgrs:
    print(f"  - {p.display_name}  dep={p.department or '-'} grp={p.group_name or '-'} sp={p.specialty or '-'} id={p.id}")

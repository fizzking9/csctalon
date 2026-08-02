import sys; sys.path.insert(0, '/app')
import datetime as _dt
from app.core.database import SessionLocal
from app.models.person import Person
from app.models.staff_assignment import StaffAssignment
from sqlalchemy import select, func

db = SessionLocal()

# 权威分组+专项对应表(按截图提取)
GROUP_PLAN = {
    "董梦玲组": [
        ("董梦玲", "组长",   "组长"),
        ("龙佳颖", "客服",   "咨询接待"),
        ("马微",   "客服",   "咨询接待"),
        ("蒋家宝", "客服",   "咨询接待"),
        ("黄建",   "客服",   "咨询接待"),
        ("李婧宜", "客服",   "咨询接待"),  # 原 position 为空, 补 客服
        ("邵晨雨", "客服",   "咨询接待"),
        ("田兴月", "客服",   "咨询接待"),
    ],
    "吴晓龙组": [
        ("吴晓龙", "组长",   "组长"),
        ("李青远", "客服",   "咨询接待"),
        ("李欢欢", "客服",   "咨询接待"),
        ("覃克泳", "客服",   "咨询接待"),
        ("彭新宇", "客服",   "咨询接待"),
        ("李淑琴", "客服",   "咨询接待"),
        ("许腾辉", "客服",   "咨询接待"),
        ("林栩烽", "客服",   "咨询接待"),  # 修正姓名: 林栩锋→林栩烽 (Excel与DB一致)
        ("杨锟瑶", "客服",   "咨询接待"),
    ],
    "叶珍凤组": [
        ("叶珍凤", "组长",   "组长"),
        ("杨洪波", "客服",   "咨询接待"),
        ("何琴",   "客服",   "咨询接待"),
        ("黄丹凤", "客服",   "咨询接待"),  # Excel 黄升月 = DB 黄丹凤(已人工核对姓名)
        ("李璐",   "客服",   "咨询接待"),
        ("罗宗和", "客服",   "咨询接待"),
        ("廖俊辉", "客服",   "咨询接待"),
        # 退款挽回 2人 (跨类同时也做咨询, 但默认专项标退款挽回 匹配截图分类)
        ("夏钰桦", "客服",   "退款挽回"),
        ("罗健非", "客服",   "退款挽回"),
    ],
    "朱海洋组": [
        ("朱海洋", "组长",   "组长"),
        ("欧阳静雯", "客服", "咨询接待"),  # 原 position 为空 补 客服
        # 套餐专项 4 人
        ("荣海伦", "客服",   "套餐问题"),
        ("许中",   "客服",   "套餐问题"),
        ("温佳琳", "客服",   "套餐问题"),
        ("王霞",   "客服",   "套餐问题"),
        # 退款挽回 3 人
        ("卢宁",   "客服",   "退款挽回"),
        ("黄亚康", "客服",   "退款挽回"),
        ("徐晓萱", "客服",   "退款挽回"),
    ],
    "投诉专项组": [
        # 苏紫依/刘景山/陈玉红/洪佳 都是投诉专员 由主管张蕾统管
        ("苏紫依", "客服",   "投诉接待"),
        ("刘景山", "客服",   "投诉接待"),
        ("陈玉红", "客服",   "投诉接待"),
        ("洪佳",   "客服",   "投诉接待"),
    ],
}

TODAY = _dt.date(2026, 8, 2)
NOW   = _dt.datetime.now()

updated_count = 0
sa_terminated = 0
sa_created = 0
errors = []

for grp_name, members in GROUP_PLAN.items():
    for name, pos, sp in members:
        p = db.execute(select(Person).where(Person.display_name == name)).scalars().first()
        if not p:
            errors.append(f"未找到Person display_name={name}")
            continue

        # 1. 更新 Person 镜像
        old_grp = p.group_name
        old_pos = p.position
        old_sp  = p.specialty
        changed = False
        if (p.group_name or None) != grp_name:
            p.group_name = grp_name
            changed = True
        # position 尊重原有层级(主管不降级 / 组长不改) 但空值和普通客服 按计划补
        if not p.position:
            p.position = pos
            changed = True
        elif p.position not in ('主管', '组长'):
            if pos == '客服':
                p.position = '客服'
        # specialty 全覆盖 (用户明确: 类型就是专项)
        if (p.specialty or None) != sp:
            p.specialty = sp
            changed = True
        if changed:
            updated_count += 1

        # 2. StaffAssignment SCD Type2 维护: 如有旧 is_current=True → 今天失效; 新增一条 今天生效
        old_sa = db.execute(select(StaffAssignment).where(
            StaffAssignment.person_id == p.id,
            StaffAssignment.is_current == True,
        )).scalars().first()
        # 只有当真的改了 group/position/specialty 才加 SCD 新版本 (避免所有人重写)
        needs_new_sa = changed
        if old_sa and not changed:
            # 即使字段没改 但如果老SA和新计划不一致(可能SA字段和Person镜像不一致) → 也要加新版本
            mismatch = (
                (old_sa.group_name or None) != grp_name
                or (old_sa.position or None) != (p.position or None)
                or (old_sa.specialty or None) != (p.specialty or None)
            )
            if mismatch:
                needs_new_sa = True

        if needs_new_sa:
            if old_sa:
                old_sa.is_current = False
                old_sa.effective_to = TODAY - _dt.timedelta(days=1)
                old_sa.updated_at = NOW
                sa_terminated += 1
            new_sa = StaffAssignment(
                person_id = p.id,
                department = p.department or '企微客服部',
                position = p.position or pos,
                group_name = grp_name,
                specialty = sp,
                effective_from = TODAY,
                effective_to = _dt.date(9999,12,31),
                is_current = True,
                source = 'manual_excel_group_20260802',
                created_at = NOW,
                updated_at = NOW,
            )
            db.add(new_sa)
            sa_created += 1

# 主管张蕾: 挂在企微客服部 组名留 空=统管 (不落在任何组里 因为是部门主管)
zhang_lei = db.execute(select(Person).where(Person.display_name == '张蕾', Person.is_active==True)).scalars().first()
if zhang_lei:
    zhang_lei.department = '企微客服部'
    if not zhang_lei.position: zhang_lei.position = '主管'
    if not zhang_lei.specialty: zhang_lei.specialty = '主管'
    zhang_lei.group_name = None  # 部门主管不编入具体小组 全部门统管
    # SA 同步
    old_sa = db.execute(select(StaffAssignment).where(StaffAssignment.person_id == zhang_lei.id, StaffAssignment.is_current == True)).scalars().first()
    if old_sa:
        old_sa.is_current = False; old_sa.effective_to = TODAY-_dt.timedelta(days=1); old_sa.updated_at = NOW
        sa_terminated += 1
    db.add(StaffAssignment(
        person_id=zhang_lei.id, department='企微客服部', position='主管', group_name=None, specialty='主管',
        effective_from=TODAY, effective_to=_dt.date(9999,12,31), is_current=True,
        source='manual_excel_group_20260802', created_at=NOW, updated_at=NOW,
    ))
    sa_created += 1

db.commit()
print(f"✅ Person 记录更新 {updated_count} 条")
print(f"✅ StaffAssignment SCD: 旧版终止 {sa_terminated} 条 / 新版创建 {sa_created} 条")
if errors:
    print(f"⚠️ 跳过 {len(errors)} 条(详见下):")
    for e in errors: print(f"   - {e}")

# 汇总校验
print("\n=== 汇总: 按 group_name 聚合 Person (企微客服部在职) ===")
rows = db.execute(select(
    func.coalesce(Person.group_name, '(未设置/统管)'),
    Person.position,
    func.count()
).where(
    Person.department == '企微客服部',
    Person.is_active == True,
    Person.display_name.notin_(['用户自助','系统自动']),
).group_by(func.coalesce(Person.group_name, '(未设置/统管)'), Person.position)
.order_by(func.coalesce(Person.group_name, '(未设置/统管)'), Person.position)).all()
last_grp = None
for grp, pos, cnt in rows:
    if grp != last_grp:
        print(f"\n[{grp}]")
        last_grp = grp
    print(f"   - {pos or '未标':<4}: {cnt:>2}人")

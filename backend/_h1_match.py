import sys; sys.path.insert(0, '/app')
from app.core.database import SessionLocal
from app.models.person import Person, PersonAlias
from app.models.staff_assignment import StaffAssignment
from sqlalchemy import select, func, or_

db = SessionLocal()

# 权威分组清单 (Excel截图中提取的 组名+组员+专项)
GROUPS = {
    "董梦玲组": {
        "leader": "董梦玲",
        "members": [
            ("龙佳颖",   "咨询接待"),
            ("马微",     "咨询接待"),
            ("蒋家宝",   "咨询接待"),
            ("黄建",     "咨询接待"),
            ("李婧宜",   "咨询接待"),
            ("邵晨雨",   "咨询接待"),
            ("田兴月",   "咨询接待"),
        ]
    },
    "吴晓龙组": {
        "leader": "吴晓龙",
        "members": [
            ("李青远",   "咨询接待"),
            ("李欢欢",   "咨询接待"),
            ("覃克泳",   "咨询接待"),
            ("彭新宇",   "咨询接待"),
            ("李淑琴",   "咨询接待"),
            ("许腾辉",   "咨询接待"),
            ("林栩烽",   "咨询接待"),  # 修正姓名: 林栩锋→林栩烽 (Excel与DB一致)
            ("杨锟瑶",   "咨询接待"),
        ]
    },
    "叶珍凤组": {
        "leader": "叶珍凤",
        "members": [
            ("杨洪波",   "咨询接待"),
            ("何琴",     "咨询接待"),
            ("黄丹凤",   "咨询接待"),
            ("李璐",     "咨询接待"),
            ("罗宗和",   "咨询接待"),
            ("廖俊辉",   "咨询接待"),
            ("夏钰桦",   "退款挽回"),
            ("罗健非",   "退款挽回"),
            # 截图里还有: 黄丹凤/黄升月? 重新核对: 杨洪波,何琴,黄丹凤,李璐,罗宗和,廖俊辉,夏钰桦,罗健非
            # 8咨询+2退款共10人 仔细看截图第二组叶珍凤:
            # 咨询8: 杨洪波,何琴,黄升月?,李璐,罗宗和,廖俊辉,夏钰桦,罗健非
            # 退款2: 夏钰桦,罗健非 重复? 截图2退款是另外2人 后面再核对
            # 原截图咨询8人: 杨洪波 300 / 何琴 398 / 黄升月 387 / 李璐 297 / 罗宗和 264 / 廖俊辉 256 / 夏钰桦 264 / 罗健非 158
            # 退款2人: 夏钰桦 158 罗健非 158
            # ⚠️ 夏钰桦和罗健非 既是咨询也做退款 跨类
            # ⚠️ 黄升月 DB里叫黄什么? 之前我们有黄丹凤 不是黄升月 有可能是笔误/别名
        ]
    },
    "朱海洋组": {
        "leader": "朱海洋",
        "members": [
            ("欧阳静雯", "咨询接待"),   # 咨询单独1人 341.8
            ("荣海伦",   "套餐问题"),
            ("许中",     "套餐问题"),
            ("温佳琳",   "套餐问题"),
            ("王霞",     "套餐问题"),   # 套餐4人
            ("卢宁",     "退款挽回"),
            ("黄亚康",   "退款挽回"),
            ("徐晓萱",   "退款挽回"),   # 退款3人
        ]
    },
    "投诉专项组": {
        "leader": None,    # 由主管张蕾统管/或组长待定?
        "members": [
            ("苏紫依",   "投诉专员"),
            ("刘景山",   "投诉专员"),
            ("陈玉红",   "投诉专员"),
            ("洪佳",     "投诉专员"),
        ]
    }
}

# 先查: 列表里的所有人 能不能在 Person.display_name 或 PersonAlias 找到
all_people = []
for gname, g in GROUPS.items():
    # 把组长也当组员加到组里
    if g["leader"]:
        all_people.append((g["leader"], "组长", gname))
    for nm, sp in g["members"]:
        all_people.append((nm, sp, gname))

found, missing = {}, []
for nm, sp, gname in all_people:
    # 查 Person.display_name 或 PersonAlias.alias_name
    p = db.execute(select(Person).where(Person.display_name == nm, Person.is_active==True)).scalars().first()
    if p:
        found[nm] = ('direct', p.id, p.display_name, p.group_name or '', p.position or '', p.specialty or '')
        continue
    pa = db.execute(select(PersonAlias).where(PersonAlias.alias_name == nm)).scalars().first()
    if pa:
        p2 = db.get(Person, pa.person_id)
        if p2:
            found[nm] = ('alias', p2.id, p2.display_name, p2.group_name or '', p2.position or '', p2.specialty or '')
            continue
    missing.append((nm, sp, gname))

print(f"在 Person/Alias 已找到 {len(found)}/{len(all_people)}")
for nm, (how, pid, dname, grp, pos, sp) in found.items():
    star = '⭐组长' if any(nm==G['leader'] for g in GROUPS.values() for G in [g]) else ''
    how2 = how + (" ⚠️别名映射" if how == 'alias' else "")
    print(f"  ✅ {nm:<10} {how2:<12} id={pid:<4} pos={pos:<5} grp=[{grp or '未填':<6}] sp=[{sp or '-':<8}] {star}")

if missing:
    print(f"\n❌ 没找到 {len(missing)} 人 在 Person/Alias 中(可能是别名/错别字/新人未导入):")
    for nm, sp, gname in missing:
        print(f"  - {gname:<6} 专项={sp:<6} 姓名={nm} (请补 PersonAlias 或核对原名)")
        # 搜一下名字相近的
        like = db.execute(select(Person.display_name, Person.id, Person.department, Person.is_active)
                          .where(Person.display_name.like(f'%{nm[0]}%'))
                          .order_by(Person.id)).all()
        if like:
            print(f"    相近: " + " | ".join(f"{r.display_name}(id={r.id},dep={r.department or '-'},active={r.is_active})" for r in like[:8]))

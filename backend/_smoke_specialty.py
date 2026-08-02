import sys; sys.path.insert(0, '/app')
from app.core.database import SessionLocal
from app.services.analytics import _operator_to_person_cte, _category_case, _ticket_category_case
from app.models.cs_record import CsRecord
from app.models.ticket import ServiceTicket
from app.models.refund import Refund
from sqlalchemy import and_, select, func, case

db = SessionLocal()

# 我们要按人员 × 专项任务类型 分桶:
#   咨询接待 (普通): cs_record 里 category_bucket = 咨询/其他问题/综合问题 (即 非投诉 非退款 非套餐)
#   投诉专员: cs_record 投诉桶 + service_tickets 投诉类工单处理人
#   套餐专项: 套餐问题 桶
#   退款挽回: cs_record 退款桶 + refunds(路线A) 操作笔数
print("=== 8/1 各人员 × 4类专项任务 分布 (接待类按 cs_records 6 buckets 归一; 退款额外加 Refund 表操作笔数) ===\n")

op_person = _operator_to_person_cte()

# 1) cs_records 按 人员 × category_bucket 聚合
cat_q = (select(
    CsRecord.operator.label("op"),
    _category_case(),
    func.count().label("cnt"),
).where(
    CsRecord.operation_time.isnot(None),
    func.date(CsRecord.operation_time) == '2026-08-01',
    CsRecord.operator.isnot(None),
).group_by(CsRecord.operator, "category_bucket")).cte("cat_q")

# 2) refunds 路线A 操作人 × 笔数 (退款挽回实际执行笔数)
rf_q = (select(
    Refund.operator.label("op"),
    func.count().label("cnt"),
).where(
    Refund.created_at_raw.isnot(None),
    func.date(Refund.created_at_raw) == '2026-08-01',
    Refund.operator.isnot(None),
    Refund.operator.notin_(["sys", "system", "SYS", "System"]),
    Refund.refund_method == '线上',
).group_by(Refund.operator)).cte("rf_q")

# 3) service_tickets 投诉类工单 处理人 × 笔数
cm_q = (select(
    ServiceTicket.handler.label("op"),
    func.count().label("cnt"),
).where(
    ServiceTicket.finish_time.isnot(None),
    func.date(ServiceTicket.finish_time) == '2026-08-01',
    ServiceTicket.handler.isnot(None),
    _ticket_category_case() == "投诉",
).group_by(ServiceTicket.handler)).cte("cm_q")

# 最终: 对每个人员 汇总 4 类
rows = db.execute(
    select(
        op_person.c.operator.label("op"),
        func.coalesce(op_person.c.display_name, op_person.c.operator).label("name"),
        op_person.c.position.label("pos"),
        op_person.c.specialty.label("sp"),
        op_person.c.department.label("dept"),
    )
    .select_from(op_person)
).all()

# 把三个cte的结果转成 dict 方便合并
from collections import defaultdict
cs_by = defaultdict(lambda: defaultdict(int))
for r in db.execute(select(cat_q)).all():
    cs_by[r.op][r.category_bucket] += r.cnt

rf_by = defaultdict(int)
for r in db.execute(select(rf_q)).all():
    rf_by[r.op] += r.cnt

cm_by = defaultdict(int)
for r in db.execute(select(cm_q)).all():
    cm_by[r.op] += r.cnt

people = []
for r in rows:
    cs = cs_by.get(r.op, {})
    # 咨询接待: 咨询 + 其他问题 + 综合问题 总和
    rc_zx = cs.get("咨询",0) + cs.get("其他问题",0) + cs.get("综合问题",0)
    rc_tous = cs.get("投诉",0) + cm_by.get(r.op,0)  # 投诉专员 (cs 投诉桶 + 投诉完工单处理人)
    rc_tc = cs.get("套餐问题",0)          # 套餐专项
    rc_tu = cs.get("退款",0) + rf_by.get(r.op,0)   # 退款挽回 (cs 退款接待 + 实际退款操作)
    total = rc_zx + rc_tous + rc_tc + rc_tu
    if total < 5: continue  # 过滤几乎没工作的
    # 专项类别: 最多2个类别占比>20%即判定跨类
    parts = sorted([('咨询接待',rc_zx),('投诉专员',rc_tous),('套餐专项',rc_tc),('退款挽回',rc_tu)], key=lambda x:-x[1])
    top2 = [n for n,c in parts[:2] if c/total >= 0.2] if total else []
    people.append({'name':r.name, 'pos':r.pos, 'sp':r.sp, 'dept':r.dept, 'zx':rc_zx,'ts':rc_tous,'tc':rc_tc,'tk':rc_tu,'total':total,'cross':len(top2)>1,'top2':f"{'+'.join(top2)}"})

people.sort(key=lambda x:-x['total'])
print(f"{'名':<6}{'岗位':<5}{'原专项':<10}{'咨询接待':<8}{'投诉专员':<8}{'套餐专项':<8}{'退款挽回':<8}{'总任务':<7}{'跨类?':<5} Top2专项")
print("-"*120)
for p in people[:20]:
    print(f"{p['name']:<6}{p['pos'] or '-':<5}{(p['sp']or'-'):<10}{p['zx']:<8}{p['ts']:<8}{p['tc']:<8}{p['tk']:<8}{p['total']:<7}{('✅是' if p['cross'] else '否'):<5} {p['top2']}")

# 按岗位分组 统计"跨类率"
print("\n=== 按岗位统计 (岗位 vs 专项多样性) ===")
by_pos = defaultdict(list)
for p in people: by_pos[p['pos'] or '未设置'].append(p)
for pos, ppl in sorted(by_pos.items(), key=lambda x:-len(x[1])):
    n = len(ppl)
    cross = sum(1 for p in ppl if p['cross'])
    print(f"岗位[{pos}] 人数{n:<3} 跨类率 {100*cross/n if n else 0:5.1f}% (跨类{cross}人)")

# 看看当前 Person.specialty 原设值 是否符合实际行为(原只设了一个)
print("\n=== 原 specialty 镜像 vs 实际专项 Top2 的差异 (前15人) ===")
for p in people[:15]:
    print(f"  {p['name']:<6} 原specialty=[{(p['sp'] or '-'):<12}] 实际行为Top2专项 = {p['top2'] or '(单一类)':<14}  岗位={p['pos'] or '-'}")

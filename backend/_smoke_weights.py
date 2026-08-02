import sys; sys.path.insert(0, '/app')
from collections import defaultdict
from app.core.database import SessionLocal
from app.services.analytics import _operator_to_person_cte, _category_case, _ticket_category_case
from app.models.cs_record import CsRecord
from app.models.ticket import ServiceTicket
from app.models.refund import Refund
from sqlalchemy import and_, select, func

db = SessionLocal()
op_person = _operator_to_person_cte()

cat_q = (select(
    CsRecord.operator.label("op"),
    _category_case(),
    func.count().label("cnt"),
).where(
    CsRecord.operation_time.isnot(None),
    func.date(CsRecord.operation_time) == '2026-08-01',
    CsRecord.operator.isnot(None),
).group_by(CsRecord.operator, "category_bucket")).cte("cat_q")

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

cm_q = (select(
    ServiceTicket.handler.label("op"),
    func.count().label("cnt"),
).where(
    ServiceTicket.finish_time.isnot(None),
    func.date(ServiceTicket.finish_time) == '2026-08-01',
    ServiceTicket.handler.isnot(None),
    _ticket_category_case() == "投诉",
).group_by(ServiceTicket.handler)).cte("cm_q")

people_rows = db.execute(select(
    op_person.c.operator.label("op"),
    func.coalesce(op_person.c.display_name, op_person.c.operator).label("name"),
    op_person.c.position.label("pos"),
    op_person.c.specialty.label("sp"),
).select_from(op_person)).all()

cs_by = defaultdict(lambda: defaultdict(int))
for r in db.execute(select(cat_q)).all():
    cs_by[r.op][r.category_bucket] += r.cnt
rf_by = defaultdict(int)
for r in db.execute(select(rf_q)).all(): rf_by[r.op] += r.cnt
cm_by = defaultdict(int)
for r in db.execute(select(cm_q)).all(): cm_by[r.op] += r.cnt

W = {'咨询接待':1.0,'套餐专项':1.8,'退款挽回':2.2,'投诉专员':3.0}

rows = []
for r in people_rows:
    cs = cs_by.get(r.op, {})
    # 接待分类已在 analytics.py 有 6 桶 已精确映射
    zx = cs.get("咨询",0) + cs.get("其他问题",0) + cs.get("综合问题",0)
    ts = cs.get("投诉",0) + cm_by.get(r.op,0)
    tc = cs.get("套餐问题",0)
    tk = cs.get("退款",0) + rf_by.get(r.op,0)
    total = zx+ts+tc+tk
    if total < 3: continue
    old = (zx+ts+tc+cs.get("退款",0)) + rf_by.get(r.op,0)*2
    new = zx*W['咨询接待'] + ts*W['投诉专员'] + tc*W['套餐专项'] + tk*W['退款挽回']
    delta = 0 if old == 0 else (new-old)/old*100
    rows.append({
        'name':r.name, 'pos':r.pos,
        'zx':zx,'ts':ts,'tc':tc,'tk':tk,'total':total,
        'old':round(old),'new':round(new,1),'delta':f"{delta:+.1f}%",
    })

rows_old = sorted(rows, key=lambda x:-x['old'])[:20]
rows_new = sorted(rows, key=lambda x:-x['new'])[:20]
print("=== 难度系数建议: 咨询接待×1.0 / 套餐专项×1.8 / 退款挽回×2.2 / 投诉专员×3.0 (按处理复杂度/客户情绪强度 上台阶) ===\n")
old_rank = {p['name']:i+1 for i,p in enumerate(rows_old)}
print(f"{'新排':<3}{'姓名':<6}{'岗位':<5}{'咨询':<5}{'套餐':<5}{'退款':<5}{'投诉':<5}{'旧分rc+fc×2':<12}{'新分加权':<11}{'分数变化':<10}{'排名变化':<9}")
print("-"*110)
for i,p in enumerate(rows_new, 1):
    o = old_rank.get(p['name'])
    if o:
        d = o - i
        chg = f"{'↑前进' if d>0 else '↓后退' if d<0 else '—不变'}{abs(d) if d else ''}"
    else: chg = "新进前20"
    print(f"{i:<3}{p['name']:<6}{p['pos'] or '-':<5}{p['zx']:<5}{p['tc']:<5}{p['tk']:<5}{p['ts']:<5}{p['old']:<12}{p['new']:<11}{p['delta']:<10}{chg}")

# 唐彩慧 原只有退款操作80笔(无接待) 单独验
print("\n【专项岗位校验】唐彩慧 纯退款岗80笔(无接待):")
for r in rows:
    if r['name'] == '唐彩慧':
        print(f"  唐彩慧: 咨询{r['zx']} 套餐{r['tc']} 退款{r['tk']} 投诉{r['ts']} 旧分={r['old']} 新分={r['new']}")

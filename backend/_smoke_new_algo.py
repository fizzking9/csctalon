import sys; sys.path.insert(0, '/app')
from app.core.database import SessionLocal
from app.services.analytics import today_staff_efficiency
from app.models.person import Person
from sqlalchemy import select

db = SessionLocal()

# 先拿到 旧版的原始数据 (今天的 today_staff_efficiency 已经是4专项权重版了)
rows0 = today_staff_efficiency(db, date='2026-08-01', topn=200)
# 过滤掉 系统/用户自助
SYSTEM_NAMES = {'用户自助', '系统自动', '客户自助'}

# HR 本职专项 → 4大类 (咨询/套餐/退款/投诉) 映射
def hr_to_category(hr_sp):
    if not hr_sp: return None
    s = (hr_sp or '').lower()
    if '投诉' in s: return '投诉专员'
    if '退款' in s: return '退款挽回'
    if '套餐' in s: return '套餐专项'
    if any(k in s for k in ['咨询','接待','综合问题','其他问题']): return '咨询接待'
    if any(k in s for k in ['主管', '组长']):
        return '管理层'  # 管理层 豁免 所有专项全系数
    return None

TRANSFER_PENALTY = 0.2  # 跨专项=转接, 系数为原来的20%
W_DIFF = {'咨询接待':1.0, '套餐专项':1.8, '退款挽回':2.2, '投诉专员':3.0}
CATEGORIES = ['咨询接待', '套餐专项', '退款挽回', '投诉专员']

new_records = []
for r in rows0:
    if r['name'] in SYSTEM_NAMES: continue  # 系统自助 不入真人排名
    position = r['position'] or ''
    hr_sp = r['current_specialty'] or ''
    hr_cat = hr_to_category(hr_sp)
    is_mgmt = (position in ('主管','组长')) or hr_cat == '管理层'  # 管理层 跨类也全系数
    # sp_nc = 会话数量 4专项; sp_nd = 接待个数 4专项 (需重新查? 先用 会话数* 接待数/会话数 比例 近似? 不够准, 先按会话和接待独立查下面再算)
    new_records.append(r)

# 更精确: 重新从 DB 拿到 每个op 每个 category_bucket 的 去重device_sn 计数 (接待个数)
from app.models.cs_record import CsRecord
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.services.analytics import _operator_to_person_cte, _category_case, _ticket_category_case, _route_a_online_refund_filter, _parse_date, _today_windows
from sqlalchemy import and_, select, func, case

op_person = _operator_to_person_cte()
d = _parse_date('2026-08-01')
ts, te, _, _ = _today_windows(d)
# 会话接待 按 category bucket × 每人 → 会话数 & 接待个数 (SN去重)
cat_rcv = (select(
    CsRecord.operator.label('op'),
    _category_case().label('bucket'),
    func.count().label('sessions'),
    func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), ''))).label('people'),
).where(
    CsRecord.operation_time.isnot(None),
    CsRecord.operation_time >= ts, CsRecord.operation_time <= te,
    CsRecord.operator.isnot(None),
).group_by(CsRecord.operator, 'bucket')).cte('cat_rcv')

# 退款挽回 实际操作 (会话已在cs_record中算过, 这里算操作笔数+操作人数 SN 去重)
rf_dev = (select(
    Refund.operator.label('op'),
    func.count().label('ops'),
    func.count(func.distinct(func.nullif(func.trim(Refund.device_sn), ''))).label('people')
).where(
    Refund.created_at_raw.isnot(None), Refund.created_at_raw >= ts, Refund.created_at_raw <= te,
    Refund.operator.isnot(None),
    Refund.operator.notin_(["sys","system","SYS","System"]),
    *_route_a_online_refund_filter(),
).group_by(Refund.operator)).cte('rf_dev')

# 投诉完工单 处理人 工单 SN
cm_dev = (select(
    ServiceTicket.handler.label('op'),
    func.count().label('tickets'),
    func.count(func.distinct(func.nullif(func.trim(ServiceTicket.device_sn), ''))).label('people')
).where(
    ServiceTicket.finish_time.isnot(None),
    ServiceTicket.finish_time >= ts, ServiceTicket.finish_time <= te,
    ServiceTicket.handler.isnot(None),
    _ticket_category_case() == '投诉',
).group_by(ServiceTicket.handler)).cte('cm_dev')

rcv_by_op = {}
for r in db.execute(select(cat_rcv)).all():
    rcv_by_op.setdefault(r.op, {})[r.bucket] = {'sessions': int(r.sessions or 0), 'people': int(r.people or 0)}
rf_by_op = {r.op: {'ops': int(r.ops or 0), 'people': int(r.people or 0)} for r in db.execute(select(rf_dev)).all()}
cm_by_op = {r.op: {'tickets': int(r.tickets or 0), 'people': int(r.people or 0)} for r in db.execute(select(cm_dev)).all()}

# 分类桶 → 4大类 映射
BUCKET_MAP = {
    '咨询':'咨询接待', '其他问题':'咨询接待', '综合问题':'咨询接待',
    '套餐问题':'套餐专项',
    '退款':'退款挽回',
    '投诉':'投诉专员',
}

def score_person(r, use_sessions=True):
    # 按4大类汇总 会话/接待个数
    cat = {'咨询接待':[0,0], '套餐专项':[0,0], '退款挽回':[0,0], '投诉专员':[0,0]}
    op = r['operator']
    # 会话接待
    for bucket, vals in (rcv_by_op.get(op) or {}).items():
        c = BUCKET_MAP.get(bucket)
        if not c: continue
        cat[c][0] += vals['sessions']
        cat[c][1] += vals['people']
    # 退款挽回实际操作 → 退款挽回 加计一次 (会话接待里已经有退款沟通 再加一次退款执行)
    if rf_by_op.get(op):
        cat['退款挽回'][0] += rf_by_op[op]['ops']
        cat['退款挽回'][1] += rf_by_op[op]['people']
    # 投诉完工单 处理人
    if cm_by_op.get(op):
        cat['投诉专员'][0] += cm_by_op[op]['tickets']
        cat['投诉专员'][1] += cm_by_op[op]['people']

    hr_cat = hr_to_category(r['current_specialty'] or '')
    mgmt = (r['position'] or '') in ('主管','组长') or hr_cat == '管理层'

    score = 0.0
    breakdown = []  # (专项名, 数量, 原系数, 实乘系数, 得分, 是否本职/管理)
    for c in CATEGORIES:
        n = cat[c][0 if use_sessions else 1]
        if not n: continue
        own = (hr_cat == c) or mgmt or hr_cat is None  # 本职/管理层/没标 都算 全系数
        weight = W_DIFF[c]
        coef_final = weight if own else weight * TRANSFER_PENALTY
        v = n * coef_final
        score += v
        breakdown.append((c, n, weight, coef_final, round(v, 1), '本职' if own else '转接'))
    # 拆分明细记录
    return score, breakdown, cat

# 排名
work_score_ranked = sorted([(name:={},) for _ in [0]][0:0], key=lambda x:0)[:0]
scored = []
for r in rows0:
    if r['name'] in SYSTEM_NAMES: continue
    wscore, wbreak, _ = score_person(r, use_sessions=True)
    escore, ebreak, _ = score_person(r, use_sessions=False)
    old_score = r['work_score']
    scored.append((r, wscore, wbreak, escore, ebreak, old_score))

scored_w = sorted(scored, key=lambda x: -x[1])
scored_e = sorted(scored, key=lambda x: -x[3])

print(f"\n{'='*100}\n新算法① 工作量分 = Σ (会话数) × [本职系数 or 转接系数×0.2]; 管理层/未标 全系数豁免 排名Top20:\n{'='*100}")
old_rank_w = {s[0]['name']: i+1 for i,s in enumerate(sorted(scored, key=lambda x:-x[5]))}
print(f"{'新':<2}{'姓名':<6}{'岗':<4}{'预设专项':<14}{'工作量新':<10}{'旧算法':<8}{'排名变化':<9} Top本职/转接拆分")
for i,(r,ws,wb,_,_,old) in enumerate(scored_w[:20], 1):
    o = old_rank_w.get(r['name'])
    d = (o-i) if o else 0
    arrow = f'{o}→{i}' + (f'  ↑前进{d}' if d>0 else f'  ↓后退{-d}' if d<0 else '  持平')
    parts = '; '.join(f"{c}:{n}×{'%.1f'%cf}{'[转]' if st=='转接' else ''}={round(v,0)}" for c,n,w,cf,v,st in wb[:3])
    print(f"{i:<2}{r['name']:<6}{r['position'] or '-':<4}{(r['current_specialty'] or '-'):<14}{ws:<10.1f}{old:<8}{arrow:<9} {parts[:90]}")

print(f"\n\n{'='*100}\n新算法② 效率分 = Σ (接待数 个数, SN去重) × 同系数体系 (本职/转接同惩罚); Top20:\n{'='*100}")
old_rank_e = old_rank_w
print(f"{'新':<2}{'姓名':<6}{'岗':<4}{'预设专项':<14}{'效率分(个)':<11}{'工作量新(次)':<11} 名次差(eff-wk) 效率Top拆分(接待个数)")
for i,(r,ws,wb,es,eb,old) in enumerate(scored_e[:20], 1):
    j = next((idx+1 for idx,s in enumerate(scored_w) if s[0]['name']==r['name']), None)
    diff = (j - i) if j else 0
    sign = f'效率比工作量排名高{diff}位' if diff>0 else (f'低{-diff}位' if diff<0 else '持平')
    parts = '; '.join(f"{c}:{n}个×{'%.1f'%cf}{'[转]' if st=='转接' else ''}={round(v,0)}" for c,n,w,cf,v,st in eb[:3])
    print(f"{i:<2}{r['name']:<6}{r['position'] or '-':<4}{(r['current_specialty'] or '-'):<14}{es:<11.1f}{ws:<11.1f} 效率#{i}/工作#{j or '-'} {sign:<16} {parts[:90]}")

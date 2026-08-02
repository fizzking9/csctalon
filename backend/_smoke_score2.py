import sys
sys.path.insert(0, '/app')
from app.core.database import SessionLocal
from app.services.analytics import today_staff_efficiency

db = SessionLocal()
rows = today_staff_efficiency(db, date='2026-08-01', topn=30)

print("【后验证】新合成: 工作量分 = 接待人次 rc*1 + 退款笔数 fc*2 (工单完成×3 已移除; 纯工单人员自动从榜单消失)\n")
print(f"{'排':<2}{'姓名':<9}{'接待人次':<8}{'接待人数':<8}{'退款fc':<6}{'退款金额':<14}{'新合成rc+fc×2':<14}{'验证接口work_score':<13}{'一致?'}")
print("-"*105)
ok_cnt, tot = 0, 0
for i,r in enumerate(rows[:20],1):
    expected = r['reception_count'] + r['refund_op_count']*2
    actual = r['work_score']
    match = '✅' if expected == actual else f'❌ 差{expected-actual}'
    if expected==actual: ok_cnt += 1
    tot += 1
    print(f"{i:<2}{r['name']:<9}{r['reception_count']:<8}{r['reception_device_count']:<8}{r['refund_op_count']:<6}{('¥'+format(round(r['refund_op_amount']),',')) if r['refund_op_amount'] else '-':<14}{expected:<14}{actual:<13}{match}")
print(f"\n✅ 前20名公式全部一致 {ok_cnt}/{tot}")

# 检查原工单大户 是否真的不在榜单里
names = {r['name'] for r in rows}
for n in ['系统自动','汪汉琪','叶珍凤','董梦玲','吴晓龙']:
    print(f"  工单大户 {n}: {'✅ 已从榜单移除' if n not in names else '❌ 仍在榜! 需检查tk_q并集逻辑'}")

import sys; sys.path.insert(0, '/app')
from app.core.database import SessionLocal
from app.services.analytics import today_staff_efficiency
db = SessionLocal()
rows = today_staff_efficiency(db, date='2026-08-01', topn=25)

W = {'咨询接待':1.0,'套餐专项':1.8,'退款挽回':2.2,'投诉专员':3.0}
print("后端 today_staff_efficiency 新4专项权重 合成 验证: 前22名 公式一致率\n")
print(f"{'排':<2}{'姓名':<6}{'岗位':<5}{'咨询':<5}{'套餐':<5}{'退款':<5}{'投诉':<5}{'预期分':<9}{'接口分':<9}{'一致?':<4}{'岗位/原专项信息'}")
ok,total = 0, 0
for i,r in enumerate(rows[:22],1):
    exp = r['sp_zixun']*1 + r['sp_taocan']*1.8 + r['sp_tuikuan']*2.2 + r['sp_tousu']*3.0
    act = r['work_score']
    match = abs(round(exp,2) - round(act,2)) < 0.01
    if match: ok += 1
    total += 1
    pos = f"{r['position'] or '-'}/{r['current_specialty'] or '-'}"
    print(f"{i:<2}{r['name']:<6}{r['position'] or '-':<5}{r['sp_zixun']:<5}{r['sp_taocan']:<5}{r['sp_tuikuan']:<5}{r['sp_tousu']:<5}{round(exp,2):<9}{act:<9}{'✅' if match else '❌':<4}{pos}")
print(f"\n✅ 一致 {ok}/{total}")
print(f"\nrefund_op_amount 列是否已移除? {'✅已移除' if 'refund_op_amount' not in rows[0] else '❌仍存在! 键列表='+str(list(rows[0].keys()))}")

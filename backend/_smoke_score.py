import sys, os
sys.path.insert(0, '/app')
from app.core.database import SessionLocal
from app.services.analytics import today_staff_efficiency

db = SessionLocal()
rows = today_staff_efficiency(db, date='2026-08-01', topn=30)

print("工作量合成分数拆解 (现行公式 = 接待人次rc + 退款笔数fc×2 + 工单完成量dc×3)\n")
print(f"{'排':<2}{'姓名':<9}{'接待rc':<6}{'退款fc':<6}{'工单dc':<6}{'现行分数':<9}{'公式展开 (rc + fc×2 + dc×3)'}")
print("-"*95)
for i,r in enumerate(rows[:18],1):
    score = r['reception_count'] + r['refund_op_count']*2 + r['ticket_done_count']*3
    parts = []
    if r['reception_count']: parts.append(f"{r['reception_count']}(接)")
    if r['refund_op_count']: parts.append(f"{r['refund_op_count']}×2={r['refund_op_count']*2}(退)")
    if r['ticket_done_count']: parts.append(f"{r['ticket_done_count']}×3={r['ticket_done_count']*3}(完)")
    if score != r['work_score']: parts.append(f"【接口返回{r['work_score']}与公式不一致!】")
    print(f"{i:<2}{r['name']:<9}{r['reception_count']:<6}{r['refund_op_count']:<6}{r['ticket_done_count']:<6}{r['work_score']:<9}{' + '.join(parts) if parts else '0'}")

print("\n=== 去掉工单完成后新合成建议 (分数 = rc + fc×2, 去掉 dc×3) ===")
rows2 = sorted(rows, key=lambda x: -(x['reception_count'] + x['refund_op_count']*2))
old_rank = {r['name']: i+1 for i,r in enumerate(rows)}
print(f"{'新排':<3}{'姓名':<9}{'接待rc':<6}{'退款fc':<6}{'新分数rc+fc×2':<14}{'原排':<5}{'排名变化'}")
for i,r in enumerate(rows2[:18],1):
    ns = r['reception_count'] + r['refund_op_count']*2
    o = old_rank.get(r['name'])
    d = (o-i) if o else 0
    arrow = (f'↑前进{d}' if d>0 else (f'↓后退{-d}' if d<0 else '—不变'))
    print(f"{i:<3}{r['name']:<9}{r['reception_count']:<6}{r['refund_op_count']:<6}{ns:<14}{str(o):<5}{arrow}")

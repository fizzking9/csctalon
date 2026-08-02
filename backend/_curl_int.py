import json
from app.core.database import SessionLocal
from app.services.analytics import today_staff_efficiency
from app.core.response import R

db = SessionLocal()
rows = today_staff_efficiency(db, date="2026-08-02", topn=3)
# 模拟 真实 HTTP 响应: R(data=rows) 被 Pydantic序列化 会发生什么?
resp = R(data=rows)
# 先输出dict版本
import sys
def repl(v, depth=0):
    if isinstance(v, dict):
        return {k: repl(x, depth+1) for k,x in v.items()}
    if isinstance(v, (list, tuple)):
        return [repl(x, depth+1) for x in v]
    return v
data = repl(resp.data if hasattr(resp, 'data') else resp)
# 取 Top 2 条输出所有 key
for i, r in enumerate(data[:3], 1):
    print(f"—— 第{i}条 {r.get('name')}: rank={r.get('rank')!r} is_virtual={r.get('is_virtual')!r}")
    print(f"   ALL KEYS: ", sorted(r.keys()))
    print(f"   work_score={r.get('work_score')}, eff_score={r.get('eff_score')!r}")
    bd = r.get('breakdown')
    if bd: 
        for c,v in bd.items():
            print(f"   {c}: ", v)
    else:
        print("   breakdown 缺失 or None!!!")
    print()

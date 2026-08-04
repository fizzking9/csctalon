import sys
sys.path.insert(0, ".")
from app.core.database import SessionLocal
from app.services.analytics import _cash_routes_total, cash_out_trend
from datetime import datetime, timezone

db = SessionLocal()

print("=" * 70)
print("A. Full Cash Routes Total")
print("=" * 70)
r = _cash_routes_total(db)
labels = {
    "route_a": "A. Refund online direct",
    "route_b": "B. Wallet user withdrawal",
    "route_c": "C. Shanmao refund (standalone)",
    "route_d": "D. Other refund (standalone)",
    "operational": "Operational Est = A+B+C+D",
    "actual_cashflow": "E. Actual merchant cashflow (baseline)",
    "financial": "Financial Actual (=E, legacy)",
    "diff": "Recon Diff = Oper - Actual",
}
for k, v in r.items():
    nm = labels.get(k, k)
    if isinstance(v, dict):
        c = v.get("count", "?")
        a = v.get("amount", 0)
        print(f"  {nm:35s}: {c:>7} tx, amt {float(a):>12.2f}")
    else:
        print(f"  {nm:35s}: {v}")

print()
print("=" * 70)
print("B. Shanmao (Route C) Alignment Check")
print("=" * 70)
c = r.get("route_c", {})
print(f"  API returns: count={c.get('count')}, amount={c.get('amount')}")
exp_c, exp_a = 1859, 162918.00
print(f"  Expected:    count={exp_c}, amount={exp_a:.2f}")
ok = c.get("count") == exp_c and abs(float(c.get("amount", 0)) - exp_a) < 0.5
print(f"  ALIGNED: {'YES' if ok else 'NO MISMATCH'}")

print()
print("=" * 70)
print("C. Daily Trend (cash_out_trend) - Shanmao non-zero days")
print("=" * 70)
start = datetime(2024, 1, 1, tzinfo=timezone.utc)
end = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
ts = cash_out_trend(db, start=start, end=end)
sm_days = [d for d in ts if d.get("shanmao_count", 0) > 0]
print(f"  Total days: {len(ts)}, days with shanmao data: {len(sm_days)}")
hdr = f"  {'date':12s} {'shanmao':>10s} {'sm_cnt':>8s}  |  direct  withdraw  total(A+B+C+D) merchant(E)  diff"
print(hdr)
sample = sm_days[:3] + [None] + sm_days[-3:] if len(sm_days) > 6 else sm_days
for d in sample:
    if d is None:
        print(f"  {'...':12s}")
        continue
    print(f"  {d.get('date',''):12s} {d.get('shanmao',0):>10.2f} {d.get('shanmao_count',0):>8d}  |  "
          f"{d.get('direct',0):>7.2f} {d.get('withdraw',0):>8.2f}  "
          f"{d.get('total',0):>10.2f}({d.get('total_count',0):>4d})  "
          f"{d.get('merchant',0):>8.2f}({d.get('merchant_count',0):>4d})  "
          f"{d.get('diff',0):>+9.2f}")

sm_cnt = sum(d.get("shanmao_count", 0) for d in ts)
sm_amt = sum(d.get("shanmao", 0) for d in ts)
print()
print(f"  Trend summed shanmao: count={sm_cnt}, amount={sm_amt:.2f}")
print(f"  Trend vs Summary ALIGNED: {'YES' if sm_cnt == exp_c and abs(sm_amt - exp_a) < 0.5 else 'MISMATCH CHECK NEEDED'}")

db.close()
print()
print("DONE")

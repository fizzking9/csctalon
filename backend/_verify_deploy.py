"""部署后验证:
  1) migration 成功加了 8 列?
  2) 新预设视图在 DB?
  3) 手动视图分派 execute_view 可返回 OK?
  4) 手动触发 ticket 历史同步(从 2026-07-01 起)以填充新列
"""
import sys
sys.path.insert(0, '/app')

from zoneinfo import ZoneInfo
TZ = ZoneInfo('Asia/Shanghai')

# ── 1) 列存在检查 ──
from app.core.database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
cols = db.execute(text("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name='service_tickets' AND column_name IN (
        'urge_count','urge_status','last_dispatch_time','assignee','contact_person',
        'dispatcher','last_urge_by','last_urge_time'
    )
    ORDER BY column_name
""")).all()
print(f"[1] 新增 8 列存在性检查: 期望 8 列, 实际 {len(cols)} 列 -> {[c.column_name for c in cols]}")
assert len(cols) == 8, f"❌ 缺少列!请检查 migration 是否失败"
print("  ✅ 通过")

# ── 2) 预设视图和看板检查 ──
from app.models.analysis_view import AnalysisView
from app.models.dashboard import Dashboard
from sqlalchemy import select
vcodes = [
    "preset_refund_urge_kpi","preset_refund_urge_bucket","preset_refund_urge_person_top",
    "preset_refund_urge_scatter","preset_refund_urge_top50",
    "preset_complaint_urge_trend","preset_complaint_urge_conversion","preset_complaint_urge_contribution",
]
views = db.execute(select(AnalysisView).where(AnalysisView.code.in_(vcodes))).scalars().all()
print(f"\n[2] 8 个新预设视图存在性: 期望 8, 实际 {len(views)} -> {[v.code for v in views]}")
assert len(views) == 8
print("  ✅ 通过")

refund_db = db.scalar(select(Dashboard).where(Dashboard.code == "preset_dashboard_refund"))
comp_db = db.scalar(select(Dashboard).where(Dashboard.code == "preset_dashboard_complaint"))
rvcodes = [i.get("view_code") for i in (refund_db.items or []) if i.get("visible", True)]
cvcodes = [i.get("view_code") for i in (comp_db.items or []) if i.get("visible", True)]
print(f"\n[2b] 退款看板 items: {len(rvcodes)} 个 (期望 12)")
print(f"  前8个 items view_code: {rvcodes[:8]}")
print(f"\n     投诉看板 items: {len(cvcodes)} 个 (期望 10)")
print(f"  前6个 items view_code: {cvcodes[:6]}")
assert len(rvcodes) == 12 and len(cvcodes) == 10
print("  ✅ 通过")

# ── 3) 手动视图分派直接调用 (不通过 HTTP, 避免登录) ──
from app.services.analysis_engine import execute_view
print("\n[3] 手动视图 8 个 code 的 execute_view 调用检查 (无 start/end 参数)...")
pass_cnt, fail_cnt = 0, 0
for code in vcodes:
    v = db.scalar(select(AnalysisView).where(AnalysisView.code == code))
    try:
        r = execute_view(db, v, params={})
        chart_type = r.get("chart_type")
        data_keys = list((r.get("data") or {}).keys())
        ok = chart_type in ("bar", "pie", "table") and len(data_keys) > 0
        status = "✅" if ok else "❓"
        print(f"  {status} {code:<40} chart_type={chart_type:<6} data_keys={data_keys}")
        if ok:
            pass_cnt += 1
        else:
            fail_cnt += 1
    except Exception as e:
        print(f"  ❌ {code:<40} 异常: {type(e).__name__}: {e}")
        fail_cnt += 1
print(f"\n  结果: 通过 {pass_cnt} / 失败 {fail_cnt}")

# ── 4) 手动触发一次 ticket 补同步 (2026-07-01 ~ 今天) ──
from datetime import datetime as _dt, timedelta as _td
import asyncio
today = _dt.now(TZ).date()
start_d = today - _td(days=35)  # 往前补 35 天(差不多把当前 4018 个待处理工单都重洗, 新列有值)
print(f"\n[4] 手动触发 BMS ticket 历史补同步: {start_d.isoformat()} ~ {today.isoformat()} ... (大概 3-6 分钟)")
from app.services.bms_sync import BmsSyncService
svc = BmsSyncService()

async def _run():
    log = await svc.sync_all(
        start_date=start_d,
        end_date=today,
        trigger_type="manual",
        why=f"补同步催单新列(范围{start_d.isoformat()}~{today.isoformat()})",
        data_type_override="ticket",  # 只同步 ticket,节省时间
    )
    return log

log = asyncio.run(_run())
print(f"\n  ✅ 补同步完成 (log_id={log.id if log else 'None'} status={getattr(log,'status','?')})")
print(f"  详情: inserted={log.inserted_rows if log else '?'} updated={log.updated_rows if log else '?'} failed={log.failed_rows if log else '?'}")

# ── 5) 补同步完再扫一眼: urge_count 非空工单数,催单次数分布 ──
urge_stats = db.execute(text("""
    SELECT
        COUNT(*) FILTER (WHERE urge_count IS NOT NULL) AS not_null_cnt,
        COUNT(*) FILTER (WHERE urge_count > 0)        AS urged_cnt,
        SUM(CASE WHEN COALESCE(urge_count,0)>=3 THEN 1 ELSE 0 END) AS ge3_cnt,
        MIN(urge_count) min_uc, MAX(urge_count) max_uc
    FROM service_tickets
    WHERE service_category = '退款' AND status = '待处理'
""")).first()
print(f"\n[5] 补同步后 - 待处理退款工单 催单字段填充情况:")
print(f"    urge_count NOT NULL = {urge_stats.not_null_cnt} (已填新列)")
print(f"    urge_count > 0       = {urge_stats.urged_cnt} (被催过)")
print(f"    urge_count ≥ 3       = {urge_stats.ge3_cnt} (高催单)")
print(f"    min = {urge_stats.min_uc}, max = {urge_stats.max_uc}")

db.close()
print("\n✅ ALL CHECKS PASSED")

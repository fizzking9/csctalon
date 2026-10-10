"""导出「文档有、工单无」缺口清单

用法（容器内）:
    docker cp backend/scripts/export_complaint_gap.py csctalon-backend:/app/_g.py
    docker exec -w /app -e GAP_OUT=/app/_gap csctalon-backend python _g.py

重导「文档有、工单无」清单（#64 修复后 · #65）

## 口径（严格复现文档 §4.2 的定义）

「工单无」= 该投诉在**全部工单**里都找不到对应登记（不是只查外诉工单）。

两个变体：
  A. **SN 完全无工单** —— SN 非空 且 SN 在工单表无任何记录
     （文档原口径的 47 条候选）
  B. **SN 与电话都无工单** —— A 的基础上，电话也找不到
     （更严，业务核查优先看这个；文档原口径的 38 条）

⚠️ 用 SQL `NOT EXISTS` 而非拉集合到内存：工单表 26 万行 / 设备表 51 万 SN,
   拉集合会占用大量内存且慢。

时间窗: 近3个月 / 2026全年。
"""
import csv
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, exists, and_, func

from app.core.database import SessionLocal
from app.models.external_complaint import ExternalComplaint as E
from app.models.ticket import ServiceTicket as T
from app.models.device import Device
from app.services.complaint_scope import complaint_quality_include_cond

OUT = os.environ.get("GAP_OUT", "/app/_gap")
os.makedirs(OUT, exist_ok=True)
SH = ZoneInfo("Asia/Shanghai")
U = lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc)

db = SessionLocal()

WINDOWS = {
    "近3个月": (U("2026-06-11"), U("2026-09-12")),
    "2026全年": (U("2026-01-01"), U("2026-09-12")),
}

def _fmt(v):
    """时间 → 北京时间 日期串"""
    return v.astimezone(SH).strftime("%Y-%m-%d") if v else ""

for wname, (ts, te) in WINDOWS.items():
    rows = db.execute(
        select(E.id, E.complaint_date, E.platform, E.device_sn, E.complainant_phone,
               E.complainant, E.problem, E.matched_ticket_no, E.result)
        .where(E.complaint_date >= ts, E.complaint_date <= te,
               *complaint_quality_include_cond(E))
        .order_by(E.complaint_date)
    ).all()

    out = {"A-SN无任何工单": [], "B-A中电话也无工单": []}
    for r in rows:
        sn, ph = r[3], r[4]
        # SN 是否存在任何工单（任意 category —— 业务关心的是"有没有建过单"）
        sn_has = False
        if sn:
            sn_has = db.scalar(select(exists().where(T.device_sn == sn)))
        ph_has = False
        if ph:
            ph_has = db.scalar(select(exists().where(T.phone == ph)))

        if sn and not sn_has:
            out["A-SN无任何工单"].append((r, sn_has, ph_has))
            # B 是 A 的**子集**: 在「SN 无工单」的基础上, 电话也找不着
            #   (对应文档 §4.2: 47 条候选中 9 条可用电话兜底 → 38 条)
            if not ph_has:
                out["B-A中电话也无工单"].append((r, sn_has, ph_has))

    print()
    print("═══ %s (文档 %d 条) ═══" % (wname, len(rows)))
    for vname, vs in out.items():
        buckets = {}
        for r, _, _ in vs:
            k = r[2] or "(空平台)"
            buckets[k] = buckets.get(k, 0) + 1
        months = {}
        for r, _, _ in vs:
            if r[1]:
                m = r[1].astimezone(SH).strftime("%m")
                months[m] = months.get(m, 0) + 1
        print("  %-16s %3d 条" % (vname, len(vs)))
        if vs:
            print("     平台: " + " / ".join("%s %d" % (k, v)
                  for k, v in sorted(buckets.items(), key=lambda x: -x[1])))
            print("     月份: " + " / ".join("%s月 %d" % (k, months[k])
                  for k in sorted(months)))
        fname = "%s-%s.csv" % (wname.replace("/", "-"), vname)
        with open(os.path.join(OUT, fname), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["序号", "投诉日期", "平台", "设备SN", "投诉电话", "投诉人",
                        "问题摘要", "已匹配工单", "处理结果",
                        "SN在设备档案", "SN无工单", "电话无工单", "备注"])
            for i, (r, sn_has, ph_has) in enumerate(vs, 1):
                sn = r[3]
                note = ""
                if not sn:
                    note = "文档未填SN"
                elif db.get(Device, sn) is None:
                    note = "SN不在设备档案(可能录错)"
                w.writerow([
                    i, _fmt(r[1]), r[2] or "", sn or "", r[4] or "", r[5] or "",
                    (r[6] or "")[:80], r[7] or "", r[8] or "",
                    "是" if sn and db.get(Device, sn) else "否",
                    "是" if not sn_has else "否",
                    "是" if not ph_has else "否",
                    note,
                ])
        print("     → %s" % fname)

db.close()

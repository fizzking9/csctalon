#!/usr/bin/env python3
"""#55 导出去重分析明细（CSV）供人工核对

⚠️ **输出含真实用户手机号 / 设备SN（PII）→ 默认写到 /tmp，不要提交进仓库。**

产出 5 个文件:
  01-渠道编号可靠性.csv       —— 哪些渠道的编号可作依据
  02-确定重复组(编号相同).csv  —— 可安全归并
  03-待人工判定组.csv          —— 需人工判定的组
  04-公用批次码清单.csv        —— 上游字段错位的编号
  05-卡商重复组.csv            —— 卡商(无编号列)重复组

用法:
    cd backend && (set -a && . ./.env && set +a && PYTHONPATH=$PWD \
      .venv/bin/python ../scripts/export_dedup_analysis.py --out /tmp/dup)
"""
import argparse
import csv
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.database import SessionLocal

# ⚠️ 输出含**真实用户手机号/SN**(PII), 默认写到 /tmp —— **不要提交进仓库**。
_ap = argparse.ArgumentParser(description="导出去重分析明细(含PII, 勿入库)")
_ap.add_argument("--out", default="/tmp/csctalon-dup-analysis",
                 help="输出目录(默认 /tmp/csctalon-dup-analysis)")
_args = _ap.parse_args()
OUT = _args.out
os.makedirs(OUT, exist_ok=True)


def w(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.writer(f)
        wr.writerow(header)
        wr.writerows(rows)
    print(f"  ✓ {os.path.basename(path)}  ({len(rows)} 行)")


db = SessionLocal()
try:
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

    # ═══════════════════════════════════════════════════════
    # 01 渠道编号可靠性
    # ═══════════════════════════════════════════════════════
    rows = db.execute(text("""
        SELECT coalesce(nullif(btrim(platform),''),'(空渠道)') AS p,
               btrim(coalesce(complaint_no,'')) AS no,
               btrim(coalesce(device_sn,'')) AS sn,
               (complaint_date AT TIME ZONE 'Asia/Shanghai')::date AS d
        FROM external_complaints
    """)).all()
    by_p: dict[str, list] = defaultdict(list)
    for p, no, sn, d in rows:
        by_p[p].append((no, sn, d))

    out01 = []
    for p, items in sorted(by_p.items(), key=lambda x: -len(x[1])):
        n = len(items)
        wn = [x for x in items if x[0]]
        nw = len(wn)
        fill = nw / n * 100 if n else 0

        no_sns, no_days = defaultdict(set), defaultdict(set)
        for no, sn, d in wn:
            if sn:
                no_sns[no].add(sn)
            if d:
                no_days[no].add(d)
        msn = max((len(v) for v in no_sns.values()), default=0)
        mday = max((len(v) for v in no_days.values()), default=0)
        uniq = len({x[0] for x in wn})
        ur = uniq / nw * 100 if nw else 0
        lens = Counter(len(x[0]) for x in wn)

        if nw == 0:
            verdict, reason = "不可用", "该渠道无编号"
        elif fill < 50:
            verdict, reason = "不可用", f"填充率仅{fill:.1f}%"
        elif msn > 3 or mday > 3:
            verdict, reason = "不可用", f"编号跨{msn}个SN/{mday}个日期(是批次码非受理号)"
        elif ur >= 95:
            verdict, reason = "✅可用", "一码一行, 可作受理号"
        elif ur >= 80:
            verdict, reason = "△慎用", f"唯一率{ur:.1f}%"
        else:
            verdict, reason = "不可用", f"唯一率低{ur:.1f}%"

        out01.append([p, n, nw, f"{fill:.1f}%", uniq, f"{ur:.1f}%", msn, mday,
                      ",".join(f"{L}位×{c}" for L, c in lens.most_common(3)) or "-",
                      verdict, reason])

    w(f"{OUT}/01-渠道编号可靠性.csv",
      ["渠道", "总行数", "有编号行数", "填充率", "唯一编号数", "唯一率",
       "编号最大跨SN数", "编号最大跨日期数", "编号长度分布", "可用性判定", "判定理由"],
      out01)

    # ═══════════════════════════════════════════════════════
    # 提取重复组（315）
    # ═══════════════════════════════════════════════════════
    det = db.execute(text("""
        SELECT id, device_sn,
               (complaint_date AT TIME ZONE 'Asia/Shanghai')::date AS d,
               btrim(coalesce(complaint_no,'')) AS no,
               coalesce(platform,'') AS p,
               coalesce(complainant_phone,'') AS ph,
               coalesce(problem,'') AS prob,
               coalesce(result,'') AS res
        FROM external_complaints
        ORDER BY device_sn, d, id
    """)).all()

    # 各渠道编号是否可靠
    ok_channels = {r[0] for r in out01 if r[9] == "✅可用"}

    groups: dict[tuple, list] = defaultdict(list)
    for r in det:
        if r[1] and str(r[1]).strip() and r[2]:
            groups[(r[1].strip(), r[2])].append(r)

    dup = {k: v for k, v in groups.items() if len(v) > 1}

    # 同编号分组（判断批次码 vs 受理号）
    # ⚠️ 判定要点: 批次码的特征是 **跨多个日期** 或 **跨多个 SN**。
    #    某些批次 SN 为空(跨SN=0) 但跨 20+ 个日期 → 同样是批次码。
    no_sns = defaultdict(set)
    no_days = defaultdict(set)
    for r in det:
        if r[3]:
            if r[1] and str(r[1]).strip():
                no_sns[r[3]].add(str(r[1]).strip())
            if r[2]:
                no_days[r[3]].add(r[2])
    # 批次码 = 跨 >3 个 SN 或 跨 >3 个日期
    batch_nos = {k for k in no_sns.keys() | no_days.keys()
                 if len(no_sns.get(k, set())) > 3 or len(no_days.get(k, set())) > 3}

    out02, out03 = [], []
    for k, v in dup.items():
        nos = [x[3] for x in v]
        present = [n for n in nos if n]
        chans = list(dict.fromkeys(x[4] or "(空)" for x in v))
        phones = list(dict.fromkeys(x[5] for x in v if x[5]))
        ids = [x[0] for x in v]

        all_ok_ch = all((x[4] in ok_channels) for x in v)
        same_no = len(set(present)) == 1 if present else False
        is_batch = any(n in batch_nos for n in present)

        if present and same_no and not is_batch and all_ok_ch:
            verdict, reason = "★确定重复", "编号相同且渠道编号可靠 → 可归并"
            target = out02
        elif present and not same_no:
            verdict, reason = "☆两笔独立", "渠道给出不同编号 → 是两笔独立受理"
            target = out03
        elif not present:
            verdict, reason = "⚠无编号", "该渠道无编号 → 需人工判定"
            target = out03
        elif is_batch:
            verdict, reason = "⚠批次码", "编号是公用批次码(跨多SN) → 无区分力, 需人工"
            target = out03
        else:
            verdict, reason = "⚠部分有编号", "一个有一个无 → 需人工判定"
            target = out03

        rec = [
            k[0], k[1], len(v), " | ".join(chans), len(chans),
            " | ".join(phones) or "(空)",
            " | ".join(present) or "(全空)",
            " | ".join(str(i) for i in ids),
            "是" if len(chans) > 1 else "否",
            verdict, reason,
            " ／ ".join((x[6] or "")[:60].replace("\n", " ") for x in v),
        ]
        target.append(rec)

    HDR = ["设备SN", "投诉日期", "记录数", "渠道", "渠道数", "电话",
           "投诉编号", "记录ID", "是否多渠道", "判定", "判定理由", "内容摘要"]

    out02.sort(key=lambda x: x[0])
    out03.sort(key=lambda x: (x[9], x[0]))
    w(f"{OUT}/02-确定重复组(编号相同).csv", HDR, out02)
    w(f"{OUT}/03-待人工判定组.csv", HDR, out03)

    # ═══════════════════════════════════════════════════════
    # 04 公用批次码清单
    # ═══════════════════════════════════════════════════════
    no_rows = defaultdict(list)
    for r in det:
        if r[3]:
            no_rows[r[3]].append(r)
    out04 = []
    for no, v in sorted(no_rows.items(), key=lambda x: -len(x[1])):
        sns = {x[1] for x in v if x[1]}
        days = {x[2] for x in v if x[2]}
        # 受理号: 单 SN 单日期(或极少数) —— 保留原行才算
        if len(sns) <= 3 and len(days) <= 3:
            continue
        chans = Counter(x[4] or "(空)" for x in v)
        is_batch = len(sns) > 3 or len(days) > 3
        out04.append([
            no, len(no), len(v), len(sns), len(days),
            " | ".join(f"{c}×{n}" for c, n in chans.most_common(3)),
            " | ".join(str(x) for x in sorted(sns)[:5]) or "(该批SN全为空)",
            "是" if is_batch else "否",
            ("上游把导出批次码写进了受理号字段"
             + (f"（跨{len(days)}个日期但其SN为空）" if not sns else ""))
            if is_batch else "",
        ])
    w(f"{OUT}/04-公用批次码清单.csv",
      ["编号", "编号长度", "涉及行数", "跨SN数", "跨日期数", "渠道分布",
       "涉及的SN(前5)", "疑似批次码", "说明"], out04)

    # ═══════════════════════════════════════════════════════
    # 05 卡商重复组
    # ═══════════════════════════════════════════════════════
    cdet = db.execute(text("""
        SELECT id, device_sn,
               (complaint_date AT TIME ZONE 'Asia/Shanghai')::date AS d,
               coalesce(complaint_group,'') AS g,
               coalesce(complainant_phone,'') AS ph,
               coalesce(problem,'') AS prob
        FROM card_complaints
        ORDER BY device_sn, d, id
    """)).all()
    cg: dict[tuple, list] = defaultdict(list)
    for r in cdet:
        if r[1] and str(r[1]).strip() and r[2]:
            cg[(r[1].strip(), r[2])].append(r)
    cdup = {k: v for k, v in cg.items() if len(v) > 1}
    out05 = []
    for k, v in sorted(cdup.items()):
        chans = list(dict.fromkeys(x[3] or "(空)" for x in v))
        phones = list(dict.fromkeys(x[4] for x in v if x[4]))
        out05.append([
            k[0], k[1], len(v), " | ".join(chans), len(chans),
            " | ".join(phones) or "(空)",
            " | ".join(str(x[0]) for x in v),
            "是" if len(chans) > 1 else "否",
            "⚠无编号", "卡商表无 complaint_no 字段 → 只能靠(SN,日期)或人工",
            " ／ ".join((x[5] or "")[:60].replace("\n", " ") for x in v),
        ])
    w(f"{OUT}/05-卡商重复组.csv", HDR, out05)

    # ═══════════════════════════════════════════════════════
    # 汇总
    # ═══════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print("汇总")
    print(f"{'='*70}")
    print(f"  315 表: {len(det)} 行")
    print(f"     同(SN,日期)重复组 : {len(dup)} 组")
    print(f"       ├ 确定重复(编号同): {len(out02)} 组")
    print(f"       └ 待人工判定      : {len(out03)} 组")
    print(f"     公用批次码        : {len(out04)} 个编号")
    print(f"  卡商表: {len(cdet)} 行, 重复组 {len(cdup)} 组")
    print(f"\n  输出目录: {OUT}")
finally:
    db.close()

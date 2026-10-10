#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""唯一索引重复扫描 —— 薄封装治理框架 D1 探测器（工单 #162）

本脚本不再自含解析/判据/脱敏逻辑（曾有两套，必然漂移），全部**复用**
`app/services/governance_detector.py` 的 D1 探测器：

    scan   列出候选索引 + 真重复（排除整组全 NULL 组），打印每个 finding 的
           metrics / 脱敏样本 / repro_sql
    --triage  逐个 finding 打印样本明细（脱敏），便于研判「哪些行、什么形态」

两条铁律（详见 service 模块文件头，由 #161/#160 两轮真实事故验证）：
  ① 逐字符复现索引真实表达式（含 WHERE 部分条件 / date() / COALESCE）——防误报
  ② 重复 = NULL 视为相等，但排除「整组全 NULL」——防漏报 wallet 这类真问题、
     又不错把「业务允许多个 NULL」当问题

用法
----
    # 扫描（纯探测, 不落库）
    python scripts/governance_scan_unique_dup.py [--triage]

容器内只读排查：
    docker cp scripts/governance_scan_unique_dup.py csctalon-backend:/tmp/ && \
    docker exec -w /app -e PYTHONPATH=/app csctalon-backend python /tmp/governance_scan_unique_dup.py
"""

import argparse
import sys

from app.core.database import SessionLocal


def main() -> int:
    ap = argparse.ArgumentParser(description="唯一索引重复扫描（复用治理框架 D1 探测器）")
    ap.add_argument("--triage", action="store_true",
                    help="详细模式：打印每个 finding 的脱敏样本明细")
    args = ap.parse_args()

    from app.services.governance_detector import run_d1_scan

    db = SessionLocal()
    try:
        res = run_d1_scan(db, persist=False)
        print(f"候选含可空列/表达式的唯一索引: {res['scanned']} 个 |"
              f" 存在真重复: {res['found']} 个\n")

        for f in res["findings"]:
            m = f["metrics"]
            print(f"· {f['table_name']} / {f['target']}"
                  f"  [severity={f['severity_raw']} kind={m['gap_kind']}]")
            print(f"    索引列: {', '.join(m['index_columns'])}"
                  + (f"  WHERE {m['index_where']}" if m["index_where"] else ""))
            print(f"    指标: {m['dup_groups']} 组 / {m['redundant_rows']} 冗余行"
                  f" / 覆盖 {m['total_rows']} 行 / 占比 {m['ratio']*100:.1f}%")
            if args.triage:
                print(f"    样本({len(f['samples'])}):")
                for s in f["samples"]:
                    dup = s.pop("_dup_count")
                    ids = s.pop("_sample_ids", None)
                    print(f"      {s}  ×{dup}"
                          + (f"  ids={ids[:3]}..." if ids else ""))
            else:
                print(f"    样本数: {len(f['samples'])} (加 --triage 看明细)")
            print(f"    repro_sql:\n{f['repro_sql']}\n")

        if not res["findings"]:
            print("✅ 无真重复（含可空列的唯一索引均已 NND/部分索引防护，或重复被业务允许）")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""外部投诉「重复导入 vs 同源判重」分离核查 (2026-09-09) — 只读

用户发现: 无SN退化分支"内容都不同 手机号相同"却合并很多 → 核查根因 =
**同一记录被重复导入**(同手机号+problem逐字相同, dedup_hash 因日期缺失/批次差异
没拦住, 造成物理重复行)。这不是"多渠道同源投诉", 是数据质量问题, 应单独治理。

本脚本:
  1. 找出全库"逐字重复导入"组(同手机号+problem完全相同, ≥2行)
  2. 区分: 有SN vs 无SN; 看 complaint_date 是否丢失
  3. 导出"重复导入治理清单"(供后续质量治理标 dup_residual)
  4. 说明: 这些应从同源判重中排除(不是多渠道同源)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
REPO_ROOT = os.path.dirname(BACKEND_DIR)
sys.path.insert(0, BACKEND_DIR)

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models.external_complaint import ExternalComplaint  # noqa: E402

TZ_CN = ZoneInfo("Asia/Shanghai")


def _sh(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_CN).strftime("%Y-%m-%d %H:%M")


def main() -> None:
    with SessionLocal() as db:
        rows = db.scalars(
            select(ExternalComplaint)
            .where(ExternalComplaint.id < 300000)
            .order_by(ExternalComplaint.id)
        ).all()
    print(f"真实批次行数: {len(rows)}")

    # 按 (手机号, problem) 分组找逐字重复
    from collections import defaultdict
    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        if r.complainant_phone and r.problem:
            groups[(r.complainant_phone.strip(), r.problem)].append(r)

    dup_groups = {k: v for k, v in groups.items() if len(v) >= 2}
    print(f"\n逐字重复导入组(同手机号+problem完全同): {len(dup_groups)} 组 / {sum(len(v) for v in dup_groups.values())} 行")

    # 统计特征
    no_sn_groups = 0
    with_null_date = 0
    for v in dup_groups.values():
        if all(not r.device_sn for r in v):
            no_sn_groups += 1
        if any(r.complaint_date is None for r in v):
            with_null_date += 1
    print(f"其中 全无SN 组: {no_sn_groups}; 含 complaint_date=空 的组: {with_null_date}")

    # 导出治理清单
    import pandas as pd
    out_path = os.path.join(REPO_ROOT, "data", "外部投诉重复导入治理清单_20260909.xlsx")
    out_rows = []
    for (phone, problem), members in sorted(dup_groups.items(), key=lambda x: -len(x[1])):
        for r in members:
            out_rows.append({
                "手机号": "'" + phone,
                "投诉内容(全文)": problem,
                "记录ID": r.id,
                "投诉编号": r.complaint_no or "",
                "渠道": r.platform or "",
                "SN": r.device_sn or "",
                "投诉人": r.complainant or "",
                "登记日期": _sh(r.complaint_date)[:10] or "(空)",
                "入库时间": _sh(r.created_at),
                "结果": r.result or "",
                "关联工单": r.matched_ticket_no or "",
                "组内行数": len(members),
                "是否无SN": "是" if not r.device_sn else "否",
                "建议": "重复导入, 保留最优行其余标 dup_residual",
            })
    df = pd.DataFrame(out_rows)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="重复导入治理")
        ws = writer.sheets["重复导入治理"]
        for col_idx, col in enumerate(df.columns, 1):
            width = min(70, max(10, int(df[col].astype(str).str.len().max() * 1.1)))
            ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = width
    print(f"✅ 已导出治理清单: {out_path} ({len(out_rows)} 行)")

    # 终端抽样
    print("\n抽样(前15组):")
    for (phone, problem), members in list(sorted(dup_groups.items(), key=lambda x: -len(x[1])))[:15]:
        sns = ",".join(sorted({(r.device_sn or '-') for r in members}))
        dates = ",".join(_sh(r.complaint_date)[:10] or '(空)' for r in members)
        print(f"  {phone} x{len(members)} SN=[{sns}] date=[{dates}] | {(problem or '')[:30]}")


if __name__ == "__main__":
    main()

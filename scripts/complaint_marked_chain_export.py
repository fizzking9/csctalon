#!/usr/bin/env python3
"""外部投诉「客服姓名括弧标注(N)」同源链核查 (2026-09-09) — 只读

目的: 客服在 complainant 姓名后标注 (N) 表示该投诉人第 N 次投诉/登记。
      本脚本按"手机号(主) + SN(辅)"把这些标注行的完整链条拉出来,
      验证: 标注数字是否自洽递增、是否跨渠道、是否与无标注的原始行同链。
      输出 Excel 供人工核对: 每个"疑似同源链"= 同一投诉人在系统里的全部登记。
"""
from __future__ import annotations

import os
import re
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
_PAREN_MARK = re.compile(r"[（(]\s*(\d+)\s*[)）]")
_DONE = {"和解结案", "撤销结案", "暂行办结", "调解结案", "调节结案", "投诉无效", "重复投诉", "终止调节", "退回消委会"}


def _sh(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_CN).strftime("%Y-%m-%d %H:%M")


def _mark_no(name: str | None) -> int | None:
    """提取姓名中的 (N): 王杰（2）→2; 无标注→None"""
    if not name:
        return None
    m = _PAREN_MARK.search(str(name))
    return int(m.group(1)) if m else None


def _base_name(name: str | None) -> str:
    """去 (N) 标注后的基准姓名"""
    if not name:
        return ""
    return _PAREN_MARK.sub("", str(name)).strip()


def main() -> None:
    with SessionLocal() as db:
        rows = db.scalars(
            select(ExternalComplaint)
            .where(ExternalComplaint.id < 300000)
            .order_by(ExternalComplaint.complaint_date)
        ).all()

    # 找到所有"带标注"的手机号
    marked_phones: set[str] = set()
    for r in rows:
        if _mark_no(r.complainant) is not None and r.complainant_phone:
            marked_phones.add(r.complainant_phone.strip())

    # 按手机号聚合, 只保留"含标注"的手机号链条
    by_phone: dict[str, list] = {}
    for r in rows:
        if r.complainant_phone and r.complainant_phone.strip() in marked_phones:
            by_phone.setdefault(r.complainant_phone.strip(), []).append(r)

    print(f"含客服标注(姓名+N)的手机号链条: {len(by_phone)} 组, 涉及行数 {sum(len(v) for v in by_phone.values())}")

    # 每条链: 组内行按日期排, 标注数字是否递增/跨渠道
    chains: list[dict] = []
    for ph, lst in by_phone.items():
        lst.sort(key=lambda x: (x.complaint_date or datetime.min, x.id))
        has_multi_mark = sum(1 for r in lst if _mark_no(r.complainant) is not None) >= 1
        if not has_multi_mark:
            continue
        chains.append({"phone": ph, "members": lst})

    # 输出 Excel
    import pandas as pd
    out_dir = os.path.join(REPO_ROOT, "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "外部投诉客服标注同源链_20260909.xlsx")

    rows_out = []
    for g in chains:
        # 组内是否跨渠道 / 跨日期 / 标注数自洽
        plats = {r.platform or "" for r in g["members"]}
        marks = [_mark_no(r.complainant) for r in g["members"]]
        has_mark = [m for m in marks if m is not None]
        unmarked_cnt = sum(1 for m in marks if m is None)
        for r in g["members"]:
            mn = _mark_no(r.complainant)
            rows_out.append({
                "手机号": "'" + g["phone"],
                "基准姓名": _base_name(r.complainant),
                "标注序号": mn if mn is not None else "(无标注)",
                "投诉编号": r.complaint_no or "",
                "渠道": r.platform or "",
                "登记日期": _sh(r.complaint_date)[:10],
                "投诉人原文": r.complainant or "",
                "SN": r.device_sn or "",
                "结果": r.result or "",
                "处理时间": _sh(r.handle_time),
                "关联工单": r.matched_ticket_no or "",
                "记录ID": r.id,
                "内容": (r.problem or r.handle_detail or "")[:50],
                "组内渠道数": len(plats),
                "组内标注最大": max(has_mark) if has_mark else "",
                "组内无标注行数": unmarked_cnt,
                "是否跨渠道": "是" if len(plats) > 1 else "否",
            })
    df = pd.DataFrame(rows_out)
    df.insert(0, "你的判定(是否同源链)", "")
    df.insert(0, "链序号", "")
    # 链序号 = 按手机号出现的顺序
    chain_idx = {}
    for i, g in enumerate(chains, 1):
        chain_idx[g["phone"]] = i
    df["链序号"] = df["手机号"].map(lambda p: chain_idx.get(p[1:], ""))

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="客服标注同源链")
        ws = writer.sheets["客服标注同源链"]
        for col_idx, col in enumerate(df.columns, 1):
            width = min(60, max(10, int(df[col].astype(str).str.len().max() * 1.1)))
            ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = width
    print(f"✅ 已导出: {out_path}  (组数 {len(chains)}, 行数 {len(rows_out)})")

    # 终端摘要: 打印几组样例看标注是否自洽
    print("\n样例链条(前 8 组):")
    for g in chains[:8]:
        ph = g["phone"]
        print(f"\n▶ 手机号 {ph}  行数 {len(g['members'])}")
        for r in g["members"]:
            mn = _mark_no(r.complainant)
            print(f"   [{mn if mn is not None else '无标注':>4}] {_sh(r.complaint_date)[:10]} "
                  f"{r.platform or '(空)':<10} {_base_name(r.complainant)[:8]:<8} "
                  f"SN={r.device_sn or '-':<15} {r.result or '':<6} id={r.id} "
                  f"{(r.problem or r.handle_detail or '')[:18]}")


if __name__ == "__main__":
    main()

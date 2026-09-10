#!/usr/bin/env python3
"""外部投诉「同源判重」v3 规则原型 (2026-09-09) — 只读

用户最终口径(2026-09-09):
  同源(重复登记) = 同SN(设备) + 内容高度相似 + 时间跨度不久 → 可合并(同时关闭)
  - 渠道不同 = 是"重复投诉"的特征(同件事多渠道), 但仅渠道不同不够
  - 手机号/姓名 = 参考/区分辅助, 非主判据
  - 同SN但内容不同/跨久/不同诉求 = 不同投诉(徐良例 不合并)
  - 不同SN即便同人 = 不同投诉(胡阳例 不合并)

v3 规则:
  主键: 同 device_sn(双方都有SN)
  条件: 同SN 且 内容相似度>=0.7 且 日期差<=30天 → 同源
  无SN退化: 同手机号 且 内容相似度>=0.8 且 同工单(或日期差<=14) → 同源
  辅助线索(仅标注, 不单独判同源): 客服(N)标注 / result=重复投诉 / 渠道不同 / 同工单
  输出: 同源组 + 各组证据, 供人工核对
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
REPO_ROOT = os.path.dirname(BACKEND_DIR)
sys.path.insert(0, BACKEND_DIR)

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models.external_complaint import ExternalComplaint  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from complaint_same_source_proto import _CHANNEL_CLUSTER, _cluster, _ratio  # noqa: E402

TZ_CN = ZoneInfo("Asia/Shanghai")
_PAREN_MARK = re.compile(r"[（(]\s*\d+\s*[)）]")
_RESULT_DUP = {"重复投诉"}


def _sh(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_CN).strftime("%Y-%m-%d")


def _mark_present(name: str | None) -> bool:
    return bool(name) and bool(_PAREN_MARK.search(str(name)))


def _full(r) -> str:
    return r.problem or r.handle_detail or ""


def main() -> None:
    with SessionLocal() as db:
        rows = db.scalars(
            select(ExternalComplaint)
            .where(ExternalComplaint.id < 300000)
            .where(ExternalComplaint.complaint_date.isnot(None))
            .order_by(ExternalComplaint.complaint_date)
        ).all()
    print(f"真实批次行数: {len(rows)}")

    by_sn: dict[str, list] = {}
    by_phone: dict[str, list] = {}
    for r in rows:
        if r.device_sn:
            by_sn.setdefault(r.device_sn.strip(), []).append(r)
        if r.complainant_phone:
            by_phone.setdefault(r.complainant_phone.strip(), []).append(r)

    hits: list[dict] = []
    seen: set[tuple[int, int]] = set()

    def _add(a, b, reason: str, sim: float, key_kind: str, score: int):
        pa = (a.id, b.id) if a.id < b.id else (b.id, a.id)
        if pa in seen:
            return
        seen.add(pa)
        ca, cb = _cluster(a.platform), _cluster(b.platform)
        hits.append({
            "id_a": a.id, "id_b": b.id, "score": score,
            "phone": a.complainant_phone or b.complainant_phone,
            "name_a": a.complainant or "", "name_b": b.complainant or "",
            "sn": a.device_sn or b.device_sn,
            "plat_a": a.platform or "", "plat_b": b.platform or "",
            "date_a": _sh(a.complaint_date), "date_b": _sh(b.complaint_date),
            "daydiff": abs((a.complaint_date - b.complaint_date).days),
            "sim": round(sim, 3),
            "result_a": a.result or "", "result_b": b.result or "",
            "ticket_a": a.matched_ticket_no or "", "ticket_b": b.matched_ticket_no or "",
            "cross_channel": ca != cb,
            "mark_a": _mark_present(a.complainant), "mark_b": _mark_present(b.complainant),
            "reason": reason,
            "content_a": (_full(a) or "")[:32], "content_b": (_full(b) or "")[:32],
            "key_kind": key_kind,
        })

    # ── 1) 主判: 同 SN ──
    for sn, lst in by_sn.items():
        if len(lst) < 2:
            continue
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                a, b = lst[i], lst[j]
                sim = _ratio(_full(a), _full(b))
                daydiff = abs((a.complaint_date - b.complaint_date).days)
                # 同SN + 内容高相似 + 时间近(30天内, 宽松些)
                if sim >= 0.7 and daydiff <= 30:
                    clues = []
                    if (a.result or "") in _RESULT_DUP or (b.result or "") in _RESULT_DUP:
                        clues.append("result=重复投诉")
                    if _mark_present(a.complainant) or _mark_present(b.complainant):
                        clues.append("客服标注(N)")
                    if _cluster(a.platform) != _cluster(b.platform):
                        clues.append("跨渠道")
                    if a.matched_ticket_no and a.matched_ticket_no == b.matched_ticket_no:
                        clues.append("同工单")
                    reason = f"同SN+内容高相似{sim:.2f}+{daydiff}天" + (f"+{'/'.join(clues)}" if clues else "")
                    _add(a, b, reason, sim, "同SN", 100 + len(clues) * 5)

    # ── 2) 无SN退化: 同手机号 + 高相似 + 同工单 且 双方都无SN ──
    for phone, lst in by_phone.items():
        lst2 = [r for r in lst if not r.device_sn]
        if len(lst2) < 2:
            continue
        for i in range(len(lst2)):
            for j in range(i + 1, len(lst2)):
                a, b = lst2[i], lst2[j]
                sim = _ratio(_full(a), _full(b))
                daydiff = abs((a.complaint_date - b.complaint_date).days)
                same_ticket = bool(a.matched_ticket_no and a.matched_ticket_no == b.matched_ticket_no)
                if sim >= 0.8 and (same_ticket or daydiff <= 14):
                    reason = f"无SN+同手机号+内容高相似{sim:.2f}" + ("+同工单" if same_ticket else f"+{daydiff}天")
                    _add(a, b, reason, sim, "无SN-同手机号", 90)

    hits.sort(key=lambda h: (-h["score"], h["sim"]))
    print(f"\nv3 同源候选: {len(hits)} 对")

    # 输出
    print(f"\n{'score':>5} {'差':>3} {'sim':>5} {'SN':<16}  理由")
    print("-" * 130)
    for h in hits:
        print(f"{h['score']:>5} {h['daydiff']:>3} {h['sim']:>5.2f}  {(h['sn'] or '')[:16]:<16} "
              f"{h['name_a'][:6]}[{h['plat_a'][:5]}]↔{h['name_b'][:6]}[{h['plat_b'][:5]}] "
              f"{h['reason'][:58]}")

    # 分档: 主判同SN(100+) vs 退化(90)
    sn_auto = [h for h in hits if h["key_kind"] == "同SN"]
    phone_fallback = [h for h in hits if h["key_kind"] != "同SN"]
    print(f"\n同SN(主判): {len(sn_auto)} 对;  无SN退化: {len(phone_fallback)} 对")

    # 导出 Excel
    import pandas as pd
    out_path = os.path.join(REPO_ROOT, "data", "外部投诉同源候选_v3_20260909.xlsx")
    rows_out = []
    for i, h in enumerate(hits, 1):
        rows_out.append({
            "序号": i, "判定依据": h["key_kind"], "证据分": h["score"],
            "内容相似": h["sim"], "日期差": h["daydiff"], "SN": h["sn"],
            "命中理由": h["reason"], "是否跨渠道": "是" if h["cross_channel"] else "否",
            "客服标注": ("A" if h["mark_a"] else "") + ("B" if h["mark_b"] else ""),
            "A_记录ID": h["id_a"], "A_姓名": h["name_a"], "A_渠道": h["plat_a"],
            "A_登记": h["date_a"], "A_结果": h["result_a"], "A_工单": h["ticket_a"],
            "A_内容": h["content_a"],
            "B_记录ID": h["id_b"], "B_姓名": h["name_b"], "B_渠道": h["plat_b"],
            "B_登记": h["date_b"], "B_结果": h["result_b"], "B_工单": h["ticket_b"],
            "B_内容": h["content_b"],
            "你的判定": "", "备注": "",
        })
    df = pd.DataFrame(rows_out)
    df.insert(0, "是否同源(人工填)", "")
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="v3同源候选(同SN)")
        ws = writer.sheets["v3同源候选(同SN)"]
        for col_idx, col in enumerate(df.columns, 1):
            width = min(60, max(8, int(df[col].astype(str).str.len().max() * 1.1)))
            ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = width
    print(f"✅ 已导出: {out_path} ({len(rows_out)} 行)")


if __name__ == "__main__":
    main()

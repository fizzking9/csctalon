#!/usr/bin/env python3
"""外部投诉「同源判重」v2 规则原型 (2026-09-09) — 只读

v1 教训: 纯内容字符相似对"客服标注同源组"召回率仅 37% —— 客服标注 (N) 的同源人,
         内容措辞常完全不同(诉求同但描述变体大)。判重主信号应为客服登记习惯:
         同手机号/SN + 归一化姓名 + 客服标注(N) + result=重复投诉 + 内容含重复词。

v2 规则(证据打分, 阈值判定):
  候选键: 同 complainant_phone(主) 或 同 device_sn(辅, 双方无手机号时)
  对每个候选对(日期差≤14天), 计算证据分:
    S1 客服姓名标注(N): 两行去标注后基准姓名相同 且 至少一行带(N)        → +60(强)
    S2 result='重复投诉'(任一)                                          → +80(极强)
    S3 内容含 重复/再次投诉/重新投诉(任一)                               → +40
    S4 同关联工单 matched_ticket_no(相同非空)                           → +50(同一工单=同源铁证)
    S5 内容字符相似度 sim≥0.8                                           → +50; 0.6~0.8 → +25
    S6 跨渠道簇(真多渠道)                                               → +10
  判定: 总分 ≥ 60 → 同源候选(高置信/中置信分档); ≥30 且 跨渠道 → 人工复核候选
  输出: 命中列表 + 与客服标注真值对比召回率
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
_DUP_WORD = re.compile(r"重复投诉|再次投诉|重新投诉|重复")
_RESULT_DUP = {"重复投诉"}
_DONE = {"和解结案", "撤销结案", "暂行办结", "调解结案", "调节结案", "投诉无效", "重复投诉", "终止调节", "退回消委会"}


def _sh(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_CN).strftime("%Y-%m-%d")


def _mark_present(name: str | None) -> bool:
    return bool(name) and bool(_PAREN_MARK.search(str(name)))


def _base_name(name: str | None) -> str:
    return _PAREN_MARK.sub("", str(name or "")).strip()


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

    by_phone: dict[str, list] = {}
    by_sn: dict[str, list] = {}
    for r in rows:
        if r.complainant_phone:
            by_phone.setdefault(r.complainant_phone.strip(), []).append(r)
        if r.device_sn:
            by_sn.setdefault(r.device_sn.strip(), []).append(r)

    def _key(r) -> str | None:
        return (r.complainant_phone or "").strip() or None

    hits: list[dict] = []
    seen: set[tuple[int, int]] = set()

    def _consider(a, b, key_kind: str):
        pa, pb = (a.id, b.id) if a.id < b.id else (b.id, a.id)
        if pa in seen:
            return
        daydiff = abs((a.complaint_date - b.complaint_date).days)
        if daydiff > 14:
            return
        score = 0
        reasons: list[str] = []
        # S1 客服姓名标注
        ba, bb = _base_name(a.complainant), _base_name(b.complainant)
        if ba and bb and ba == bb and (_mark_present(a.complainant) or _mark_present(b.complainant)):
            score += 60
            reasons.append("客服标注(N)同名")
        elif ba and bb and ba == bb:
            score += 20
            reasons.append("同名(无标注)")
        # S2 result 重复投诉
        if (a.result or "") in _RESULT_DUP or (b.result or "") in _RESULT_DUP:
            score += 80
            reasons.append("result=重复投诉")
        # S3 内容含重复词
        if _DUP_WORD.search(_full(a) or "") or _DUP_WORD.search(_full(b) or ""):
            score += 40
            reasons.append("内容含重复词")
        # S4 同关联工单
        ta, tb = a.matched_ticket_no, b.matched_ticket_no
        if ta and tb and ta == tb:
            score += 50
            reasons.append(f"同工单{ta}")
        # S5 内容相似
        sim = _ratio(_full(a), _full(b))
        if sim >= 0.8:
            score += 50
            reasons.append(f"内容高相似{sim:.2f}")
        elif sim >= 0.6:
            score += 25
            reasons.append(f"内容中相似{sim:.2f}")
        # S6 跨渠道
        ca, cb = _cluster(a.platform), _cluster(b.platform)
        cross = ca != cb
        if cross:
            score += 10
            reasons.append("跨渠道簇")
        if score >= 60:
            seen.add(pa)
            hits.append({
                "id_a": a.id, "id_b": b.id, "score": score,
                "phone": a.complainant_phone or b.complainant_phone,
                "name_a": a.complainant or "", "name_b": b.complainant or "",
                "plat_a": a.platform or "", "plat_b": b.platform or "",
                "sn_a": a.device_sn, "sn_b": b.device_sn,
                "date_a": _sh(a.complaint_date), "date_b": _sh(b.complaint_date),
                "daydiff": daydiff, "sim": round(sim, 3),
                "result_a": a.result or "", "result_b": b.result or "",
                "ticket_a": ta or "", "ticket_b": tb or "",
                "cross": cross, "reasons": "+".join(reasons),
                "content_a": (_full(a) or "")[:32], "content_b": (_full(b) or "")[:32],
                "key_kind": key_kind,
            })

    for phone, lst in by_phone.items():
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                _consider(lst[i], lst[j], "phone")
    for sn, lst in by_sn.items():
        lst2 = [r for r in lst if not _key(r)]
        for i in range(len(lst2)):
            for j in range(i + 1, len(lst2)):
                _consider(lst2[i], lst2[j], "sn_only")

    # ── 与客服标注真值对比召回 ──
    # 真值 = 同手机号 且 至少一行带(N)标注 且 ≤14天(近似"客服认为相关"的同源对)
    marked_pairs = 0
    marked_hit = 0
    hit_ids = {(h["id_a"], h["id_b"]) for h in hits}
    for phone, lst in by_phone.items():
        has_mark = any(_mark_present(r.complainant) for r in lst)
        if not has_mark:
            continue
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                a, b = lst[i], lst[j]
                if abs((a.complaint_date - b.complaint_date).days) > 14:
                    continue
                marked_pairs += 1
                pa = (a.id, b.id) if a.id < b.id else (b.id, a.id)
                if pa in hit_ids:
                    marked_hit += 1

    hits.sort(key=lambda h: -h["score"])
    print(f"\nv2 规则命中: {len(hits)} 对")
    print(f"客服标注真值配对(≤14天): {marked_pairs}; 命中 {marked_hit} (召回 {marked_hit/marked_pairs*100:.0f}%)")
    print(f"(v1 内容相似召回为 37%)\n")

    print(f"{'score':>5} {'差':>3} {'sim':>5}  理由")
    print("-" * 120)
    for h in hits[:100]:
        print(f"{h['score']:>5} {h['daydiff']:>3} {h['sim']:>5.2f}  {h['reasons'][:60]:<60} "
              f"{h['name_a'][:8]} [{h['plat_a'][:6]}]↔{h['name_b'][:8]} [{h['plat_b'][:6]}] id{h['id_a']}/{h['id_b']}")

    # 分档
    auto = [h for h in hits if h["score"] >= 90]
    review = [h for h in hits if 60 <= h["score"] < 90]
    print(f"\n≥90 高置信: {len(auto)} 对;  60~90 中置信: {len(review)} 对")

    # 导出 Excel
    import pandas as pd
    out_path = os.path.join(REPO_ROOT, "data", "外部投诉同源候选_v2_20260909.xlsx")
    rows_out = []
    for i, h in enumerate(hits, 1):
        rows_out.append({
            "序号": i, "证据分": h["score"],
            "档位": "高置信" if h["score"] >= 90 else "中置信-需复核",
            "日期差": h["daydiff"], "内容相似": h["sim"], "命中理由": h["reasons"],
            "是否跨渠道": "是" if h["cross"] else "否",
            "A_记录ID": h["id_a"], "A_姓名": h["name_a"], "A_渠道": h["plat_a"],
            "A_登记": h["date_a"], "A_SN": h["sn_a"], "A_结果": h["result_a"],
            "A_工单": h["ticket_a"], "A_内容": h["content_a"],
            "B_记录ID": h["id_b"], "B_姓名": h["name_b"], "B_渠道": h["plat_b"],
            "B_登记": h["date_b"], "B_SN": h["sn_b"], "B_结果": h["result_b"],
            "B_工单": h["ticket_b"], "B_内容": h["content_b"],
            "你的判定": "", "备注": "",
        })
    df = pd.DataFrame(rows_out)
    df.insert(0, "是否同源(人工填)", "")
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="v2同源候选")
        ws = writer.sheets["v2同源候选"]
        for col_idx, col in enumerate(df.columns, 1):
            width = min(60, max(8, int(df[col].astype(str).str.len().max() * 1.1)))
            ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = width
    print(f"✅ 已导出: {out_path} ({len(rows_out)} 行)")


if __name__ == "__main__":
    main()

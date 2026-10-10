#!/usr/bin/env python3
"""外部投诉「同源判重」v5 FINAL 规则 (2026-09-09) — 只读

用户最终口径(全部拍板 + 无SN修正):
  同源(重复登记/可同时关闭) ⇔ 同 device_sn(设备) AND 内容高相似 AND 时间跨度不久
  - 渠道不同 = 重复投诉特征, 但单凭不够(须叠同SN+内容+时间)
  - 手机号/姓名仅参考(张帆↔刘佳奇 同SN同内容不同姓名 = 同源)
  - 不同SN即便同人 = 不同投诉(胡阳)
  - 同SN但不同诉求/跨久 = 不同投诉(徐良/梁先生)
  - ⚠️ 公共服务平台"再次来电/再次来电补充/投诉N次"会把前次原文内嵌在 problem
    (以 <br/> 或整段复制分隔) → 相似度被历史原文虚高 → 判为"疑似新事件/需人工",
    不自动并入 (梁先生/刘桂梅-新诉求类)
  - ✅ v5 修正: **去掉无SN退化分支**。同源判重必须有 SN(设备)作主键;
    无SN行缺锚点, 把"重复登记/重复导入"(同手机号+problem逐字同, 48组/97行)
    误当同源 → 一律不自动并(单列人工/质量治理)。

v5 规则:
  仅同SN: 同 device_sn + 内容相似>=0.7 + 日期差<=30天
      - 若任一行命中"内嵌前文/再次来电"特征(problem含'再次来电'或'<br/>'且>150字)
        → 降级为 NEED_REVIEW(人工), 不自动同源
      - 否则 → 同源(AUTO)
  无SN行: 不参与自动判重(无设备主键, 无法可靠区分同源/重复登记)
  输出分档: AUTO(自动同源) / NEED_REVIEW(需人工复核)
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
from complaint_same_source_proto import _cluster, _ratio  # noqa: E402

TZ_CN = ZoneInfo("Asia/Shanghai")
_PAREN_MARK = re.compile(r"[（(]\s*\d+\s*[)）]")
_EMBED_RE = re.compile(r"再次来电|再次来电补充|再次来电反馈|投诉\d+次|市民来电投诉")
_BR_TAG = "<br/>"


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


def _embed_suspect(r) -> bool:
    """判断是否为'内嵌前文'的再次来电(相似度会被历史原文虚高, 需人工)"""
    txt = _full(r) or ""
    if len(txt) < 150:
        return False
    has_repeat_word = bool(_EMBED_RE.search(txt))
    has_br = _BR_TAG in txt
    # 公共服务平台风格: 再次来电 + 超长 + 内嵌分段标记
    return has_repeat_word and (has_br or len(txt) > 250)


def main() -> None:
    with SessionLocal() as db:
        rows = db.scalars(
            select(ExternalComplaint)
            .where(ExternalComplaint.id < 300000)
            .where(ExternalComplaint.complaint_date.isnot(None))
            .order_by(ExternalComplaint.complaint_date)
        ).all()
    print(f"真实批次行数: {len(rows)}")

    # 仅同SN 分组(有SN是硬前提, 无SN不参与自动判重)
    by_sn: dict[str, list] = {}
    for r in rows:
        if r.device_sn:
            by_sn.setdefault(r.device_sn.strip(), []).append(r)
    sn_rows = sum(len(v) for v in by_sn.values())
    sn_groups_multi = sum(1 for v in by_sn.values() if len(v) >= 2)
    print(f"有SN行: {sn_rows}; 同SN多行组: {sn_groups_multi}")

    hits: list[dict] = []
    seen: set[tuple[int, int]] = set()

    def _add(a, b, sim, daydiff, reason, decision: str, key_kind: str):
        pa = (a.id, b.id) if a.id < b.id else (b.id, a.id)
        if pa in seen:
            return
        seen.add(pa)
        hits.append({
            "id_a": a.id, "id_b": b.id,
            "phone": a.complainant_phone or b.complainant_phone,
            "name_a": a.complainant or "", "name_b": b.complainant or "",
            "sn": a.device_sn or b.device_sn,
            "plat_a": a.platform or "", "plat_b": b.platform or "",
            "date_a": _sh(a.complaint_date), "date_b": _sh(b.complaint_date),
            "daydiff": daydiff, "sim": round(sim, 3),
            "result_a": a.result or "", "result_b": b.result or "",
            "ticket_a": a.matched_ticket_no or "", "ticket_b": b.matched_ticket_no or "",
            "cross_channel": _cluster(a.platform) != _cluster(b.platform),
            "mark_a": _mark_present(a.complainant), "mark_b": _mark_present(b.complainant),
            "embed_a": _embed_suspect(a), "embed_b": _embed_suspect(b),
            "reason": reason, "decision": decision, "key_kind": key_kind,
            "content_a": (_full(a) or "")[:40], "content_b": (_full(b) or "")[:40],
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
                if sim >= 0.7 and daydiff <= 30:
                    clues = []
                    if (a.result or "") == "重复投诉" or (b.result or "") == "重复投诉":
                        clues.append("result=重复投诉")
                    if _mark_present(a.complainant) or _mark_present(b.complainant):
                        clues.append("客服标注")
                    if _cluster(a.platform) != _cluster(b.platform):
                        clues.append("跨渠道")
                    if a.matched_ticket_no and a.matched_ticket_no == b.matched_ticket_no:
                        clues.append("同工单")
                    base_reason = f"同SN+相似{sim:.2f}+{daydiff}天" + (f"+{'/'.join(clues)}" if clues else "")
                    # 内嵌前文嫌疑 → 人工复核, 不自动并
                    if _embed_suspect(a) or _embed_suspect(b):
                        decision = "NEED_REVIEW"
                        reason = base_reason + " [⚠疑似再次来电内嵌前文→人工]"
                    else:
                        decision = "AUTO"
                        reason = base_reason
                    _add(a, b, sim, daydiff, reason, decision, "同SN")
    # ── 无SN行: 不参与自动判重(v5, 缺设备主键) ──

    hits.sort(key=lambda h: (h["decision"] != "AUTO", -h["sim"]))
    auto = [h for h in hits if h["decision"] == "AUTO"]
    review = [h for h in hits if h["decision"] == "NEED_REVIEW"]
    print(f"\n✅ 同SN同源候选: {len(hits)} 对 = 自动 {len(auto)} + 人工 {len(review)} (无SN行已排除)")

    print(f"\n{'决策':<11} {'差':>3} {'sim':>5} {'SN':<16} 双方 → 理由")
    print("-" * 140)
    for h in hits:
        d = "AUTO" if h["decision"] == "AUTO" else "人工"
        print(f"{d:<11} {h['daydiff']:>3} {h['sim']:>5.2f}  {(h['sn'] or '')[:16]:<16} "
              f"{h['name_a'][:6]}[{h['plat_a'][:5]}]↔{h['name_b'][:6]}[{h['plat_b'][:5]}] "
              f"{h['reason'][:60]}")

    # 导出 FINAL v5
    import pandas as pd
    out_path = os.path.join(REPO_ROOT, "data", "外部投诉同源FINAL_v5_20260909.xlsx")
    rows_out = []
    for i, h in enumerate(hits, 1):
        rows_out.append({
            "序号": i, "决策": "自动同源" if h["decision"] == "AUTO" else "需人工复核",
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
        dfa = df[df["决策"] == "自动同源"]
        dfr = df[df["决策"] == "需人工复核"]
        dfa.to_excel(writer, index=False, sheet_name="自动同源")
        dfr.to_excel(writer, index=False, sheet_name="需人工复核")
        for sheet, ddf in (("自动同源", dfa), ("需人工复核", dfr)):
            ws = writer.sheets[sheet]
            for col_idx, col in enumerate(ddf.columns, 1):
                width = min(60, max(8, int(ddf[col].astype(str).str.len().max() * 1.1)))
                ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = width
    print(f"✅ 已导出 FINAL v5(仅同SN): {out_path} (自动 {len(dfa)} + 人工 {len(dfr)})")


if __name__ == "__main__":
    main()

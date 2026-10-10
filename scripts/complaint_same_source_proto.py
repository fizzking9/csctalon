#!/usr/bin/env python3
"""外部投诉「同源判重」规则原型 (2026-09-09) — 只读, 不改数据

用途: 按用户口径(同手机号/SN + 内容相似 + ≤14天 + 渠道簇) 找出"同一投诉多渠道/多次登记"
      的候选分组, 输出供人工核对准确率, 为后续合并核算功能提供依据。

运行方式 (在 backend 目录下, 用项目 venv):
  cd /Users/tanzk/dev/csctalon/backend && ./.venv/bin/python ../scripts/complaint_same_source_proto.py

规则:
  1. 仅 external_complaints 真实主批次(id<300000, 排除疑似脏副本)
  2. 关联键: complainant_phone 非空用手机号; 否则退化用 device_sn
  3. 时间窗: 两行 complaint_date 差 ≤ 14 天
  4. 渠道簇: 市监局各监所同簇; 全国12315/消协同簇; 公共服务平台/消委会各成簇
  5. 内容相似: 归一化(去手机号/SN/标点/日期数字)+字符n-gram Dice + 关键词命中
  6. 判定阈值: 跨簇(Dice≥0.5) 或 同簇(Dice≥0.65) → 候选同源
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

TZ_CN = ZoneInfo("Asia/Shanghai")
_DONE_RESULTS = {
    "和解结案", "撤销结案", "暂行办结", "调解结案", "调节结案",
    "投诉无效", "重复投诉", "终止调节", "退回消委会",
}

# ── 渠道簇(用户 2026-09-09 补充: 市监局可能多个渠道主体/不同监管所) ──
_CHANNEL_CLUSTER: dict[str, str] = {
    # 市监局簇(不同监所视为同一监管主体)
    "航城监所": "市监局", "宝安市监局": "市监局", "南沙市监局": "市监局",
    "天河市监局": "市监局", "武汉市监局": "市监局",
    # 12315/消协簇
    "全国12315平台": "12315/消协", "全国消协智慧315平台": "12315/消协",
    "全国消协315": "12315/消协",
    # 各自独立
    "公共服务平台": "公共服务平台", "消委会": "消委会",
    "投诉处理内部群": "内部",
}

# 归一化: 去手机号/SN(长数字串)/标点/空白/常见日期
_MSK_NOISE = re.compile(r"(1[3-9]\d{9})|(\d{6,})|[^\u4e00-\u9fa5a-zA-Z]")
_STOP_KW = {"投诉", "举报", "本人", "消费者", "商家", "购买", "该公司", "深圳市", "有限公司", "反映", "来电", "情况"}


def _cluster(platform: str | None) -> str:
    return _CHANNEL_CLUSTER.get((platform or "").strip(), "其他")


def _norm(text: str | None) -> str:
    """归一化投诉内容: 去手机号/SN/标点/空白/停用词, 保中文主体"""
    if not text:
        return ""
    t = _MSK_NOISE.sub("", str(text))
    for w in _STOP_KW:
        t = t.replace(w, "")
    return t


def _ngram_dice(a: str, b: str, n: int = 2) -> float:
    """字符 n-gram Dice 系数(中文文本相似度稳健指标)"""
    if not a or not b:
        return 0.0
    if len(a) < n or len(b) < n:
        return 1.0 if a == b else 0.0
    sa = {a[i:i + n] for i in range(len(a) - n + 1)}
    sb = {b[i:i + n] for i in range(len(b) - n + 1)}
    inter = len(sa & sb)
    return 2.0 * inter / (len(sa) + len(sb)) if (len(sa) + len(sb)) else 0.0


def _ratio(a: str, b: str) -> float:
    """综合相似度: n-gram Dice 与 最长公共子串 ratio 取较高者"""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    dice = _ngram_dice(na, nb)
    sm = SequenceMatcher(None, na, nb).ratio()
    return max(dice, sm)


def main() -> None:
    with SessionLocal() as db:
        rows = db.scalars(
            select(ExternalComplaint)
            .where(ExternalComplaint.id < 300000)
            .where(ExternalComplaint.complaint_date.isnot(None))
            .order_by(ExternalComplaint.complaint_date)
        ).all()
    print(f"真实批次行数: {len(rows)}")

    # 索引: phone -> rows; sn -> rows(退化)
    by_phone: dict[str, list] = {}
    by_sn: dict[str, list] = {}
    for r in rows:
        if r.complainant_phone:
            by_phone.setdefault(r.complainant_phone.strip(), []).append(r)
        if r.device_sn:
            by_sn.setdefault(r.device_sn.strip(), []).append(r)

    def _key(r) -> str | None:
        return (r.complainant_phone or "").strip() or None

    def _key2(r) -> str | None:
        return (r.device_sn or "").strip() or None

    groups: list[dict] = []
    seen_pairs: set[tuple[int, int]] = set()

    def _consider(a, b, key_kind: str):
        """评估一对是否为同源"""
        if a.id == b.id:
            return
        pa, pb = (a.id, b.id) if a.id < b.id else (b.id, a.id)
        if pa in seen_pairs:
            return
        daydiff = abs((a.complaint_date - b.complaint_date).days)
        if daydiff > 14:
            return
        ca, cb = _cluster(a.platform), _cluster(b.platform)
        sim = _ratio(a.problem or a.handle_detail, b.problem or b.handle_detail)
        # 跨簇(真多渠道) 阈值放宽; 同簇 需更接近才判(防同人不同诉求误并)
        cross = ca != cb
        thr = 0.45 if cross else 0.62
        if sim >= thr:
            seen_pairs.add(pa)
            groups.append({
                "id_a": a.id, "id_b": b.id,
                "phone": a.complainant_phone or b.complainant_phone,
                "sn_a": a.device_sn, "sn_b": b.device_sn,
                "cluster_a": ca, "cluster_b": cb,
                "plat_a": a.platform or "", "plat_b": b.platform or "",
                "date_a": a.complaint_date.date(), "date_b": b.complaint_date.date(),
                "daydiff": daydiff, "sim": round(sim, 3),
                "content_a": (a.problem or a.handle_detail or "")[:38],
                "content_b": (b.problem or b.handle_detail or "")[:38],
                "key_kind": key_kind,
                # ── 导出核对补充字段 ──
                "no_a": a.complaint_no, "no_b": b.complaint_no,
                "result_a": a.result, "result_b": b.result,
                "hd_a": a.handle_time, "hd_b": b.handle_time,
                "complainant_a": a.complainant, "complainant_b": b.complainant,
                "ticket_a": a.matched_ticket_no, "ticket_b": b.matched_ticket_no,
                "full_a": a.problem or a.handle_detail or "",
                "full_b": b.problem or b.handle_detail or "",
            })

    # 1) 手机号强键
    for phone, lst in by_phone.items():
        if len(lst) < 2:
            continue
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                _consider(lst[i], lst[j], "phone")
    # 2) SN 退化键(仅当双方都无手机号)
    for sn, lst in by_sn.items():
        lst2 = [r for r in lst if not _key(r)]
        if len(lst2) < 2:
            continue
        for i in range(len(lst2)):
            for j in range(i + 1, len(lst2)):
                _consider(lst2[i], lst2[j], "sn_only")

    # 输出(终端摘要)
    print(f"\n同源候选对: {len(groups)} 组")
    groups.sort(key=lambda g: (-g["sim"], g["daydiff"]))
    print(f"\n{'id_a':>8} {'id_b':>8} {'差(d)':>5} {'sim':>5}  {'簇A→簇B':<14} {'日期':<22} 内容A / 内容B")
    print("-" * 160)
    for g in groups[:120]:
        print(f"{g['id_a']:>8} {g['id_b']:>8} {g['daydiff']:>5} {g['sim']:>5.2f}  "
              f"{g['cluster_a']}→{g['cluster_b']:<10} {str(g['date_a'])}~{g['date_b']}  "
              f"{g['content_a']} / {g['content_b']}")

    # 汇总
    import collections
    cross_cnt = sum(1 for g in groups if g["cluster_a"] != g["cluster_b"])
    same_cnt = len(groups) - cross_cnt
    print(f"\n跨簇(真多渠道): {cross_cnt} 组; 同簇(同渠道多次跟进): {same_cnt} 组")
    print(f"高相似(≥0.7): {sum(1 for g in groups if g['sim'] >= 0.7)} 组; 中(0.55~0.7): {sum(1 for g in groups if 0.55 <= g['sim'] < 0.7)} 组")
    phones = collections.Counter(g["phone"] for g in groups if g["phone"])
    print(f"涉及不同手机号人数: {len(phones)}")

    # 导出 Excel(默认导出, 供人工核对)
    _export_excel(groups)


def _sh(dt: datetime | None) -> str:
    """datetime → 北京时间 YYYY-MM-DD HH:MM"""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_CN).strftime("%Y-%m-%d %H:%M")


def _row_view(g: dict, side: str) -> dict:
    """构造 Excel 一侧行的可读视图(保留原始投诉编号避免前导0丢失, 用文本)"""
    id_k = f"id_{side}"
    return {
        "记录ID": g[id_k],
        "投诉编号": g.get(f"no_{side}") or "",
        "渠道": g.get(f"plat_{side}") or "",
        "登记日期": str(g.get(f"date_{side}") or ""),
        "投诉人": g.get(f"complainant_{side}") or "",
        "手机号": ("'" + g.get("phone", "")) if g.get("phone") else "",
        "SN": g.get(f"sn_{side}") or "",
        "结果": g.get(f"result_{side}") or "",
        "处理时间": _sh(g.get(f"hd_{side}")),
        "关联工单": g.get(f"ticket_{side}") or "",
        "内容": g.get(f"full_{side}") or "",
    }


def _export_excel(groups: list[dict]) -> str:
    """把候选对导出为 Excel: 每人一对两行(A/B)横向展开为一行, 便于逐条核对."""
    import pandas as pd

    rows = []
    for i, g in enumerate(groups, 1):
        is_cross = g["cluster_a"] != g["cluster_b"]
        # 判定档位: ≥0.79 高置信自动同源; 0.45~0.79 人工复核候选
        level = "自动同源(高置信)" if g["sim"] >= 0.79 else "需人工复核"
        va = _row_view(g, "a")
        vb = _row_view(g, "b")
        rows.append({
            "序号": i,
            "判定档位": level,
            "相似度": g["sim"],
            "日期差(天)": g["daydiff"],
            "关联键": "手机号" if g["key_kind"] == "phone" else "SN(无手机号)",
            "是否跨渠道簇": "是" if is_cross else "否",
            # A 侧
            "A_记录ID": va["记录ID"], "A_投诉编号": va["投诉编号"],
            "A_渠道": va["渠道"], "A_登记日期": va["登记日期"],
            "A_投诉人": va["投诉人"], "A_手机号": va["手机号"], "A_SN": va["SN"],
            "A_结果": va["结果"], "A_处理时间": va["处理时间"],
            "A_关联工单": va["关联工单"], "A_内容": va["内容"],
            # B 侧
            "B_记录ID": vb["记录ID"], "B_投诉编号": vb["投诉编号"],
            "B_渠道": vb["渠道"], "B_登记日期": vb["登记日期"],
            "B_投诉人": vb["投诉人"], "B_手机号": vb["手机号"], "B_SN": vb["SN"],
            "B_结果": vb["结果"], "B_处理时间": vb["处理时间"],
            "B_关联工单": vb["关联工单"], "B_内容": vb["内容"],
            # 供用户填列
            "人工判定": "", "备注": "",
        })
    df = pd.DataFrame(rows)
    df.insert(0, "是否同源(人工填: 是/否)", "")
    df.insert(0, "你的判定", [""] * len(df))

    out_dir = os.path.join(REPO_ROOT, "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "外部投诉同源候选_20260909.xlsx")

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df_auto = df[df["判定档位"] == "自动同源(高置信)"]
        df_rev = df[df["判定档位"] == "需人工复核"]
        df_auto.to_excel(writer, index=False, sheet_name="高置信自动同源")
        df_rev.to_excel(writer, index=False, sheet_name="需人工复核")
        # 自适应列宽(近似)
        for sheet, ddf in (("高置信自动同源", df_auto), ("需人工复核", df_rev)):
            ws = writer.sheets[sheet]
            for col_idx, col in enumerate(ddf.columns, 1):
                width = min(60, max(8, int(ddf[col].astype(str).str.len().max() * 1.1)))
                ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = width
    print(f"\n✅ 已导出: {out_path}")
    print(f"   高置信自动同源: {len(df_auto)} 行 / 需人工复核: {len(df_rev)} 行")
    return out_path


if __name__ == "__main__":
    main()

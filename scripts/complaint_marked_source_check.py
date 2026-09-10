#!/usr/bin/env python3
"""外部投诉同源判重: 客服姓名括弧标注(N) 真值核对 (2026-09-09) — 只读

背景: 客服登记时会在 complainant 姓名后用括弧+数字标注该投诉人第几次投诉,
      如 胡康玲(2)/胡康玲(3), 同一投诉人跨渠道/跨日期多次登记。
      这批标注可作为「已知同源」真值, 用来评估规则原型的召回率。

用途:
  1. 找出 complainant 含 (N) 括弧标注 的人 (按手机号聚合), 生成"标注同源组"
  2. 用现有规则原型对这些人跑一遍候选, 看是否全部命中(召回率)
  3. 输出: 标注但规则未召回的组(漏网) + 两侧结果/内容, 供调优
"""
from __future__ import annotations

import os
import re
import sys
from difflib import SequenceMatcher

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND_DIR)

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models.external_complaint import ExternalComplaint  # noqa: E402

# 复用原型脚本的判重规则
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from complaint_same_source_proto import _CHANNEL_CLUSTER, _cluster, _ratio  # noqa: E402

# 姓名里的括弧标注: 王杰（2）/ 胡康玲(3) / 李俊成（2） → 提取基准姓名
_PAREN_MARK = re.compile(r"^(.+?)[（(]\s*\d+\s*[)）]\s*$")
_PAREN_MARK_ANY = re.compile(r"[（(]\s*\d+\s*[)）]")


def _strip_mark(name: str | None) -> str:
    """去掉姓名中的 (N) 标注, 返回基准姓名. 无标注返回原文."""
    if not name:
        return ""
    m = _PAREN_MARK.match(str(name).strip())
    return m.group(1).strip() if m else str(name).strip()


def main() -> None:
    with SessionLocal() as db:
        rows = db.scalars(
            select(ExternalComplaint)
            .where(ExternalComplaint.id < 300000)
            .order_by(ExternalComplaint.complaint_date)
        ).all()
    print(f"真实批次行数: {len(rows)}")

    # ── 1. 找出所有"带标注"的行 & 其关联键(手机号/SN) ──
    # 标注行 = complainant 含 (N); 与之同源的其它行 = 同手机号(或同SN) 且 基准姓名相同
    marked: list = [r for r in rows if _PAREN_MARK_ANY.search(r.complainant or "")]
    print(f"含 (N) 标注行: {len(marked)}")

    by_phone: dict[str, list] = {}
    for r in rows:
        if r.complainant_phone:
            by_phone.setdefault(r.complainant_phone.strip(), []).append(r)

    # 对每个标注行, 找它所属的"标注同源组": 同手机号 且 基准姓名相同(或互为空白/同SN)
    # 简单可靠判据: 同手机号 且 (基准姓名相同 或 组内任一行含标注)
    # 先按 手机号 分组, 看组内是否有人带标注
    groups: list[dict] = []  # {phone, base_name, members:[row...], marked_count}
    seen_phones: set[str] = set()
    for r in marked:
        ph = (r.complainant_phone or "").strip()
        if not ph or ph in seen_phones:
            continue
        # 同手机号全部行
        peers = [x for x in by_phone.get(ph, [])]
        base_names = {_strip_mark(x.complainant) for x in peers if _strip_mark(x.complainant)}
        # 只看"有人带标注 且 有跨行/跨渠道"的组(标注才有意义)
        if len(peers) < 2:
            continue
        # 排除: 组内全是同一行内容的脏副本(带SN vs 不带SN同内容) — 需人工看
        groups.append({
            "phone": ph,
            "base_names": sorted(base_names),
            "members": peers,
            "marked_count": sum(1 for x in peers if _PAREN_MARK_ANY.search(x.complainant or "")),
        })
        seen_phones.add(ph)

    print(f"标注同源候选组(同手机号多行+含标注): {len(groups)} 组")

    # ── 2. 对每组, 用规则原型跑组内两两相似, 统计"被规则判定同源"的对数 ──
    total_marked_pairs = 0
    rule_hit_pairs = 0
    missed: list[dict] = []
    for g in groups:
        peers = sorted(g["members"], key=lambda x: x.complaint_date or datetime.min)
        # 组内两两对(排除完全相同 content + 同SN的重复行, 那个是脏副本不是同源线索)
        for i in range(len(peers)):
            for j in range(i + 1, len(peers)):
                a, b = peers[i], peers[j]
                # 时间窗
                daydiff = abs((a.complaint_date - b.complaint_date).days)
                if daydiff > 14:
                    continue
                ca, cb = _cluster(a.platform), _cluster(b.platform)
                sim = _ratio(a.problem or a.handle_detail, b.problem or b.handle_detail)
                cross = ca != cb
                thr = 0.45 if cross else 0.62
                total_marked_pairs += 1
                if sim >= thr:
                    rule_hit_pairs += 1
                else:
                    missed.append({
                        "phone": g["phone"],
                        "name_a": a.complainant, "name_b": b.complainant,
                        "id_a": a.id, "id_b": b.id,
                        "plat_a": a.platform or "", "plat_b": b.platform or "",
                        "date_a": a.complaint_date.date(), "date_b": b.complaint_date.date(),
                        "daydiff": daydiff, "sim": round(sim, 3),
                        "result_a": a.result, "result_b": b.result,
                        "content_a": (a.problem or a.handle_detail or "")[:30],
                        "content_b": (b.problem or b.handle_detail or "")[:30],
                    })

    print(f"\n组内 ≤14天 的配对总数: {total_marked_pairs}")
    print(f"规则判定同源(命中): {rule_hit_pairs} / {total_marked_pairs}  (召回率 {rule_hit_pairs/total_marked_pairs*100:.0f}%)")
    print(f"漏网(标注同源但规则未命中): {len(missed)} 对\n")

    if missed:
        print(f"{'name_a':<10} {'name_b':<10} {'差':>3} {'sim':>5}  {'渠道A→B':<18} 内容A / 内容B")
        print("-" * 150)
        for m in sorted(missed, key=lambda x: x["sim"])[:60]:
            print(f"{m['name_a']:<10} {m['name_b']:<10} {m['daydiff']:>3} {m['sim']:>5.2f}  "
                  f"{m['plat_a'] or '(空)'}→{m['plat_b'] or '(空)'}  "
                  f"{m['content_a']} / {m['content_b']}")

    # ── 3. 汇总每组规模 ──
    import collections
    sz = collections.Counter(len(g["members"]) for g in groups)
    print(f"\n组规模分布(同手机号行数): {dict(sorted(sz.items()))}")


if __name__ == "__main__":
    main()

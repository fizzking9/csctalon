"""外部投诉同源判重 v5 服务（工单 #208；草案 docs/design/20260909-...§5 FINAL v5）

规则（#206 已拍板）：
  同源 ⇔ 同 device_sn AND 内容相似度≥0.7 AND complaint_date 差≤14天
       AND 非「再次来电内嵌前文」(命中→needs_review 人工)
  - 无 SN 行不参与（缺设备主键，无法区分同源/重复登记）
  - 渠道簇③A 仅作辅助证据（跨渠道=重复登记旁证），**不单独成判定条件**
  - ④C：组登记日=最早 complaint_date；处理人/分类取组内最新有效行（见 group_view）
  - ⑤A：自动 auto 打标 + 内嵌前文降 needs_review；打标不删源数据

幂等：重跑时解散未经人工终审（auto/needs_review）的旧组后全量重建；
      confirmed/rejected 的人工组保留，其成员不参与本轮重排。

相似度实现沿用原型（scripts/complaint_same_source_proto.py / _final.py），
不引入新依赖（re 标准库 + difflib）。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import column, select, table
from sqlalchemy.orm import Session

from app.models.complaint_same_source import (
    ComplaintSameSourceGroup,
    ComplaintSameSourceMember,
)
from app.services.quality_check import stats_quality_include

# #338: 同源判重读 std_all 镜像
_EC = table("std_external_complaint_all",
            column("id"), column("device_sn"), column("complaint_date"),
            column("problem"), column("handle_detail"), column("quality_status"),
            column("platform"), column("complainant"), column("complainant_phone"),
            column("complaint_category"), column("result"), column("follower"),
            column("matched_ticket_no"))

logger = logging.getLogger(__name__)

RULE_VERSION = "v5"
SIM_THRESHOLD = 0.7
# ⚠️ 时间窗=14 天是用户 2026-09-09 拍板值（草案 §7 待确认 1）；
#    原型脚本 complaint_same_source_final.py 里的 30 天是拍板前旧值，以此常量为准。
DAY_WINDOW = 14

# ── ③A 渠道簇映射（#206 拍板：市监局多监所同簇 + 12315/消协同簇）──────────
# 仅作辅助证据（跨簇=重复登记旁证），不单独成判定条件；即使个别监所漏登记，
# 也不会误并（主判定仍是同SN+相似+时间窗），只会少一条旁证。
# 上线后按 SELECT DISTINCT platform FROM external_complaints 巡检补全（#206 约定）。
CHANNEL_CLUSTER: dict[str, str] = {
    # 市监局簇（不同监所视为同一监管主体）
    "航城监所": "市监局", "宝安市监局": "市监局", "南沙市监局": "市监局",
    "天河市监局": "市监局", "武汉市监局": "市监局",
    # 12315/消协簇
    "全国12315平台": "12315/消协", "全国消协智慧315平台": "12315/消协",
    "全国消协315": "12315/消协",
    # 各自独立成簇
    "公共服务平台": "公共服务平台", "消委会": "消委会",
    "投诉处理内部群": "内部",
}

_PAREN_MARK = re.compile(r"[（(]\s*\d+\s*[)）]")
_EMBED_RE = re.compile(r"再次来电|再次来电补充|再次来电反馈|投诉\d+次|市民来电投诉")
_BR_TAG = "<br/>"
# 归一化：去手机号 / SN 等长数字串 / 标点空白，保中英文主体
_MASK_NOISE = re.compile(r"(1[3-9]\d{9})|(\d{6,})|[^\u4e00-\u9fa5a-zA-Z]")
_STOP_KW = (
    "投诉", "举报", "本人", "消费者", "商家", "购买", "该公司",
    "深圳市", "有限公司", "反映", "来电", "情况",
)

_DONE_RESULTS = {
    "和解结案", "撤销结案", "暂行办结", "调解结案", "调节结案",
    "投诉无效", "重复投诉", "终止调节", "退回消委会",
}


def channel_cluster(platform: str | None) -> str:
    """渠道名 → 渠道簇；未登记的渠道自成一簇（③A，漏登记不误并）。"""
    return CHANNEL_CLUSTER.get((platform or "").strip(), (platform or "").strip() or "其他")


def _norm(text: str | None) -> str:
    if not text:
        return ""
    t = _MASK_NOISE.sub("", str(text))
    for w in _STOP_KW:
        t = t.replace(w, "")
    return t


def _ngram_dice(a: str, b: str, n: int = 2) -> float:
    """字符 n-gram Dice 系数（中文短文本稳健指标）。"""
    if not a or not b:
        return 0.0
    if len(a) < n or len(b) < n:
        return 1.0 if a == b else 0.0
    sa = {a[i:i + n] for i in range(len(a) - n + 1)}
    sb = {b[i:i + n] for i in range(len(b) - n + 1)}
    total = len(sa) + len(sb)
    return 2.0 * len(sa & sb) / total if total else 0.0


def content_similarity(a: str | None, b: str | None) -> float:
    """综合相似度：n-gram Dice 与 SequenceMatcher ratio 取较高者（沿用原型）。"""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    return max(_ngram_dice(na, nb), SequenceMatcher(None, na, nb).ratio())


def _content_text(r: Any) -> str:
    return r.problem or r.handle_detail or ""


def mark_present(name: str | None) -> bool:
    """客服姓名括弧标注 (N) 信号（仅作旁证，不单独成判）。"""
    return bool(name) and bool(_PAREN_MARK.search(str(name)))


def embed_suspect(r: Any) -> bool:
    """「公共服务平台再次来电内嵌前文」嫌疑：历史原文把相似度虚高 → 需人工。

    判据沿用 v5 原型：文本 ≥150 字，且含再次来电极词，且（含 <br/> 或 长度 >250）。
    """
    txt = _content_text(r)
    if len(txt) < 150:
        return False
    return bool(_EMBED_RE.search(txt)) and ("<br/>" in txt or len(txt) > 250)


def _daydiff(a: datetime, b: datetime) -> int:
    """两个 aware 时间戳的日历日差绝对值（输入为 naive 时按 UTC 补）。"""
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return abs((a - b).days)


def _discover_pairs(
    rows_by_sn: dict[str, list[Any]],
) -> dict[str, list[dict[str, Any]]]:
    """同 SN 组内两两判定，产出 {sn: [pair,...]}（仅命中对）。

    pair: {ids:(ida,idb), sim, daydiff, clues:[...], embed:bool}
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for sn, rows in rows_by_sn.items():
        if len(rows) < 2:
            continue
        pairs: list[dict[str, Any]] = []
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                sim = content_similarity(_content_text(a), _content_text(b))
                dd = _daydiff(a.complaint_date, b.complaint_date)
                if sim < SIM_THRESHOLD or dd > DAY_WINDOW:
                    continue
                clues: list[str] = []
                if (a.result or "") == "重复投诉" or (b.result or "") == "重复投诉":
                    clues.append("result=重复投诉")
                if mark_present(a.complainant) or mark_present(b.complainant):
                    clues.append("客服标注")
                if channel_cluster(a.platform) != channel_cluster(b.platform):
                    clues.append("跨渠道簇")
                if a.matched_ticket_no and a.matched_ticket_no == b.matched_ticket_no:
                    clues.append("同工单")
                pairs.append({
                    "sn": sn,
                    "ida": a.id, "idb": b.id,
                    "sim": round(sim, 3), "daydiff": dd,
                    "clues": clues,
                    "embed": embed_suspect(a) or embed_suspect(b),
                })
        if pairs:
            out[sn] = pairs
    return out


def _components(
    rows_by_sn: dict[str, list[Any]],
    pairs_by_sn: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """按命中对做连通分量（同 SN 内并查集），组装组定义。

    组状态：组内任一对 embed=True → needs_review（保守，人工看整组）；否则 auto。
    置信度：组内命中对最低 sim。
    """
    groups: list[dict[str, Any]] = []
    for sn, rows in rows_by_sn.items():
        pairs = pairs_by_sn.get(sn)
        if not pairs:
            continue
        parent = {r.id: r.id for r in rows}

        def find(x: int) -> int:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: int, y: int) -> None:
            parent[find(x)] = find(y)

        for p in pairs:
            union(p["ida"], p["idb"])

        comp: dict[int, list[int]] = {}
        for r in rows:
            if any(p["ida"] == r.id or p["idb"] == r.id for p in pairs):
                comp.setdefault(find(r.id), []).append(r.id)

        date_by_id = {r.id: r.complaint_date for r in rows}
        for member_ids in comp.values():
            comp_pairs = [
                p for p in pairs
                if p["ida"] in member_ids and p["idb"] in member_ids
            ]
            any_embed = any(p["embed"] for p in comp_pairs)
            dates = [date_by_id[i] for i in member_ids if date_by_id.get(i) is not None]
            groups.append({
                "device_sn": sn,
                "status": "needs_review" if any_embed else "auto",
                "confidence": min(p["sim"] for p in comp_pairs),
                "member_ids": sorted(member_ids),
                "first_date": min(dates) if dates else None,
                "last_date": max(dates) if dates else None,
                "evidence": [
                    {
                        "a": p["ida"], "b": p["idb"], "sim": p["sim"],
                        "daydiff": p["daydiff"], "clues": p["clues"],
                        "embed": p["embed"],
                    }
                    for p in comp_pairs
                ],
            })
    return groups


def rebuild_groups(db: Session, *, persist: bool = True) -> dict[str, Any]:
    """全量重算并（可选）落库。返回统计 dict。

    幂等：先删除未经人工终审的旧组（auto/needs_review，成员随 FK CASCADE）；
          confirmed/rejected 人工组保留，其成员 complaint_id 不参与本轮重排。
    persist=False 时只计算不落库（dry-run / 预览）。
    """
    # 1) 候选行：有 SN、有投诉日期、质量未排除（dup_residual/ignored 脏数据不参与，
    #    草案 §5.2 重复导入与同源判重分开）。
    rows = db.execute(
        select(_EC)
        .where(_EC.c.device_sn.isnot(None))
        .where(_EC.c.complaint_date.isnot(None))
        .where(stats_quality_include(_EC.c.quality_status))
        .order_by(_EC.c.complaint_date, _EC.c.id)
    ).mappings().all()

    # 2) 人工已终审组的成员锁定，不参与重排。
    locked_ids: set[int] = set()
    kept_groups = db.scalars(
        select(ComplaintSameSourceGroup).where(
            ComplaintSameSourceGroup.rule_version == RULE_VERSION,
            ComplaintSameSourceGroup.status.in_(("confirmed", "rejected")),
        )
    ).all()
    kept_member_rows = db.scalars(select(ComplaintSameSourceMember)).all() if kept_groups else []
    kept_group_ids = {g.id for g in kept_groups}
    for m in kept_member_rows:
        if m.group_id in kept_group_ids:
            locked_ids.add(m.complaint_id)

    rows_by_sn: dict[str, list[Any]] = {}
    for r in rows:
        if r.id in locked_ids:
            continue
        rows_by_sn.setdefault(r.device_sn.strip(), []).append(r)

    # 3) 判对 → 连通分量。
    pairs_by_sn = _discover_pairs(rows_by_sn)
    group_defs = _components(rows_by_sn, pairs_by_sn)
    n_pairs = sum(len(v) for v in pairs_by_sn.values())
    n_auto_groups = sum(1 for g in group_defs if g["status"] == "auto")
    n_review_groups = len(group_defs) - n_auto_groups

    stats = {
        "rule_version": RULE_VERSION,
        "scanned": len(rows),
        "sn_multi_groups": sum(1 for v in rows_by_sn.values() if len(v) >= 2),
        "pairs": n_pairs,
        "groups_total": len(group_defs),
        "groups_auto": n_auto_groups,
        "groups_needs_review": n_review_groups,
        "locked_members_kept": len(locked_ids),
        "persisted": False,
    }

    if not persist:
        return stats

    # 4) 解散未经终审的旧组。用显式 bulk DELETE 且先成员后组：
    #    两表无 ORM relationship，UoW 不保证删除顺序（可能先删组触发 DB CASCADE，
    #    再删成员时 0 行匹配告警）；显式顺序最稳。
    stale_ids = list(db.scalars(
        select(ComplaintSameSourceGroup.id).where(
            ComplaintSameSourceGroup.rule_version == RULE_VERSION,
            ComplaintSameSourceGroup.status.in_(("auto", "needs_review")),
        )
    ))
    if stale_ids:
        db.execute(
            ComplaintSameSourceMember.__table__.delete().where(
                ComplaintSameSourceMember.group_id.in_(stale_ids)
            )
        )
        db.execute(
            ComplaintSameSourceGroup.__table__.delete().where(
                ComplaintSameSourceGroup.id.in_(stale_ids)
            )
        )
        db.flush()

    # 5) 写入新组与成员（uq_same_source_complaint_once 兜底：锁定成员不会重复进入）。
    for gd in group_defs:
        g = ComplaintSameSourceGroup(
            rule_version=RULE_VERSION,
            device_sn=gd["device_sn"],
            status=gd["status"],
            confidence=gd["confidence"],
            first_complaint_date=gd["first_date"],
            latest_complaint_date=gd["last_date"],
            member_count=len(gd["member_ids"]),
            evidence=gd["evidence"],
        )
        db.add(g)
        db.flush()
        for cid in gd["member_ids"]:
            db.add(ComplaintSameSourceMember(group_id=g.id, complaint_id=cid))
    # 显式 flush：调用方可能在 autoflush=False 的会话（pytest db fixture）中紧接查询，
    # 不依赖自动刷盘即可读到成员行。
    db.flush()

    stats["persisted"] = True
    logger.info(
        "complaint_same_source %s rebuilt: groups=%s (auto=%s review=%s), pairs=%s, locked=%s",
        RULE_VERSION, len(group_defs), n_auto_groups, n_review_groups, n_pairs, len(locked_ids),
    )
    return stats


def group_view(db: Session, group: ComplaintSameSourceGroup) -> dict[str, Any]:
    """组视图（④C 取值）：成员明细 + 登记日取最早 + 处理人/分类/状态取最新有效行。

    "最新有效行" = 成员中 complaint_date 最晚的一行；
    案件状态语义（草案 §4）：组内任一办结即已处理（在视图里给 done_any 标志，
    最新行 result 仅作展示，二者不互相覆盖）。
    """
    members = db.execute(
        select(_EC)
        .join(
            ComplaintSameSourceMember,
            ComplaintSameSourceMember.complaint_id == _EC.c.id,
        )
        .where(ComplaintSameSourceMember.group_id == group.id)
        .order_by(_EC.c.complaint_date, _EC.c.id)
    ).mappings().all()

    latest = members[-1] if members else None
    return {
        "id": group.id,
        "rule_version": group.rule_version,
        "device_sn": group.device_sn,
        "status": group.status,
        "confidence": group.confidence,
        "member_count": group.member_count,
        "first_complaint_date": group.first_complaint_date.isoformat() if group.first_complaint_date else None,
        "latest_complaint_date": group.latest_complaint_date.isoformat() if group.latest_complaint_date else None,
        "evidence": group.evidence or [],
        "review_note": group.review_note,
        "reviewed_by": group.reviewed_by,
        "reviewed_name": group.reviewed_name,
        "reviewed_at": group.reviewed_at.isoformat() if group.reviewed_at else None,
        # ④C：登记日取最早（= first_complaint_date）；处理人/分类取最新有效行
        "latest_handler": latest.follower if latest else None,
        "latest_category": latest.complaint_category if latest else None,
        "latest_result": latest.result if latest else None,
        "done_any": any((m.result or "") in _DONE_RESULTS for m in members),
        "members": [_member_dict(m) for m in members],
    }


def _member_dict(r: Any) -> dict[str, Any]:
    return {
        "id": r.id,
        "complaint_date": r.complaint_date.isoformat() if r.complaint_date else None,
        "platform": r.platform,
        "channel_cluster": channel_cluster(r.platform),
        "complainant": r.complainant,
        "complainant_phone": r.complainant_phone,
        "device_sn": r.device_sn,
        "complaint_category": r.complaint_category,
        "result": r.result,
        "follower": r.follower,
        "matched_ticket_no": r.matched_ticket_no,
        "problem": (r.problem or "")[:200],
        "embed_suspect": embed_suspect(r),
    }

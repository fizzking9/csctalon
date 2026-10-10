"""同源判重 v5 落地测试（工单 #208）

覆盖：规则判定（相似阈值/14天窗/无SN/不同SN/内嵌前文降级）、连通分量、
质量排除、幂等重跑、人工组锁定、④C 组视图取值、dry-run。
造数用唯一 SN 前缀（全表扫描型服务，靠 SN 隔离其他用例/残留）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from app.models.complaint_same_source import (
    ComplaintSameSourceGroup,
    ComplaintSameSourceMember,
)
from app.models.device import Device
from app.models.external_complaint import ExternalComplaint
from app.services import complaint_same_source as css

RUN = f"SN208-{uuid4().hex[:8]}"
BASE = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)

HI_A = "商家虚标无限流量套餐与宣传不符，客服沟通闭口不回复，消费者要求退款处理并赔偿损失"
HI_B = "商家虚标无限流量套餐与宣传不符，客服沟通闭口不回复，消费者要求退款处理并道歉赔偿"
LO_B = "随身设备插上卡后红灯常亮无法联网重启无效，商家同意全额退还购机费用"

# 内嵌前文（再次来电 + <br/> + 超长），相似度会被历史原文虚高
EMBED_B = HI_A + "<br/>" + "再次来电补充：" + ("前次原文复述" * 30)


def _sn(tag: str) -> str:
    return f"{RUN}-{tag}"


def _mk(db, tag: str, *, sn: str | None = None, problem: str = HI_A,
        date: datetime = BASE, platform: str = "全国12315平台",
        complainant: str = "张三", result: str | None = None,
        follower: str | None = None, category: str | None = None,
        quality: str | None = None, phone: str | None = "13800000001",
        ticket: str | None = None, handle_detail: str | None = None) -> int:
    sn = sn if sn is not None else _sn(tag)
    if sn:
        # 同 SN 只建一次 device（FK external_complaints.device_sn → devices.sn）
        if not db.get(Device, sn):
            db.add(Device(sn=sn))
    db.add(ExternalComplaint(
        complaint_no=f"{RUN}-{tag}-{uuid4().hex[:6]}",
        complaint_date=date, platform=platform, device_sn=sn,
        complainant=complainant, complainant_phone=phone,
        problem=problem, result=result, follower=follower,
        complaint_category=category, matched_ticket_no=ticket,
        handle_detail=handle_detail, quality_status=quality,
    ))
    db.flush()
    return sn


def _groups_for(db, sn: str) -> list[ComplaintSameSourceGroup]:
    return list(db.scalars(
        select(ComplaintSameSourceGroup).where(
            ComplaintSameSourceGroup.device_sn == sn,
            ComplaintSameSourceGroup.rule_version == css.RULE_VERSION,
        )
    ))


def _rebuild(db):
    return css.rebuild_groups(db, persist=True)


# ───────────────────────── 纯函数 ─────────────────────────

def test_channel_cluster_3a():
    # 市监局多监所同簇、12315/消协同簇
    assert css.channel_cluster("航城监所") == "市监局"
    assert css.channel_cluster("武汉市监局") == "市监局"
    assert css.channel_cluster("全国消协智慧315平台") == "12315/消协"
    # 未登记渠道自成一簇（漏登记不误并，③A 约定）
    assert css.channel_cluster("某新监所") == "某新监所"


def test_similarity_helpers():
    assert css.content_similarity(HI_A, HI_B) >= 0.9
    assert css.content_similarity(HI_A, LO_B) < 0.3
    assert css.content_similarity("", HI_A) == 0.0


def test_embed_suspect_rule():
    r_short = type("R", (), {"problem": "再次来电" + "短" * 10, "handle_detail": None})()
    assert css.embed_suspect(r_short) is False
    r_embed = type("R", (), {"problem": EMBED_B, "handle_detail": None})()
    assert css.embed_suspect(r_embed) is True


# ───────────────────────── 判定规则 ─────────────────────────

def test_auto_group_same_sn_similar_within_window(db):
    sn = _sn("auto")
    _mk(db, "a1", sn=sn, problem=HI_A, date=BASE)
    _mk(db, "a2", sn=sn, problem=HI_B, date=BASE + timedelta(days=5))
    _rebuild(db)
    gs = _groups_for(db, sn)
    assert len(gs) == 1
    g = gs[0]
    assert g.status == "auto"
    assert g.member_count == 2
    assert g.confidence is not None and g.confidence >= css.SIM_THRESHOLD
    assert g.first_complaint_date == BASE
    assert len(g.evidence) == 1 and g.evidence[0]["daydiff"] == 5


def test_no_group_when_low_similarity(db):
    sn = _sn("lowsim")
    _mk(db, "b1", sn=sn, problem=HI_A, date=BASE)
    _mk(db, "b2", sn=sn, problem=LO_B, date=BASE + timedelta(days=1))
    _rebuild(db)
    assert _groups_for(db, sn) == []


def test_no_group_when_beyond_14d_window(db):
    sn = _sn("window")
    _mk(db, "c1", sn=sn, problem=HI_A, date=BASE)
    _mk(db, "c2", sn=sn, problem=HI_B, date=BASE + timedelta(days=20))
    _rebuild(db)
    assert _groups_for(db, sn) == []


def test_window_boundary_14d_groups(db):
    sn = _sn("b14")
    _mk(db, "d1", sn=sn, problem=HI_A, date=BASE)
    _mk(db, "d2", sn=sn, problem=HI_B, date=BASE + timedelta(days=14))
    _rebuild(db)
    assert len(_groups_for(db, sn)) == 1  # 恰 14 天命中


def test_no_sn_rows_excluded(db):
    # 无 SN：两行内容完全相同也不参与自动判重（v5 去无 SN 退化）
    _mk(db, "n1", sn=None, problem=HI_A, date=BASE)
    _mk(db, "n2", sn=None, problem=HI_A, date=BASE + timedelta(days=1))
    stats = _rebuild(db)
    # 无 SN 行不进任何组（用统计断言：组内不存在无 SN 成员）
    members = db.scalars(select(ComplaintSameSourceMember)).all()
    comp_ids = [m.complaint_id for m in members]
    rows = db.scalars(select(ExternalComplaint).where(
        ExternalComplaint.complaint_no.like(f"{RUN}-n%"))).all()
    assert all(r.id not in comp_ids for r in rows)
    assert stats["rule_version"] == "v5"


def test_different_sn_not_grouped_even_same_person(db):
    # 不同 SN 即便同人同内容 = 不同投诉（胡阳例）
    s1, s2 = _sn("sn-x"), _sn("sn-y")
    _mk(db, "e1", sn=s1, problem=HI_A, date=BASE, phone="13911112222")
    _mk(db, "e2", sn=s2, problem=HI_B, date=BASE + timedelta(days=2), phone="13911112222")
    _rebuild(db)
    assert _groups_for(db, s1) == [] and _groups_for(db, s2) == []


def test_cross_cluster_is_clue_not_standalone(db):
    # 跨渠道簇但内容不相似 → 不组（簇仅旁证，不单独成判）
    sn = _sn("cross")
    _mk(db, "f1", sn=sn, problem="淘宝购买一百八十元套餐宣传与实际不符要求退款",
        date=BASE, platform="全国12315平台")
    _mk(db, "f2", sn=sn, problem="京东购买随身wifi设备宣传不符沟通无效投诉",
        date=BASE + timedelta(days=1), platform="航城监所")
    _rebuild(db)
    assert _groups_for(db, sn) == []


def test_embed_suspect_downgrades_needs_review(db):
    sn = _sn("embed")
    _mk(db, "g1", sn=sn, problem=HI_A, date=BASE)
    _mk(db, "g2", sn=sn, problem=EMBED_B, date=BASE + timedelta(days=3))
    _rebuild(db)
    gs = _groups_for(db, sn)
    assert len(gs) == 1 and gs[0].status == "needs_review"


def test_connected_three_members_one_group(db):
    # A-B 相似、B-C 相似（A-C 可能跨度更大）→ 连通为一个 3 人组
    sn = _sn("chain")
    _mk(db, "h1", sn=sn, problem=HI_A, date=BASE)
    _mk(db, "h2", sn=sn, problem=HI_B, date=BASE + timedelta(days=6))
    _mk(db, "h3", sn=sn, problem=HI_B, date=BASE + timedelta(days=12))
    _rebuild(db)
    gs = _groups_for(db, sn)
    assert len(gs) == 1 and gs[0].member_count == 3


def test_quality_excluded_rows_skipped(db):
    # dup_residual（重复导入脏数据，草案 §5.2 与本判重分开）不参与
    sn = _sn("qual")
    _mk(db, "i1", sn=sn, problem=HI_A, date=BASE)  # 正常
    _mk(db, "i2", sn=sn, problem=HI_B, date=BASE + timedelta(days=2),
        quality="dup_residual")  # 已治理重复
    _rebuild(db)
    assert _groups_for(db, sn) == []


# ───────────────────────── 幂等 / 锁定 / 视图 ─────────────────────────

def test_rebuild_idempotent(db):
    sn = _sn("idem")
    _mk(db, "j1", sn=sn, problem=HI_A, date=BASE)
    _mk(db, "j2", sn=sn, problem=HI_B, date=BASE + timedelta(days=3))
    _rebuild(db)
    _rebuild(db)
    gs = _groups_for(db, sn)
    assert len(gs) == 1 and gs[0].member_count == 2


def test_reviewed_group_members_locked(db):
    sn = _sn("lock")
    _mk(db, "k1", sn=sn, problem=HI_A, date=BASE)
    _mk(db, "k2", sn=sn, problem=HI_B, date=BASE + timedelta(days=3))
    _rebuild(db)
    g = _groups_for(db, sn)[0]
    g.status = "confirmed"  # 模拟人工终审
    db.flush()
    # 再跑：人工组保留，成员仍在
    _rebuild(db)
    gs2 = _groups_for(db, sn)
    assert len(gs2) == 1 and gs2[0].status == "confirmed"
    assert gs2[0].member_count == 2


def test_dry_run_not_persisted(db):
    sn = _sn("dry")
    _mk(db, "m1", sn=sn, problem=HI_A, date=BASE)
    _mk(db, "m2", sn=sn, problem=HI_B, date=BASE + timedelta(days=2))
    stats = css.rebuild_groups(db, persist=False)
    assert stats["persisted"] is False and stats["groups_total"] >= 1
    assert _groups_for(db, sn) == []  # 未落库


def test_group_view_takes_earliest_date_and_latest_row(db):
    # ④C：登记日=最早 complaint_date；处理人/分类取最新有效行
    sn = _sn("view")
    _mk(db, "v1", sn=sn, problem=HI_A, date=BASE,
        follower="早期处理人", category="套餐/计费")
    _mk(db, "v2", sn=sn, problem=HI_B, date=BASE + timedelta(days=4),
        follower="最新处理人", category="退款/售后", result="和解结案")
    _rebuild(db)
    g = _groups_for(db, sn)[0]
    view = css.group_view(db, g)
    assert view["first_complaint_date"] == BASE.isoformat()
    assert view["latest_handler"] == "最新处理人"
    assert view["latest_category"] == "退款/售后"
    assert view["done_any"] is True
    assert len(view["members"]) == 2

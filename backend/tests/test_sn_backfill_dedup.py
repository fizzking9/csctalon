# -*- coding: utf-8 -*-
"""315 采集「SN 后补」治理测试 (#59)

## 用户报告（2026-09-11）
> 「`67912450006278` 9.2；这个有重复，没有去重成功」
> 「9.1 航城监所 …没有关键的信息显示」
> 「08-31 / 09-02 有个投诉 怎么全部都是空的」

## 根因
源文档「**先建行、后补 SN**」+ `dedup_hash` **含 SN** →
T1(SN空) INSERT 残影, T2(SN补齐) 指纹变 → **又 INSERT** → 同投诉两条并列。

实测(生产库): 85 条 = 37 组，冗余 48 条，形态均为「缺SN残影 + 有SN完整行」。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.models.device import Device
from app.models.external_complaint import ExternalComplaint
from app.services.complaint_sync import (
    _identity_dedup_hash,
    _reconcile_sn_backfill,
)


def _utc(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


def _ensure_device(db, sn):
    if sn and db.get(Device, sn) is None:
        db.add(Device(sn=sn))
        db.flush()


def _rec(db, sn, phone, problem, cdate=None):
    """构造一条待入库行(含 dedup_hash)"""
    _ensure_device(db, sn)
    r = {
        "complaint_date": cdate or _utc(2026, 9, 2),
        "complainant_phone": phone,
        "device_sn": sn,
        "problem": problem,
        "platform": "航城监所",
    }
    r["dedup_hash"] = _identity_dedup_hash(r)
    return r


def _store(db, rec, **kw):
    """把行写入库(模拟已入库的残影/完整行)"""
    row = ExternalComplaint(
        complaint_date=rec["complaint_date"],
        complainant_phone=rec["complainant_phone"],
        device_sn=rec["device_sn"],
        problem=rec["problem"],
        platform=rec.get("platform"),
        dedup_hash=rec["dedup_hash"],
        matched_ticket_no=None,
        **kw,
    )
    db.add(row)
    db.flush()
    return row


# ══════════════════════════════════════════════════════════════════
# 二、SN 后补 → 标记残影（不新增、不回填）—— #59 语义, #62 修正
# ══════════════════════════════════════════════════════════════════
class TestSnBackfillMark:
    """★ 有SN的行**不再认领/回填**残影, 而是把残影**标记为被取代**

    修正原因(实测缺陷): 初版让有SN的行改指纹去 UPDATE 残影, 但 upsert 同时
    回填了 SN ⇒ 落库成「device_sn 有值 + dedup_hash 按无SN算」的自相矛盾行,
    与真完整行**永不归并** ⇒ 反而制造重复(生产 26 条被写坏)。
    """

    def test_marks_existing_ghost(self, db):
        """★ 有SN的行命中残影 → 把残影标记为 dup_sn_backfill"""
        from app.services.complaint_scope import QSTATUS_DUP_SN_BACKFILL

        ghost = _rec(db, None, "13700137000", "我充值160元要求退款")
        g = _store(db, ghost)

        full = _rec(db, "SNAB1", "13700137000", "我充值160元要求退款")
        orig_hash = full["dedup_hash"]
        rows = [full]
        marked, skipped = _reconcile_sn_backfill(db, ExternalComplaint, rows)

        assert marked == 1, "应标记 1 条残影"
        assert skipped == 0
        assert len(rows) == 1, "本行应正常入库"
        assert rows[0]["dedup_hash"] == orig_hash, \
            "★ 指纹**不得**被改成残影的(这是初版的 bug)"
        db.refresh(g)
        assert g.quality_status == QSTATUS_DUP_SN_BACKFILL, "残影应被标记"

    def test_ghost_sn_not_backfilled(self, db):
        """★ 残影的 device_sn **不得**被回填(初版 bug 的直接体现)"""
        from app.services.complaint_scope import QSTATUS_DUP_SN_BACKFILL

        ghost = _rec(db, None, "13700137001", "回填缺陷验证")
        g = _store(db, ghost)
        full = _rec(db, "SNAB2", "13700137001", "回填缺陷验证")
        _reconcile_sn_backfill(db, ExternalComplaint, [full])

        db.refresh(g)
        assert g.device_sn is None, "★ 残影的 SN 必须保持为空(不回填)"
        assert g.quality_status == QSTATUS_DUP_SN_BACKFILL

    def test_no_ghost_keeps_own_hash(self, db):
        """库内无残影 → 不标记, 指纹不变(正常新增)"""
        full = _rec(db, "SNAB3", "13700137002", "无残影可标记")
        rows = [full]
        orig = full["dedup_hash"]
        marked, skipped = _reconcile_sn_backfill(db, ExternalComplaint, rows)
        assert marked == 0 and skipped == 0
        assert rows[0]["dedup_hash"] == orig

    def test_mark_does_not_delete_ghost(self, db):
        """★ 标记**不删**原残影行（用户: 不能乱删除）"""
        ghost = _rec(db, None, "13700137003", "不删数据的验证")
        g = _store(db, ghost)
        gid = g.id

        full = _rec(db, "SNAB4", "13700137003", "不删数据的验证")
        _reconcile_sn_backfill(db, ExternalComplaint, [full])
        assert db.get(ExternalComplaint, gid) is not None, "残影行必须保留"


# ══════════════════════════════════════════════════════════════════
# 三、无SN行 → 跳过（不覆盖完整行）
# ══════════════════════════════════════════════════════════════════
class TestGhostSkip:
    def test_ghost_skipped_when_full_exists(self, db):
        """★ 库内已有完整行(有SN), 又来一条无SN的 → 跳过, 不写入"""
        full = _rec(db, "SNSK1", "13700137004", "已有完整行的场景")
        _store(db, full)

        ghost = _rec(db, None, "13700137004", "已有完整行的场景")
        rows = [ghost]
        marked, skipped = _reconcile_sn_backfill(db, ExternalComplaint, rows)

        assert skipped == 1, "应跳过 1 行"
        assert marked == 0
        assert rows == [], "无SN的重复行不应写入"

    def test_ghost_kept_when_no_full_exists(self, db):
        """库内无完整行 → 残影正常入库(首见)"""
        ghost = _rec(db, None, "13700137005", "首次出现的无SN行")
        rows = [ghost]
        marked, skipped = _reconcile_sn_backfill(db, ExternalComplaint, rows)
        assert marked == 0 and skipped == 0
        assert len(rows) == 1, "首见残影应保留"


# ══════════════════════════════════════════════════════════════════
# 四、幂等与边界
# ══════════════════════════════════════════════════════════════════
class TestIdempotence:
    def test_repeat_mark_is_idempotent(self, db):
        """★ 幂等: 已标记的残影不会被重复处理"""
        ghost = _rec(db, None, "13700137006", "重复标记测试")
        _store(db, ghost)

        full1 = _rec(db, "SNID1", "13700137006", "重复标记测试")
        m1, _ = _reconcile_sn_backfill(db, ExternalComplaint, [full1])
        assert m1 == 1

        full2 = _rec(db, "SNID1", "13700137006", "重复标记测试")
        m2, _ = _reconcile_sn_backfill(db, ExternalComplaint, [full2])
        assert m2 == 0, "已标记的残影不应重复标记"

    def test_empty_rows(self, db):
        assert _reconcile_sn_backfill(db, ExternalComplaint, []) == (0, 0)

    def test_missing_phone_no_crash(self, db):
        """缺电话时不应崩(跳过治理逻辑)"""
        r = {"complaint_date": _utc(2026, 9, 2), "complainant_phone": None,
             "device_sn": "SNNP1", "problem": "无电话"}
        r["dedup_hash"] = _identity_dedup_hash(r)
        rows = [r]
        marked, skipped = _reconcile_sn_backfill(db, ExternalComplaint, rows)
        assert marked == 0 and skipped == 0
        assert len(rows) == 1

    def test_mixed_batch(self, db):
        """混合批次: 一条标记残影 + 一条无SN跳过"""
        ghost_a = _rec(db, None, "13700137007", "可被标记的残影")
        ga = _store(db, ghost_a)
        full_b = _rec(db, "SNMX1", "13700137008", "已有完整行")
        _store(db, full_b)

        claim_rec = _rec(db, "SNMX2", "13700137007", "可被标记的残影")
        skip_rec = _rec(db, None, "13700137008", "已有完整行")
        rows = [claim_rec, skip_rec]

        marked, skipped = _reconcile_sn_backfill(db, ExternalComplaint, rows)
        assert marked == 1 and skipped == 1
        assert len(rows) == 1, "只应保留有SN那条"
        db.refresh(ga)
        assert ga.quality_status is not None


# ══════════════════════════════════════════════════════════════════
# 五、真实案例复现（语义修正后）
# ══════════════════════════════════════════════════════════════════
class TestRealCases:
    """复现用户报告的两个生产案例 —— 现应「标记残影」而非「回填SN」"""

    def test_case_67912450006278(self, db):
        """案例1: 309648(16:30, SN空) + 312247(17:00, SN有)"""
        from app.services.complaint_scope import QSTATUS_DUP_SN_BACKFILL

        prob = ("26年1月我在深圳这家随身wifi设备上充值160元，之后到8月因为"
                "套餐更新问题不再使用，要求客服退钱包充值剩下的余额")
        ghost = _rec(db, None, "18338595596", prob, _utc(2026, 9, 2))
        g = _store(db, ghost)

        incoming = _rec(db, "67912450006278", "18338595596", prob, _utc(2026, 9, 2))
        rows = [incoming]
        marked, _ = _reconcile_sn_backfill(db, ExternalComplaint, rows)

        assert marked == 1, "该案例应标记残影"
        db.refresh(g)
        assert g.device_sn is None, "★ 残影SN不得被回填"
        assert g.quality_status == QSTATUS_DUP_SN_BACKFILL
        assert rows[0]["device_sn"] == "67912450006278", "完整行正常入库"

    def test_case_234603_and_247519(self, db):
        """案例2: 234603(11:30, SN空) + 247519(13:00, SN有, 字段完整)"""
        from app.services.complaint_scope import QSTATUS_DUP_SN_BACKFILL

        prob = ("具体违法违规的行为或线索:购买本商品之前，我明确的问过客服推荐的"
                "套餐是否为多网通")
        ghost = _rec(db, None, "18224953354", prob, _utc(2026, 9, 1))
        g = _store(db, ghost)

        incoming = _rec(db, "69212633317456", "18224953354", prob, _utc(2026, 9, 1))
        marked, _ = _reconcile_sn_backfill(db, ExternalComplaint, [incoming])
        assert marked == 1
        db.refresh(g)
        assert g.device_sn is None and g.quality_status == QSTATUS_DUP_SN_BACKFILL


# ══════════════════════════════════════════════════════════════════
# 六、统计排除（#60）
# ══════════════════════════════════════════════════════════════════
class TestStatsExclusion:
    """残影标记后必须从统计口径排除，避免同一投诉被算两次"""

    def test_exclude_cond_drops_marked(self, db):
        """★ 被标记的残影不出现在统计集合里"""
        from app.models.external_complaint import ExternalComplaint
        from app.services.complaint_scope import (
            QSTATUS_DUP_SN_BACKFILL,
            complaint_quality_include_cond,
        )

        keep = _rec(db, "SNQC1", "13700138001", "保留的完整行")
        _store(db, keep)
        ghost = _rec(db, None, "13700138002", "被排除的残影")
        g = _store(db, ghost)
        g.quality_status = QSTATUS_DUP_SN_BACKFILL
        db.flush()

        got = set(db.scalars(
            select(ExternalComplaint.device_sn)
            .where(*complaint_quality_include_cond(ExternalComplaint))
            .where(ExternalComplaint.complainant_phone.in_(
                ["13700138001", "13700138002"]))
        ).all())
        assert "SNQC1" in got
        assert None not in got, "被标记的残影应被排除"

    def test_excluded_row_still_exists(self, db):
        """★ 排除是**统计口径**行为 —— 原始行必须还在（不删数据）"""
        from app.models.external_complaint import ExternalComplaint
        from app.services.complaint_scope import QSTATUS_DUP_SN_BACKFILL

        ghost = _rec(db, None, "13700138003", "排除后仍应存在的残影")
        g = _store(db, ghost)
        g.quality_status = QSTATUS_DUP_SN_BACKFILL
        db.flush()
        gid = g.id

        assert db.get(ExternalComplaint, gid) is not None, "原始行不得被删除"

    def test_guard_skips_when_no_column(self, db):
        """列存在性守卫: 传入 db 时正常返回条件"""
        from app.models.external_complaint import ExternalComplaint
        from app.services.complaint_scope import (
            complaint_quality_include_cond,
            has_quality_col,
        )

        assert has_quality_col(db, "external_complaints") is True
        assert len(complaint_quality_include_cond(ExternalComplaint, db)) == 1

    def test_normal_rows_unaffected(self, db):
        """未标记的普通行不受影响"""
        from app.models.external_complaint import ExternalComplaint
        from app.services.complaint_scope import complaint_quality_include_cond

        _store(db, _rec(db, "SNQC2", "13700138004", "普通行不该被排除"))
        got = db.scalars(
            select(ExternalComplaint.device_sn)
            .where(*complaint_quality_include_cond(ExternalComplaint))
            .where(ExternalComplaint.complainant_phone == "13700138004")
        ).all()
        assert got == ["SNQC2"]


# ══════════════════════════════════════════════════════════════════
# 七、残影参与事件归并（#61）
# ══════════════════════════════════════════════════════════════════
class TestGhostEventMerge:
    """残影(缺SN)应用**弱指纹**归入对应完整行的事件，而非落 `unkeyed`"""

    def _ev(self, rid, sn, phone, day, problem, ch="航城监所", qs=None):
        return {
            "id": rid, "device_sn": sn, "complainant_phone": phone,
            "complaint_date": _utc(2026, 9, day), "problem": problem,
            "channel": ch, "quality_status": qs,
        }

    def test_ghost_merged_into_event(self):
        """★ 核心: 残影(缺SN)与完整行 → 归并为 **1 个事件**"""
        from app.services.complaint_scope import (
            QSTATUS_DUP_SN_BACKFILL,
            dedup_events,
        )
        prob = "26年1月充值160元要求退款"
        rows = [
            self._ev(312247, "67912450006278", "18338595596", 2, prob),
            self._ev(309648, None, "18338595596", 2, prob,
                     qs=QSTATUS_DUP_SN_BACKFILL),
        ]
        events, merged, _ = dedup_events(rows)
        assert len(events) == 1, "残影应并入完整行的事件"
        assert set(events[0]["record_ids"]) == {312247, 309648}
        assert events[0]["ghost_ids"] == [309648]
        assert events[0]["ghost_merged"] == 1

    def test_real_missing_sn_not_merged(self):
        """★ 关键: **真实缺SN**(未标残影) 不得被误并 —— 仍独立成事件"""
        from app.services.complaint_scope import dedup_events
        prob = "同一天同电话同问题但确实没SN"
        rows = [
            self._ev(1, "SNAAA", "13711111111", 3, prob),
            self._ev(2, None, "13711111111", 3, prob),   # 无 quality_status
        ]
        events, _, _ = dedup_events(rows)
        kinds = sorted(e["kind"] for e in events)
        assert len(events) == 2, "真实缺SN的不能被并掉(会漏计真实投诉)"
        assert "unkeyed" in kinds

    def test_ghost_without_match_stays_separate(self):
        """残影若找不到对应完整行 → 保守独立成事件(不猜)"""
        from app.services.complaint_scope import (
            QSTATUS_DUP_SN_BACKFILL,
            dedup_events,
        )
        rows = [
            self._ev(10, "SNBBB", "13722222222", 4, "另一个人的投诉"),
            self._ev(11, None, "13722222222", 4, "残影的孤立问题",
                     qs=QSTATUS_DUP_SN_BACKFILL),
        ]
        events, _, _ = dedup_events(rows)
        # 问题签名不同 → 弱指纹不匹配 → 残影独立
        assert len(events) == 2

    def test_ghost_ids_empty_for_normal_event(self):
        """普通事件 ghost_ids 为空"""
        from app.services.complaint_scope import dedup_events
        rows = [self._ev(20, "SNCCC", "13733333333", 5, "普通投诉")]
        events, _, _ = dedup_events(rows)
        assert events[0]["ghost_ids"] == []
        assert events[0]["ghost_merged"] == 0

    def test_ghost_merge_is_idempotent(self):
        """幂等: 重复归并结果一致"""
        from app.services.complaint_scope import (
            QSTATUS_DUP_SN_BACKFILL,
            dedup_events,
        )
        prob = "幂等性验证的问题描述"
        rows = [
            self._ev(30, "SNDDD", "13744444444", 6, prob),
            self._ev(31, None, "13744444444", 6, prob,
                     qs=QSTATUS_DUP_SN_BACKFILL),
        ]
        a = dedup_events(rows)[0]
        b = dedup_events(rows)[0]
        assert len(a) == len(b) == 1
        assert a[0]["record_ids"] == b[0]["record_ids"]

    def test_no_mutation_of_input(self):
        """★ 归并**不修改**输入行(用户: 不能乱删除/乱改)"""
        from app.services.complaint_scope import (
            QSTATUS_DUP_SN_BACKFILL,
            dedup_events,
        )
        prob = "不得修改输入"
        rows = [
            self._ev(40, "SNEEE", "13755555555", 7, prob),
            self._ev(41, None, "13755555555", 7, prob,
                     qs=QSTATUS_DUP_SN_BACKFILL),
        ]
        snapshot = [dict(r) for r in rows]
        dedup_events(rows)
        assert rows == snapshot, "输入行不得被修改"

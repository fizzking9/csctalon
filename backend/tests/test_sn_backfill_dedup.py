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

from app.models.device import Device
from app.models.external_complaint import ExternalComplaint
from app.services.complaint_sync import (
    _identity_dedup_hash,
    _reconcile_sn_backfill,
    _weak_identity_hash,
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
# 一、弱指纹（不含 SN）
# ══════════════════════════════════════════════════════════════════
class TestWeakHash:
    def test_sn_not_in_weak_hash(self, db):
        """★ 弱指纹不含 SN → 有SN/无SN 的两行弱指纹**相同**"""
        a = _rec(db, "SNW1", "13800138000", "套餐涨价投诉内容")
        b = _rec(db, None, "13800138000", "套餐涨价投诉内容")
        assert _weak_identity_hash(a) == _weak_identity_hash(b)

    def test_main_hash_differs_by_sn(self, db):
        """主指纹含 SN → 两行主指纹**不同**（这正是重复的来源）"""
        a = _rec(db, "SNW2", "13800138000", "套餐涨价投诉内容")
        b = _rec(db, None, "13800138000", "套餐涨价投诉内容")
        assert a["dedup_hash"] != b["dedup_hash"]

    def test_weak_hash_sensitive_to_phone(self, db):
        a = _rec(db, "SNW3", "13800138000", "同样的问题描述")
        b = _rec(db, "SNW3", "13900139000", "同样的问题描述")
        assert _weak_identity_hash(a) != _weak_identity_hash(b)

    def test_weak_hash_sensitive_to_date(self, db):
        a = _rec(db, "SNW4", "13800138000", "同样的问题", _utc(2026, 9, 2))
        b = _rec(db, "SNW4", "13800138000", "同样的问题", _utc(2026, 9, 3))
        assert _weak_identity_hash(a) != _weak_identity_hash(b)


# ══════════════════════════════════════════════════════════════════
# 二、SN 后补 → UPDATE 残影（不新增）
# ══════════════════════════════════════════════════════════════════
class TestSnBackfillClaim:
    def test_backfill_claims_existing_ghost(self, db):
        """★ 核心场景: 库内已有「缺SN残影」, 新行带SN → 认领(改指纹为残影的)"""
        ghost = _rec(db, None, "13700137000", "我充值160元要求退款")
        _store(db, ghost)                      # T1: 残影入库(SN空)

        # T2: 同一投诉, SN 补上了
        full = _rec(db, "SNAB1", "13700137000", "我充值160元要求退款")
        rows = [full]
        claimed, skipped = _reconcile_sn_backfill(db, ExternalComplaint, rows)

        assert claimed == 1, "应认领 1 行"
        assert skipped == 0
        assert len(rows) == 1, "不应新增行"
        assert rows[0]["dedup_hash"] == ghost["dedup_hash"], \
            "指纹应改为残影的 → 后续 upsert 会 UPDATE 残影"

    def test_no_ghost_keeps_own_hash(self, db):
        """库内无残影 → 指纹不变(正常新增)"""
        full = _rec(db, "SNAB2", "13700137001", "无残影可认领")
        rows = [full]
        orig = full["dedup_hash"]
        claimed, skipped = _reconcile_sn_backfill(db, ExternalComplaint, rows)
        assert claimed == 0 and skipped == 0
        assert rows[0]["dedup_hash"] == orig

    def test_claim_does_not_delete_ghost(self, db):
        """★ 认领**不删**原残影行（用户: 不能乱删除）"""
        ghost = _rec(db, None, "13700137002", "不删数据的验证")
        g = _store(db, ghost)
        gid = g.id

        full = _rec(db, "SNAB3", "13700137002", "不删数据的验证")
        _reconcile_sn_backfill(db, ExternalComplaint, [full])

        assert db.get(ExternalComplaint, gid) is not None, "残影行必须保留"


# ══════════════════════════════════════════════════════════════════
# 三、无SN残影 → 跳过（不覆盖完整行）
# ══════════════════════════════════════════════════════════════════
class TestGhostSkip:
    def test_ghost_skipped_when_full_exists(self, db):
        """★ 库内已有完整行(有SN), 又来一条无SN的 → 跳过, 不覆盖"""
        full = _rec(db, "SNSK1", "13700137003", "已有完整行的场景")
        _store(db, full)                       # 完整行已入库

        ghost = _rec(db, None, "13700137003", "已有完整行的场景")
        rows = [ghost]
        claimed, skipped = _reconcile_sn_backfill(db, ExternalComplaint, rows)

        assert skipped == 1, "应跳过 1 行"
        assert claimed == 0
        assert rows == [], "残影不应写入"

    def test_ghost_kept_when_no_full_exists(self, db):
        """库内无完整行 → 残影正常入库(首见)"""
        ghost = _rec(db, None, "13700137004", "首次出现的无SN行")
        rows = [ghost]
        claimed, skipped = _reconcile_sn_backfill(db, ExternalComplaint, rows)
        assert claimed == 0 and skipped == 0
        assert len(rows) == 1, "首见残影应保留"


# ══════════════════════════════════════════════════════════════════
# 四、幂等与边界
# ══════════════════════════════════════════════════════════════════
class TestIdempotence:
    def test_repeat_claim_is_stable(self, db):
        """★ 幂等: 同一残影重复被认领, 结果一致(不产生额外行)"""
        ghost = _rec(db, None, "13700137005", "重复认领测试")
        _store(db, ghost)

        for _ in range(3):
            full = _rec(db, "SNID1", "13700137005", "重复认领测试")
            rows = [full]
            claimed, _ = _reconcile_sn_backfill(db, ExternalComplaint, rows)
            assert claimed == 1
            assert rows[0]["dedup_hash"] == ghost["dedup_hash"]

    def test_empty_rows(self, db):
        assert _reconcile_sn_backfill(db, ExternalComplaint, []) == (0, 0)

    def test_missing_phone_no_crash(self, db):
        """缺电话时不应崩(跳过认领逻辑)"""
        r = {"complaint_date": _utc(2026, 9, 2), "complainant_phone": None,
             "device_sn": "SNNP1", "problem": "无电话"}
        r["dedup_hash"] = _identity_dedup_hash(r)
        rows = [r]
        claimed, skipped = _reconcile_sn_backfill(db, ExternalComplaint, rows)
        assert claimed == 0 and skipped == 0
        assert len(rows) == 1

    def test_mixed_batch(self, db):
        """混合批次: 一条可认领 + 一条残影应跳过"""
        ghost_a = _rec(db, None, "13700137006", "可被认领的残影")
        _store(db, ghost_a)
        full_b = _rec(db, "SNMX1", "13700137007", "已有完整行")
        _store(db, full_b)

        claim_rec = _rec(db, "SNMX2", "13700137006", "可被认领的残影")
        skip_rec = _rec(db, None, "13700137007", "已有完整行")
        rows = [claim_rec, skip_rec]

        claimed, skipped = _reconcile_sn_backfill(db, ExternalComplaint, rows)
        assert claimed == 1 and skipped == 1
        assert len(rows) == 1, "只应保留可认领那条"
        assert rows[0]["dedup_hash"] == ghost_a["dedup_hash"]


# ══════════════════════════════════════════════════════════════════
# 五、真实案例复现
# ══════════════════════════════════════════════════════════════════
class TestRealCases:
    """复现用户报告的两个生产案例"""

    def test_case_67912450006278(self, db):
        """案例1: 309648(16:30, SN空) + 312247(17:00, SN有)"""
        prob = "26年1月我在深圳这家随身wifi设备上充值160元，之后到8月因为套餐更新问题不再使用，要求客服退钱包充值剩下的余额"
        ghost = _rec(db, None, "18338595596", prob, _utc(2026, 9, 2))
        _store(db, ghost)

        incoming = _rec(db, "67912450006278", "18338595596", prob, _utc(2026, 9, 2))
        rows = [incoming]
        claimed, _ = _reconcile_sn_backfill(db, ExternalComplaint, rows)

        assert claimed == 1, "该案例应被认领(不再产生第二条)"
        assert rows[0]["dedup_hash"] == ghost["dedup_hash"]

    def test_case_234603_and_247519(self, db):
        """案例2: 234603(11:30, SN空) + 247519(13:00, SN有, 字段完整)"""
        prob = "具体违法违规的行为或线索:购买本商品之前，我明确的问过客服推荐的套餐是否为多网通"
        ghost = _rec(db, None, "18224953354", prob, _utc(2026, 9, 1))
        _store(db, ghost)

        incoming = _rec(db, "69212633317456", "18224953354", prob, _utc(2026, 9, 1))
        rows = [incoming]
        claimed, _ = _reconcile_sn_backfill(db, ExternalComplaint, rows)
        assert claimed == 1

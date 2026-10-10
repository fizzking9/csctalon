"""#63 回归测试 —— 纯工信部判定（★ 必须用「SN 或 电话」）

## 用户需求

> 「纯粹工信部的**优先级不高**，有统计记录就好了。回头**单列一页**展示即可。
>   工单+文档 **合并去重** 统计展示。」

## 判据

```
纯工信部 = 工信部工单 ∧ NOT (卡商表 SN 或 电话 命中)
                      ∧ NOT (315表  SN 或 电话 命中)
```

## ★ 为什么必须「SN 或 电话」（本测试的核心）

用户人工核对 6 条「SN 查不到」的工信部工单后，发现其中 3 条**卡商表有登记**
—— 不是 SN 一致，而是**电话一致**：

| 现实情况 | 影响 |
|---|---|
| 同一客户**多台设备**（工单管 A 机，卡商单登记 B 机） | SN 不匹配 |
| 同客户**换机**，SN 变了 | SN 不匹配 |
| 处理意见里提到另一台 SN | SN 不匹配 |

若只用 SN 精确匹配，这 3 条会被**误判为纯工信部**（实际应归卡商）。

> 印证 #55: **SN = 客户身份，但一个客户可能有多台设备**；电话才是可靠关联键。

⚠️ 测试用 SN/电话均为**虚构**，不含真实数据。
"""
from __future__ import annotations

import datetime as dt

from app.models.card_complaint import CardComplaint
from app.models.device import Device
from app.models.external_complaint import ExternalComplaint
from app.models.ticket import ServiceTicket
from app.services.pure_card_complaint import pure_card_tickets

_TS = dt.datetime(2026, 8, 6, 12, 0, tzinfo=dt.timezone.utc)


def _ensure_device(db, sn):
    if sn and db.get(Device, sn) is None:
        db.add(Device(sn=sn))
        db.flush()


def _ticket(db, no, sn, phone, item="工信部投诉", status="待处理"):
    _ensure_device(db, sn)
    db.add(ServiceTicket(
        ticket_no=no, device_sn=sn, phone=phone, service_item=item,
        service_category=None, channel="H5", register_time=_TS, status=status,
    ))
    db.flush()


def _card(db, sn, phone, group="卡商群A"):
    _ensure_device(db, sn)
    db.add(CardComplaint(
        device_sn=sn, complainant_phone=phone, complaint_group=group,
        complaint_date=_TS,
    ))
    db.flush()


def _ext(db, sn, phone, platform="航城监所"):
    _ensure_device(db, sn)
    db.add(ExternalComplaint(
        device_sn=sn, complainant_phone=phone, platform=platform,
        complaint_date=_TS,
    ))
    db.flush()


class TestPureCardDetection:
    """判定四种命中情形"""

    def test_sn_hit_is_not_pure(self, db):
        """SN 精确命中卡商表 → 不算纯工信部"""
        _ticket(db, "TC63-1", "869630000000001", "13800138001")
        _card(db, "869630000000001", "13900139001")

        r = pure_card_tickets(db)
        assert r["pure"] == 0
        assert r["matched"] == 1

    def test_phone_hit_is_not_pure(self, db):
        """★ 核心: SN 不同但**电话**命中 → 也不算纯工信部

        场景: 同一客户多台设备（工单管 A 机, 卡商单登记 B 机）。
        只用 SN 精确匹配会误判为纯工信部。
        """
        _ticket(db, "TC63-2", "869630000000002", "13800138002")
        _card(db, "869630000000099", "13800138002")   # SN 不同, 电话相同

        r = pure_card_tickets(db)
        assert r["pure"] == 0, "电话命中却判为纯工信部 —— SN 精确匹配的盲区"
        assert r["sn_exact_only"] == 0, "SN 不该命中"
        assert "卡商电话" in r["items"] or r["matched"] == 1

    def test_both_miss_is_pure(self, db):
        """SN 与电话都无 → 纯工信部"""
        _ticket(db, "TC63-3", "869630000000003", "13800138003")
        _card(db, "869630000000099", "13900139009")
        _ext(db, "869630000000098", "13900139008")

        r = pure_card_tickets(db)
        assert r["pure"] == 1
        it = r["items"][0]
        assert it["ticket_no"] == "TC63-3"
        assert it["is_pure"] is True
        assert it["hit_paths"] == []

    def test_315_phone_hit_is_not_pure(self, db):
        """315 表电话命中 → 不算纯工信部"""
        _ticket(db, "TC63-4", "869630000000004", "13800138004")
        _ext(db, "869630000000097", "13800138004")

        r = pure_card_tickets(db)
        assert r["pure"] == 0
        assert r["matched"] == 1

    def test_multiple_devices_same_customer(self, db):
        """★ 多设备场景: 工单 SN_A + 电话 P; 卡商 SN_B + 电话 P → 不算纯"""
        _ticket(db, "TC63-5", "869630000000005", "13800138005")
        _card(db, "869630000000006", "13800138005")   # 另一台设备

        r = pure_card_tickets(db)
        assert r["pure"] == 0, "同客户多设备应经电话关联, 不算纯工信部"


class TestStatistics:
    """统计口径"""

    def test_sn_exact_vs_or_phone(self, db):
        """★ `sn_exact_only` ≤ `matched` —— 电话兜底只增不减"""
        _ticket(db, "TC63-6", "869630000000007", "13800138007")   # SN 命中
        _ticket(db, "TC63-8", "869630000000009", "13800138009")   # 仅电话命中
        _card(db, "869630000000007", "13900139007")
        _card(db, "869630000000088", "13800138009")

        r = pure_card_tickets(db)
        assert r["sn_exact_only"] == 1, "只有 1 条 SN 精确命中"
        assert r["matched"] == 2, "两条都经 (SN 或 电话) 命中"
        assert r["sn_exact_only"] <= r["matched"]

    def test_monthly_buckets(self, db):
        """月度分布: total 与 pure 都要有"""
        _ticket(db, "TC63-M1", "869630000000011", "13800138011")
        _ticket(db, "TC63-M2", "869630000000012", "13800138012")
        _card(db, "869630000000011", "13900139011")   # M1 命中

        r = pure_card_tickets(db)
        m = {x["month"]: x for x in r["monthly"]}
        assert "2026-08" in m
        assert m["2026-08"]["total"] == 2
        assert m["2026-08"]["pure"] == 1

    def test_hit_path_counts(self, db):
        """命中路径分布按来源归类"""
        _ticket(db, "TC63-P1", "869630000000013", "13800138013")
        _ticket(db, "TC63-P2", "869630000000014", "13800138014")
        _card(db, "869630000000088", "13800138013")   # 卡商电话
        _ext(db, "869630000000014", "13900139014")    # 315 SN

        r = pure_card_tickets(db)
        assert r["by_hit_path"].get("卡商电话", 0) == 1
        assert r["by_hit_path"].get("315SN", 0) == 1

    def test_include_checked_returns_all(self, db):
        """include_checked=True 时连已对账的也返回, 并带 is_pure 标记"""
        _ticket(db, "TC63-I1", "869630000000015", "13800138015")
        _ticket(db, "TC63-I2", "869630000000016", "13800138016")
        _card(db, "869630000000015", "13900139015")

        r = pure_card_tickets(db, include_checked=True)
        items = r["items"]
        assert len(items) == 2
        by_no = {x["ticket_no"]: x for x in items}
        assert by_no["TC63-I1"]["is_pure"] is False
        assert by_no["TC63-I2"]["is_pure"] is True


class TestScope:
    """范围与过滤"""

    def test_non_gyb_tickets_excluded(self, db):
        """非工信部工单不进本视图"""
        _ticket(db, "TC63-X1", "869630000000017", "13800138017", item="12315")
        _ticket(db, "TC63-X2", "869630000000018", "13800138018", item="商品投诉")
        _ticket(db, "TC63-X3", "869630000000019", "13800138019", item="工信部投诉")

        r = pure_card_tickets(db)
        assert r["total_tickets"] == 1
        assert r["items"][0]["ticket_no"] == "TC63-X3"

    def test_handle_opinion_fallback_to_remark(self, db):
        """处理意见: handle_opinion 为空时回退 remark"""
        _ensure_device(db, "869630000000020")
        db.add(ServiceTicket(
            ticket_no="TC63-R1", device_sn="869630000000020",
            phone="13800138020", service_item="工信部投诉", service_category=None,
            channel="H5", register_time=_TS, status="待处理",
            handle_opinion=None, remark="客户要求退费",
        ))
        db.flush()

        r = pure_card_tickets(db)
        assert r["items"][0]["handle_opinion"] == "客户要求退费"

    def test_readonly_no_data_mutation(self, db):
        """★ 只读: 调用后原数据不变（用户: "不能乱删除 所以还是慎重"）"""
        _ticket(db, "TC63-RO", "869630000000021", "13800138021")
        before = db.execute(
            __import__("sqlalchemy").text(
                "SELECT service_item, status FROM service_tickets WHERE ticket_no='TC63-RO'"
            )
        ).first()

        pure_card_tickets(db)

        after = db.execute(
            __import__("sqlalchemy").text(
                "SELECT service_item, status FROM service_tickets WHERE ticket_no='TC63-RO'"
            )
        ).first()
        assert before == after, "本视图不得修改任何数据"
        assert after[0] == "工信部投诉", "原 service_item 必须保持原样"

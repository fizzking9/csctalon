"""外部投诉对账逻辑单测 (#56) —— 判据收敛 / 候选限定 / 双边空悬 / 重匹配幂等

用户语义（2026-09-11 权威）:
  「内部投诉、商户号投诉 跟外部投诉 **不存在对账关系**。
    外部投诉对账 = 服务类别为空、类型是几个平台的**外部投诉**与文档数据拉齐。
    两边数据都有意义: BMS 是**经营登记**; 文档是**接待过程**。」
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.models.device import Device
from app.models.external_complaint import ExternalComplaint
from app.models.ticket import ServiceTicket
from app.services.complaint_scope import (
    is_complaint_ticket_cond,
)
from app.services.external_reconcile import (
    _pick_nearest,
    _sn_candidates,
    external_reconcile,
    remark_matched,
)

# ⚠️ 复用 conftest 的 `db` fixture(真实 PostgreSQL 测试库 + 事务回滚)。
#    不用 SQLite: 模型主键是 BigInteger, SQLite 不自增 → NOT NULL 失败。
#    ⚠️ `service_tickets.device_sn` 有 FK → 必须先建设备行。


def _utc(y, m, d, h=0):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


def _ensure_device(db, sn):
    """建 SN 主档(device_sn 有 FK 约束到 devices.sn)"""
    if sn and db.get(Device, sn) is None:
        db.add(Device(sn=sn))
        db.flush()


def _ticket(db, tno, sn, item, *, cat=None, channel="平台", reg=None):
    _ensure_device(db, sn)
    db.add(ServiceTicket(
        ticket_no=tno, device_sn=sn, service_item=item, service_category=cat,
        channel=channel, register_time=reg or _utc(2026, 8, 1),
    ))
    db.flush()


def _ext(db, sn, cdate, *, mtno=None, platform="全国12315平台"):
    _ensure_device(db, sn)
    e = ExternalComplaint(
        device_sn=sn, complaint_date=cdate, matched_ticket_no=mtno,
        platform=platform,
    )
    db.add(e)
    db.flush()
    return e.id


# ══════════════════════════════════════════════════════════════════
# 一、判据收敛（#56 缺陷 2）
# ══════════════════════════════════════════════════════════════════
class TestComplaintCondConvergence:
    """`is_complaint_ticket_cond` 只认 category, 不再"乱查" item"""

    # ⚠️ 测试库可能残留其他用例的数据 → 所有断言都限定在本次构造的 ticket_no 上
    _MINE = ("T1", "T2", "T3", "T4")

    def _mine(self, db, cond):
        return sorted(db.scalars(
            select(ServiceTicket.ticket_no)
            .where(cond, ServiceTicket.ticket_no.in_(self._MINE))
        ).all())

    def test_blank_category_is_complaint(self, db):
        _ticket(db, "T1", "SN1", "商品投诉", cat=None)
        got = self._mine(db, is_complaint_ticket_cond(ServiceTicket.service_category))
        assert got == ["T1"]

    def test_explicit_complaint_category(self, db):
        _ticket(db, "T2", "SN2", "其他项", cat="投诉")
        got = self._mine(db, is_complaint_ticket_cond(ServiceTicket.service_category))
        assert got == ["T2"]

    def test_item_col_ignored_even_if_complaint_word(self, db):
        """★ 关键: category 有值(非'投诉') 时, item 含"投诉"也**不算**

        这是 #56 的核心收敛 —— 原实现会因 item ILIKE '%投诉%' 把它算进来。
        """
        _ticket(db, "T3", "SN3", "商品投诉", cat="退款")
        got = self._mine(db, is_complaint_ticket_cond(ServiceTicket.service_category))
        assert got == [], "category='退款' 不应算投诉(不看 item)"

    def test_item_col_param_deprecated_ignored(self, db):
        """传 item_col 也不改变结果(仅告警)"""
        _ticket(db, "T4", "SN4", "商品投诉", cat="退款")
        cond = is_complaint_ticket_cond(
            ServiceTicket.service_category, ServiceTicket.service_item
        )
        assert self._mine(db, cond) == []

    def test_blank_and_explicit_both_counted(self, db):
        """空白(cat=None) 与 显式'投诉' 应**同时**命中(两种口径都支持)"""
        _ticket(db, "T1", "SN1", "商品投诉", cat=None)
        _ticket(db, "T2", "SN2", "服务投诉", cat="投诉")
        _ticket(db, "T3", "SN3", "商品投诉", cat="退款")   # 不该命中
        got = self._mine(db, is_complaint_ticket_cond(ServiceTicket.service_category))
        assert got == ["T1", "T2"]


# ══════════════════════════════════════════════════════════════════
# 二、候选工单只含「外部投诉类」（#56 缺陷 1 —— CRITICAL）
# ══════════════════════════════════════════════════════════════════
class TestCandidatesOnlyExternal:
    """`_sn_candidates` 必须排除内部/商户号投诉工单"""

    def test_only_external_tickets_are_candidates(self, db):
        sn = "SNX"
        _ticket(db, "T_EXT", sn, "全国12315平台", cat=None)
        _ticket(db, "T_INT", sn, "商品投诉", cat=None)          # 内部
        _ticket(db, "T_MER", sn, "微信投诉", cat=None)          # 商户号
        _ticket(db, "T_NONCOMPLAINT", sn, "未领取体验套餐", cat="套餐疑问")

        m = _sn_candidates(db, {sn})
        tnos = {t for t, _ in m.get(sn, [])}
        assert tnos == {"T_EXT"}, "只应含外部投诉类工单, 实际 %s" % tnos

    def test_noncomplaint_ticket_excluded(self, db):
        """★ 复现缺陷 1: 原实现会把 `网慢/网卡/无网络`(cat=退款) 当候选"""
        sn = "SNY"
        _ticket(db, "T_SLOW", sn, "网慢/网卡/无网络", cat="退款")
        assert _sn_candidates(db, {sn}) == {}

    def test_empty_sns_returns_empty(self, db):
        assert _sn_candidates(db, set()) == {}


# ══════════════════════════════════════════════════════════════════
# 三、就近选代表（#47 语义保持）
# ══════════════════════════════════════════════════════════════════
class TestPickNearest:
    def test_picks_closest_by_date(self):
        c = [("A", _utc(2026, 1, 1)), ("B", _utc(2026, 8, 1))]
        tno, diff = _pick_nearest(c, _utc(2026, 7, 30))
        assert tno == "B" and diff == 2

    def test_none_ref_falls_back_to_earliest(self):
        c = [("A", _utc(2026, 1, 1)), ("B", _utc(2026, 8, 1))]
        assert _pick_nearest(c, None)[0] == "A"

    def test_empty_candidates(self):
        assert _pick_nearest(None, _utc(2026, 1, 1)) == (None, None)
        assert _pick_nearest([], _utc(2026, 1, 1)) == (None, None)

    def test_tie_prefers_earlier(self):
        c = [("EARLY", _utc(2026, 8, 1)), ("LATE", _utc(2026, 8, 5))]
        tno, _ = _pick_nearest(c, _utc(2026, 8, 3))
        assert tno == "EARLY", "同距应取更早登记的(结果稳定可复现)"


# ══════════════════════════════════════════════════════════════════
# 四、双边空悬（#56 缺陷 3）
# ══════════════════════════════════════════════════════════════════
class TestDoubleSidedOrphans:
    def test_doc_orphan_no_ticket(self, db):
        """文档有、工单无 → doc_orphan(漏登记)"""
        _ext(db, "SN_A", _utc(2026, 8, 1))
        r = external_reconcile(db, _utc(2026, 8, 1), _utc(2026, 8, 31))
        assert r["doc_total"] == 1
        assert r["doc_matched"] == 0
        assert r["doc_orphan"] == 1
        assert r["doc_orphan_rows"][0]["device_sn"] == "SN_A"

    def test_doc_matched_with_external_ticket(self, db):
        _ticket(db, "T1", "SN_B", "全国12315平台", cat=None,
                reg=_utc(2026, 8, 1))
        _ext(db, "SN_B", _utc(2026, 8, 2))
        r = external_reconcile(db, _utc(2026, 8, 1), _utc(2026, 8, 31))
        assert r["doc_matched"] == 1
        assert r["doc_orphan"] == 0

    def test_ticket_orphan_no_document(self, db):
        """工单有、文档无 → ticket_orphan(缺过程记录)"""
        _ticket(db, "T2", "SN_C", "全国12315平台", cat=None,
                reg=_utc(2026, 8, 1))
        r = external_reconcile(db, _utc(2026, 8, 1), _utc(2026, 8, 31))
        assert r["ticket_total"] == 1
        assert r["ticket_documented"] == 0
        assert r["ticket_orphan"] == 1
        assert r["ticket_orphan_rows"][0]["ticket_no"] == "T2"

    def test_sides_do_not_net_out(self, db):
        """★ 两侧不互相抵消(用户: 两边数据都有其意义)"""
        # 1 条文档无工单 + 1 条工单无文档 → 两个方向各计 1
        _ext(db, "SN_D1", _utc(2026, 8, 1))
        _ticket(db, "T3", "SN_D2", "全国12315平台", cat=None,
                reg=_utc(2026, 8, 1))
        r = external_reconcile(db, _utc(2026, 8, 1), _utc(2026, 8, 31))
        assert r["doc_orphan"] == 1
        assert r["ticket_orphan"] == 1
        assert r["doc_total"] == 1 and r["ticket_total"] == 1

    def test_internal_tickets_not_in_ticket_total(self, db):
        """内部/商户号投诉工单**不计入**对账基数"""
        _ticket(db, "T_INT", "SN_E", "商品投诉", cat=None)
        _ticket(db, "T_MER", "SN_F", "微信投诉", cat=None)
        r = external_reconcile(db, _utc(2026, 8, 1), _utc(2026, 8, 31))
        assert r["ticket_total"] == 0, "内部/商户号工单不参与外部对账"

    def test_coverage_rates(self, db):
        _ticket(db, "T1", "SN_G", "全国12315平台", cat=None,
                reg=_utc(2026, 8, 1))
        _ext(db, "SN_G", _utc(2026, 8, 1))
        _ext(db, "SN_H", _utc(2026, 8, 1))
        r = external_reconcile(db, _utc(2026, 8, 1), _utc(2026, 8, 31))
        assert r["coverage"]["doc_hit_rate"] == 0.5          # 1/2
        assert r["coverage"]["ticket_doc_rate"] == 1.0       # 1/1

    def test_detail_can_be_disabled(self, db):
        _ext(db, "SN_I", _utc(2026, 8, 1))
        r = external_reconcile(db, _utc(2026, 8, 1), _utc(2026, 8, 31),
                               include_detail=False)
        assert "doc_orphan_rows" not in r


# ══════════════════════════════════════════════════════════════════
# 五、重匹配（#56 缺陷 4 —— 工单晚到不补挂）
# ══════════════════════════════════════════════════════════════════
class TestRemarkMatched:
    def test_backfills_when_ticket_arrives_later(self, db):
        """★ 核心场景: 先入 315(空悬), 后入工单 → 重匹配应挂上"""
        eid = _ext(db, "SN_J", _utc(2026, 8, 1), mtno=None)

        # 工单"晚到"
        _ticket(db, "T_LATE", "SN_J", "全国12315平台", cat=None,
                reg=_utc(2026, 8, 1))

        stat = remark_matched(db, window_days=365, commit=False)["external"]
        assert stat["newly_matched"] == 1
        got = db.get(ExternalComplaint, eid)
        assert got.matched_ticket_no == "T_LATE"
        assert got.matched_at is not None

    def test_clears_illegal_old_match(self, db):
        """★ 旧匹配指向**非外部投诉类**工单 → 应被清空(避免污染统计)"""
        # 预置一个不合规的旧匹配
        _ticket(db, "T_BAD", "SN_K", "未领取体验套餐", cat="套餐疑问",
                reg=_utc(2026, 8, 1))
        eid = _ext(db, "SN_K", _utc(2026, 8, 1), mtno="T_BAD")

        stat = remark_matched(db, window_days=365, commit=False)["external"]
        assert stat["changed"] >= 1
        assert db.get(ExternalComplaint, eid).matched_ticket_no is None

    def test_idempotent(self, db):
        """★ 幂等: 重复执行不再产生变更"""
        _ticket(db, "T1", "SN_L", "全国12315平台", cat=None, reg=_utc(2026, 8, 1))
        _ext(db, "SN_L", _utc(2026, 8, 1))
        first = remark_matched(db, window_days=365, commit=False)["external"]
        second = remark_matched(db, window_days=365, commit=False)["external"]
        assert first["newly_matched"] == 1
        assert second["newly_matched"] == 0
        assert second["changed"] == 0

    def test_dry_run_does_not_write(self, db):
        _ticket(db, "T1", "SN_M", "全国12315平台", cat=None, reg=_utc(2026, 8, 1))
        eid = _ext(db, "SN_M", _utc(2026, 8, 1), mtno=None)
        stat = remark_matched(db, window_days=365, dry_run=True, commit=False)["external"]
        assert stat["newly_matched"] == 1
        assert db.get(ExternalComplaint, eid).matched_ticket_no is None

    def test_does_not_touch_other_fields(self, db):
        """★ 只写匹配字段, 不动其他字段(用户: 不能乱删乱改)"""
        _ticket(db, "T1", "SN_N", "全国12315平台", cat=None, reg=_utc(2026, 8, 1))
        eid = _ext(db, "SN_N", _utc(2026, 8, 1), mtno=None,
                   platform="航城监所")
        remark_matched(db, window_days=365, commit=False)
        row = db.get(ExternalComplaint, eid)
        assert row.platform == "航城监所"      # 未被改动
        assert row.complaint_date == _utc(2026, 8, 1)


# ══════════════════════════════════════════════════════════════════
# 六、工信部摘出为「(外)卡商」渠道（#57）
# ══════════════════════════════════════════════════════════════════
class TestCardChannelSplit:
    """用户: 「工信部投诉 应该是卡商投诉匹配, 从外部投诉摘出来 分开对账」"""

    def test_industry_gov_not_external(self, db):
        """★ 工信部不再算「外部投诉」"""
        from app.services.complaint_scope import (
            BUCKET_CARD_TICKET,
            classify_bucket_from_item,
        )
        assert classify_bucket_from_item("工信部投诉") == BUCKET_CARD_TICKET
        assert classify_bucket_from_item("工信部投诉") != "外部投诉"

    def test_other_platforms_stay_external(self, db):
        """其他平台仍是外部投诉(不被工信部摘出影响)"""
        from app.services.complaint_scope import (
            BUCKET_EXTERNAL,
            classify_bucket_from_item,
        )
        for item in ("全国12315平台", "公共服务平台", "消费者协会", "监督局"):
            assert classify_bucket_from_item(item) == BUCKET_EXTERNAL, item

    def test_industry_gov_priority_first(self, db):
        """工信部判定必须**先于**其他关键词(若 item 同时含两者)"""
        from app.services.complaint_scope import (
            BUCKET_CARD_TICKET,
            classify_bucket_from_item,
        )
        assert classify_bucket_from_item("工信部投诉-12315转办") == BUCKET_CARD_TICKET

    def test_card_ticket_not_in_external_candidates(self, db):
        """工信部工单不出现在**外部投诉**候选集里"""
        sn = "SN_CARD1"
        _ticket(db, "T_GX", sn, "工信部投诉", cat=None)
        assert _sn_candidates(db, {sn}) == {}, "工信部不应属外部投诉候选"

    def test_card_ticket_in_card_candidates(self, db):
        """工信部工单**应**出现在卡商候选集里"""
        from app.services.external_reconcile import _card_ticket_where
        sn = "SN_CARD2"
        _ticket(db, "T_GX", sn, "工信部投诉", cat=None)
        m = _sn_candidates(db, {sn}, where=_card_ticket_where())
        assert {t for t, _ in m.get(sn, [])} == {"T_GX"}

    def test_card_reconcile_double_sided(self, db):
        """卡商双边对账: 文档侧=card_complaints, 工单侧=工信部"""
        from app.models.card_complaint import CardComplaint
        from app.services.external_reconcile import card_reconcile

        # 有卡商文档 + 有工信部工单 → 已对账
        _ticket(db, "T_GX1", "SN_CD1", "工信部投诉", cat=None,
                reg=_utc(2026, 8, 1))
        _ensure_device(db, "SN_CD1")
        _ensure_device(db, "SN_CD2")
        db.add(CardComplaint(device_sn="SN_CD1", complaint_date=_utc(2026, 8, 2),
                             complaint_group="云客猫&思航"))
        # 只有卡商文档、无工单 → doc_orphan
        db.add(CardComplaint(device_sn="SN_CD2", complaint_date=_utc(2026, 8, 3),
                             complaint_group="小皮丘"))
        db.flush()

        r = card_reconcile(db, _utc(2026, 8, 1), _utc(2026, 8, 31))
        assert r["doc_total"] == 2
        assert r["doc_matched"] == 1
        assert r["doc_orphan"] == 1
        assert r["ticket_total"] == 1
        assert r["ticket_documented"] == 1

    def test_reconcile_all_splits_channels(self, db):
        """★ `reconcile_all` 返回两套: 外部投诉 与 卡商, 互不混入"""
        from app.models.card_complaint import CardComplaint
        from app.services.external_reconcile import reconcile_all

        _ticket(db, "T_EXT", "SN_RA1", "全国12315平台", cat=None,
                reg=_utc(2026, 8, 1))
        _ticket(db, "T_GX", "SN_RA2", "工信部投诉", cat=None,
                reg=_utc(2026, 8, 1))
        _ext(db, "SN_RA1", _utc(2026, 8, 1))
        _ensure_device(db, "SN_RA2")
        db.add(CardComplaint(device_sn="SN_RA2", complaint_date=_utc(2026, 8, 1),
                             complaint_group="云客猫"))
        db.flush()

        out = reconcile_all(db, _utc(2026, 8, 1), _utc(2026, 8, 31))
        assert out["external"]["ticket_total"] == 1, "外部渠道不应含工信部"
        assert out["card"]["ticket_total"] == 1, "卡商渠道应含工信部"
        assert out["card"]["doc_total"] == 1

    def test_remark_matched_covers_both_channels(self, db):
        """重匹配同时处理两条渠道, 返回值分 external / card"""
        from app.models.card_complaint import CardComplaint

        _ticket(db, "T_EXT", "SN_RM1", "全国12315平台", cat=None,
                reg=_utc(2026, 8, 1))
        _ticket(db, "T_GX", "SN_RM2", "工信部投诉", cat=None,
                reg=_utc(2026, 8, 1))
        eid = _ext(db, "SN_RM1", _utc(2026, 8, 1), mtno=None)
        _ensure_device(db, "SN_RM2")
        c = CardComplaint(device_sn="SN_RM2", complaint_date=_utc(2026, 8, 1),
                          complaint_group="云客猫")
        db.add(c)
        db.flush()

        stat = remark_matched(db, window_days=365, commit=False)
        assert stat["external"]["newly_matched"] == 1
        assert stat["card"]["newly_matched"] == 1
        assert db.get(ExternalComplaint, eid).matched_ticket_no == "T_EXT"
        db.refresh(c)
        assert c.matched_ticket_no == "T_GX"


# ══════════════════════════════════════════════════════════════════
# 七、监所/市监局误录工信部 → 软纠正 (#58)
# ══════════════════════════════════════════════════════════════════
class TestSupervisionMisclassified:
    """用户 2026-09-11:
    > 「目前 监督所 的哪些 都录入到了工信部 里面去了」
    > 「工信部的优先算作登记错误 与外部投诉表进行对账」
    """

    def _sup(self, db, sn, cdate=None):
        """建一条 315 监所类文档"""
        _ensure_device(db, sn)
        db.add(ExternalComplaint(
            device_sn=sn, complaint_date=cdate or _utc(2026, 8, 1),
            platform="航城监所",
        ))
        db.flush()

    def test_supervision_platform_cond(self, db):
        """判据能识别各类监所/市监局命名"""
        from app.services.complaint_scope import supervision_platform_cond
        r = db.execute(
            select(ExternalComplaint.platform)
            .where(supervision_platform_cond(ExternalComplaint.platform))
        ).scalars().all()
        for p in r:
            assert any(k in (p or "") for k in ("监所", "市监", "监督局"))

    def test_misclassified_goes_to_external(self, db):
        """★ 工信部工单 + SN 在315是监所 → 应算**外部投诉**"""
        from app.services.external_reconcile import _external_ticket_where
        sn = "SN_MIS1"
        _ticket(db, "T_GX", sn, "工信部投诉", cat=None)
        self._sup(db, sn)

        got = db.scalars(
            select(ServiceTicket.ticket_no)
            .where(_external_ticket_where(), ServiceTicket.device_sn == sn)
        ).all()
        assert got == ["T_GX"], "监所类工信部单应被纠正为外部投诉"

    def test_misclassified_not_card(self, db):
        """★ 被误录的**不应**再算卡商"""
        from app.services.external_reconcile import _card_ticket_where
        sn = "SN_MIS2"
        _ticket(db, "T_GX", sn, "工信部投诉", cat=None)
        self._sup(db, sn)

        got = db.scalars(
            select(ServiceTicket.ticket_no)
            .where(_card_ticket_where(), ServiceTicket.device_sn == sn)
        ).all()
        assert got == [], "误录项不应算卡商"

    def test_true_card_still_card(self, db):
        """★ 无监所证据的真卡商工信部单 → 仍算卡商"""
        from app.services.external_reconcile import _card_ticket_where
        sn = "SN_MIS3"
        _ticket(db, "T_GX", sn, "工信部投诉", cat=None)
        # 不建监所文档 → 无纠正依据
        got = db.scalars(
            select(ServiceTicket.ticket_no)
            .where(_card_ticket_where(), ServiceTicket.device_sn == sn)
        ).all()
        assert got == ["T_GX"], "无监所证据的应保留为卡商"

    def test_priority_supervision_over_card(self, db):
        """★ 两者都有时 **监所优先**(用户口径)"""
        from app.models.card_complaint import CardComplaint
        from app.services.external_reconcile import (
            _card_ticket_where,
            _external_ticket_where,
        )
        sn = "SN_MIS4"
        _ticket(db, "T_GX", sn, "工信部投诉", cat=None)
        self._sup(db, sn)                       # 有监所证据
        db.add(CardComplaint(device_sn=sn, complaint_date=_utc(2026, 8, 1),
                             complaint_group="云客猫"))
        db.flush()                              # 也有卡商记录

        in_ext = db.scalars(
            select(ServiceTicket.ticket_no)
            .where(_external_ticket_where(), ServiceTicket.device_sn == sn)
        ).all()
        in_card = db.scalars(
            select(ServiceTicket.ticket_no)
            .where(_card_ticket_where(), ServiceTicket.device_sn == sn)
        ).all()
        assert in_ext == ["T_GX"], "监所优先 → 算外部投诉"
        assert in_card == [], "不应同时算卡商"

    def test_normal_external_unaffected(self, db):
        """非监所的普通外部投诉不受影响"""
        from app.services.external_reconcile import _external_ticket_where
        sn = "SN_MIS5"
        _ticket(db, "T_EXT", sn, "全国12315平台", cat=None)
        got = db.scalars(
            select(ServiceTicket.ticket_no)
            .where(_external_ticket_where(), ServiceTicket.device_sn == sn)
        ).all()
        assert got == ["T_EXT"]

    def test_does_not_modify_source_data(self, db):
        """★ 软纠正**不改**原数据 —— service_item 保持 '工信部投诉'"""
        from app.services.external_reconcile import _external_ticket_where
        sn = "SN_MIS6"
        _ticket(db, "T_GX", sn, "工信部投诉", cat=None)
        self._sup(db, sn)
        # 触发查询
        db.scalars(select(ServiceTicket.ticket_no)
                   .where(_external_ticket_where())).all()
        row = db.get(ServiceTicket, "T_GX")
        assert row.service_item == "工信部投诉", "原始 service_item 不得被修改"
        assert row.service_category is None

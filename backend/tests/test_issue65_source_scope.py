"""#65 回归测试 —— 统计层接入 #58 软纠正 + 桶互斥完备性

## 背景

#58 的软纠正（工信部工单若 SN 属 315 监所/市监局类 → 实为监所投诉）
**只被 `external_reconcile.py` 调用** → 对账正确、**统计错误**。

实测（近 3 月）188 条工信部工单在统计层**两边都不算**：
外部桶排除它（#57 判定为卡商桶），而 `card_total` 又直接查
`card_complaints` 表（不取工单）→ 看板少 160 条**真实监所投诉**。

## 修复

软纠正**下沉到 `complaint_bucket_case(item_col, channel_col, sn_col)`**：
只要调用方传入 `sn_col` 即自动生效。全部统计口经
`analytics._complaint_source_type_case`（已默认传 `ServiceTicket.device_sn`）
统一走此处 → 一处修复，全域对齐。

## 本测试守护的三条不变量

1. **软纠正生效** —— 监所 SN 的工信部工单改判外部投诉
2. **不传 SN 时行为不变** —— 兼容 `metric_registry` 等不便取 SN 的调用点
3. **`effective_*` 与分桶完全等价** —— 二者现共用同一段 CASE，
   本测试防其未来再次漂移（即 #65 的根因）

⚠️ 测试用 SN 均为**虚构**（`86987654321xxxx`），不含真实设备号。

⚠️ `service_tickets.device_sn` / `external_complaints.device_sn` 均有
   **FK 约束到 `devices.sn`** → 插入前必须先建设备主档（`_ensure_device`）。
   本文件用真实 PostgreSQL 测试库（非 SQLite：BigInteger 主键不自增）。
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import func, select

from app.models.device import Device
from app.models.external_complaint import ExternalComplaint
from app.models.ticket import ServiceTicket
from app.services.complaint_scope import (
    ALL_BUCKETS_WITH_CARD,
    BUCKET_CARD_TICKET,
    BUCKET_EXTERNAL,
    BUCKET_INTERNAL,
    BUCKET_MERCHANT,
    complaint_bucket_case,
    effective_card_ticket_cond,
    effective_external_ticket_cond,
    is_complaint_ticket_cond,
)

# 虚构 SN（避免真实设备号入库）
_SN_SUPERVISION = "869876543210001"   # 在 315 属监所类
_SN_PURE_CARD = "869876543210002"     # 不在 315 → 真卡商
_SN_12315 = "869876543210003"
_SN_INTERNAL = "869876543210004"
_SN_SUP2 = "869876543210005"
_SN_PURE2 = "869876543210006"

_DOC_DAY = datetime(2026, 7, 1)


def _ensure_device(db, sn: str) -> None:
    """建 SN 主档 —— `service_tickets.device_sn` 有 FK 约束到 `devices.sn`。"""
    if sn and db.get(Device, sn) is None:
        db.add(Device(sn=sn))
        db.flush()


def _mk_ticket(db, no: str, sn: str, item: str, category=None) -> ServiceTicket:
    """建投诉类工单（category 留空 = 投诉）。"""
    _ensure_device(db, sn)
    t = ServiceTicket(
        ticket_no=no, device_sn=sn, service_category=category,
        service_item=item, channel="H5", status="待处理",
    )
    db.add(t)
    return t


def _mk_doc(db, sn: str, platform: str) -> None:
    """建 315 文档记录（platform 决定是否监所类）。"""
    _ensure_device(db, sn)
    db.add(ExternalComplaint(
        device_sn=sn, platform=platform, complaint_date=_DOC_DAY,
    ))


def _bucket(db, no: str, *, with_sn: bool) -> str:
    """取指定工单的桶值（with_sn 控制是否启用软纠正）。"""
    if with_sn:
        expr = complaint_bucket_case(
            ServiceTicket.service_item, ServiceTicket.channel, ServiceTicket.device_sn
        )
    else:
        expr = complaint_bucket_case(ServiceTicket.service_item, ServiceTicket.channel)
    return db.scalar(select(expr).where(ServiceTicket.ticket_no == no))


class TestSoftCorrection:
    """软纠正生效条件"""

    def test_supervision_sn_redirected_to_external(self, db):
        """SN ∈ 监所类 → 工信部工单改判「外部投诉」"""
        _mk_ticket(db, "TC65-1", _SN_SUPERVISION, "工信部投诉")
        _mk_doc(db, _SN_SUPERVISION, "航城监所")
        db.flush()

        assert _bucket(db, "TC65-1", with_sn=True) == BUCKET_EXTERNAL

    def test_no_sn_param_keeps_legacy_behavior(self, db):
        """不传 sn_col → 保持历史行为（仍为卡商桶）

        兼容 `metric_registry_service` 等不便取 SN 列的调用点。
        """
        _mk_ticket(db, "TC65-2", _SN_SUPERVISION, "工信部投诉")
        _mk_doc(db, _SN_SUPERVISION, "航城监所")
        db.flush()

        assert _bucket(db, "TC65-2", with_sn=False) == BUCKET_CARD_TICKET

    def test_pure_card_sn_stays_card(self, db):
        """SN ∉ 监所类 → 仍为卡商桶（真卡商）"""
        _mk_ticket(db, "TC65-3", _SN_PURE_CARD, "工信部投诉")
        db.flush()

        assert _bucket(db, "TC65-3", with_sn=True) == BUCKET_CARD_TICKET

    @pytest.mark.parametrize("platform", [
        "航城监所", "南沙市监局", "宝安市监局", "武汉市监局", "市场监督管理局",
    ])
    def test_all_supervision_keywords(self, db, platform):
        """#58 关键词全表覆盖（含「市监局」「市场监督管理局」等变体）"""
        sn = f"8698765{abs(hash(platform)) % 10**8:08d}"
        no = f"TC65P-{abs(hash(platform)) % 10**6:06d}"
        _mk_ticket(db, no, sn, "工信部投诉")
        _mk_doc(db, sn, platform)
        db.flush()

        assert _bucket(db, no, with_sn=True) == BUCKET_EXTERNAL

    def test_non_supervision_platform_not_corrected(self, db):
        """315 平台非监所类（如 12315）→ 不纠正，仍为卡商"""
        _mk_ticket(db, "TC65-4", _SN_12315, "工信部投诉")
        _mk_doc(db, _SN_12315, "全国12315平台")
        db.flush()

        assert _bucket(db, "TC65-4", with_sn=True) == BUCKET_CARD_TICKET

    def test_correction_does_not_affect_non_card_items(self, db):
        """软纠正只作用于卡商桶，不得误伤外部/内部/商户号"""
        _mk_ticket(db, "TC65-5", _SN_12315, "12315")
        _mk_ticket(db, "TC65-6", _SN_INTERNAL, "商品投诉")
        _mk_doc(db, _SN_12315, "航城监所")     # 该 SN 也在监所文档里
        _mk_doc(db, _SN_INTERNAL, "航城监所")
        db.flush()

        # 已是外部投诉 → 仍是外部投诉（幂等）
        assert _bucket(db, "TC65-5", with_sn=True) == BUCKET_EXTERNAL
        # 内部投诉 → 不受影响
        assert _bucket(db, "TC65-6", with_sn=True) == BUCKET_INTERNAL


class TestEffectiveParity:
    """`effective_*` 与分桶必须完全等价（防再次漂移 —— #65 根因）"""

    @pytest.fixture
    def dataset(self, db):
        for i, (item, sn, plat) in enumerate([
            ("工信部投诉", _SN_SUPERVISION, "航城监所"),   # 纠正 → 外部
            ("工信部投诉", _SN_PURE_CARD, None),           # 真卡商
            ("工信部投诉", _SN_SUP2, "南沙市监局"),        # 纠正 → 外部
            ("工信部投诉", _SN_PURE2, "全国12315平台"),    # 非监所 → 卡商
            ("12315", _SN_12315, None),                    # 原生外部
            ("商品投诉", _SN_INTERNAL, None),              # 内部
        ]):
            _mk_ticket(db, f"TC65-E{i}", sn, item)
            if plat:
                _mk_doc(db, sn, plat)
        db.flush()
        return db

    def _effective(self, db, cond_fn) -> set[str]:
        return set(db.scalars(select(ServiceTicket.ticket_no).where(
            is_complaint_ticket_cond(ServiceTicket.service_category),
            cond_fn,
        )).all())

    def _by_bucket(self, db, bucket: str) -> set[str]:
        return set(db.scalars(select(ServiceTicket.ticket_no).where(
            is_complaint_ticket_cond(ServiceTicket.service_category),
            complaint_bucket_case(
                ServiceTicket.service_item, ServiceTicket.channel, ServiceTicket.device_sn
            ) == bucket,
        )).all())

    def test_external_equivalent(self, dataset):
        eff = self._effective(dataset, effective_external_ticket_cond(
            ServiceTicket.device_sn, ServiceTicket.service_category,
            ServiceTicket.service_item, ServiceTicket.channel))
        bkt = self._by_bucket(dataset, BUCKET_EXTERNAL)
        assert eff == bkt, f"外部口径漂移: effective={eff} bucket={bkt}"

    def test_card_equivalent(self, dataset):
        eff = self._effective(dataset, effective_card_ticket_cond(
            ServiceTicket.device_sn, ServiceTicket.service_category,
            ServiceTicket.service_item, ServiceTicket.channel))
        bkt = self._by_bucket(dataset, BUCKET_CARD_TICKET)
        assert eff == bkt, f"卡商口径漂移: effective={eff} bucket={bkt}"

    def test_external_and_card_disjoint(self, dataset):
        """外部与卡商必须互斥（同一工单不得双算）"""
        ext = self._by_bucket(dataset, BUCKET_EXTERNAL)
        card = self._by_bucket(dataset, BUCKET_CARD_TICKET)
        assert not (ext & card), f"双算: {ext & card}"

    def test_union_covers_raw_split(self, dataset):
        """外部 ∪ 卡商 == 原始卡商桶 ∪ 原始外部桶（拆分完备，无遗漏）

        ★ 这是 #65 的核心不变量 —— 修复前 188 条工单
          落在「外部桶排除、卡商表又查不到」的黑洞里。
        """
        raw_card = set(dataset.scalars(select(ServiceTicket.ticket_no).where(
            is_complaint_ticket_cond(ServiceTicket.service_category),
            complaint_bucket_case(ServiceTicket.service_item, ServiceTicket.channel)
            == BUCKET_CARD_TICKET,
        )).all())
        raw_ext = set(dataset.scalars(select(ServiceTicket.ticket_no).where(
            is_complaint_ticket_cond(ServiceTicket.service_category),
            complaint_bucket_case(ServiceTicket.service_item, ServiceTicket.channel)
            == BUCKET_EXTERNAL,
        )).all())
        split = (self._by_bucket(dataset, BUCKET_EXTERNAL)
                 | self._by_bucket(dataset, BUCKET_CARD_TICKET))
        assert split == (raw_card | raw_ext), (
            f"拆分不完备(黑洞): 丢失 {raw_card | raw_ext - split}"
        )


class TestBucketCompleteness:
    """四桶互斥完备"""

    @pytest.mark.parametrize("item,expect", [
        ("工信部投诉", BUCKET_CARD_TICKET),
        ("12315", BUCKET_EXTERNAL),
        ("微信投诉", BUCKET_MERCHANT),
        ("商品投诉", BUCKET_INTERNAL),
        ("服务投诉", BUCKET_INTERNAL),
        ("套餐扣费申诉", BUCKET_INTERNAL),
        ("", BUCKET_INTERNAL),
    ])
    def test_item_maps_to_expected_bucket(self, db, item, expect):
        """各种 service_item 的桶归属（不传 SN，纯 item 判据）"""
        _mk_ticket(db, "TC65-C1", "869876543210099", item)
        db.flush()

        assert _bucket(db, "TC65-C1", with_sn=False) == expect

    def test_bucket_sum_equals_ticket_total(self, db):
        """四桶计数之和 == 投诉类工单总数（无遗漏、无重叠）"""
        for i in range(8):
            _mk_ticket(db, f"TC65-S{i}", f"86987654321{i:04d}",
                       ["工信部投诉", "12315", "微信投诉", "商品投诉"][i % 4])
        _mk_doc(db, "869876543210000", "航城监所")
        db.flush()

        total = db.scalar(select(func.count()).select_from(ServiceTicket).where(
            is_complaint_ticket_cond(ServiceTicket.service_category))) or 0
        s = sum(
            db.scalar(select(func.count()).select_from(ServiceTicket).where(
                is_complaint_ticket_cond(ServiceTicket.service_category),
                complaint_bucket_case(
                    ServiceTicket.service_item, ServiceTicket.channel, ServiceTicket.device_sn
                ) == b,
            )) or 0
            for b in ALL_BUCKETS_WITH_CARD
        )
        assert s == total, f"桶合计 {s} != 投诉工单总数 {total}"

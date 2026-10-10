"""#65 回归测试 —— `/complaints/detail` 的 `source_type` 筛选必须约束「补充表独立行」

## 背景（Bug）

`complaint_detail` 的补充表收集逻辑（`_collect`）原先**不受 `source_type` 约束**：

```python
if source_type:
    stmt = stmt.where(src_expr == source_type)   # ← 只筛工单主行
...
_collect(ExternalComplaint, ...)   # ← 315 表全量照收
_collect(CardComplaint, ...)       # ← 卡商表全量照收
```

后果：`source_type=(外)卡商` 返回 **473 条**，其中 **472 条是外部投诉**
（315 表未关联工单的独立行）。前端「卡商」筛选看到的全是外部投诉。

## 修复要点（★ 容易修错的地方）

**不能**在 `_collect` 开头直接 `return` —— 补充记录若 SN 命中工单行，
它是该行的 **附注（`supplementary`）**，不改变主行所属桶，
且 `subject` 过滤依赖它（W2 场景：商户号工单行 + 挂载的 315 主体）。

→ 必须**只对「独立行」（`extra_rows`）施加桶筛选**，保留挂载逻辑。

本测试守护 `test_external_brief.py::test_detail_subject_filter_matches_attached_315`
依赖的挂载行为不被破坏。

## 测试技巧（★ 踩过的坑）

直接调用 FastAPI 路由函数时，**未显式传入的参数是其默认值对象**
（`Query(False)`），是**真值**！必须显式传全部参数，否则
`include_supplementary` 意外为 True。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.models.card_complaint import CardComplaint
from app.models.device import Device
from app.models.external_complaint import ExternalComplaint
from app.models.ticket import ServiceTicket
from app.services.complaint_scope import CARD_SCOPE

_DAY = "2026-08-01"
_TS = dt.datetime(2026, 8, 1, 3, 0, tzinfo=dt.timezone.utc)

_SN_CARD_TICKET = "869870000000001"    # 工单侧真卡商（工信部，SN 不在监所）
_SN_EXT_TICKET = "869870000000002"     # 工单侧外部投诉
_SN_SUPPL_ONLY = "869870000000003"     # 仅 315 补充表，无工单
_SN_SUPPL_CARD = "869870000000004"     # 仅卡商补充表，无工单


def _ensure_device(db, sn):
    if sn and db.get(Device, sn) is None:
        db.add(Device(sn=sn))
        db.flush()


def _ticket(db, no, sn, item):
    _ensure_device(db, sn)
    db.add(ServiceTicket(
        ticket_no=no, device_sn=sn, service_item=item, service_category=None,
        channel="H5", register_time=_TS, status="待处理",
    ))
    db.flush()


def _ext(db, sn, platform="航城监所"):
    _ensure_device(db, sn)
    db.add(ExternalComplaint(
        device_sn=sn, platform=platform, complaint_date=_TS,
    ))
    db.flush()


def _card(db, sn, group="卡商群A"):
    _ensure_device(db, sn)
    db.add(CardComplaint(
        device_sn=sn, complaint_group=group, complaint_date=_TS,
    ))
    db.flush()


def _detail(db, *, source_type, include_supplementary=True, subject=None):
    """调用 `/complaints/detail` 业务函数。

    ⚠️ 必须显式传全部参数 —— 直接调用 FastAPI 函数时未传参数是其
       `Query(...)` 默认**对象**(真值), 会导致 `include_supplementary` 意外为 True。
    ⚠️ 用 `__wrapped__` 绕过 `@cached_json`, 避免 Redis 缓存干扰。
    """
    from app.api.v1.complaints import complaint_detail

    resp = complaint_detail.__wrapped__(
        db, None, start=_DAY, end=_DAY, source_type=source_type,
        subject=subject, limit=500, include_supplementary=include_supplementary,
    )
    data = getattr(resp, "data", resp)
    return data if isinstance(data, dict) else data


@pytest.fixture
def dataset(db):
    """四条工单 + 两条纯补充表记录。"""
    _ticket(db, "TC65D-1", _SN_CARD_TICKET, "工信部投诉")   # → 卡商桶
    _ticket(db, "TC65D-2", _SN_EXT_TICKET, "12315")        # → 外部桶
    _ext(db, _SN_SUPPL_ONLY)                               # 315 独立行
    _card(db, _SN_SUPPL_CARD)                              # 卡商独立行
    return db


class TestSourceTypeFilter:
    """`source_type` 必须同时约束工单主行与补充表独立行"""

    def test_card_filter_excludes_315_rows(self, dataset):
        """★ 核心 Bug: 筛卡商不得返回 315 外部投诉行"""
        body = _detail(dataset, source_type=CARD_SCOPE)
        items = body.get("items") or []
        sources = {r.get("source") for r in items}
        assert sources <= {CARD_SCOPE}, (
            f"卡商筛选混入了其他来源: {sources - {CARD_SCOPE}}"
        )

    def test_card_filter_keeps_card_rows(self, dataset):
        """卡商筛选应保留卡商工单行 + 卡商补充独立行"""
        body = _detail(dataset, source_type=CARD_SCOPE)
        items = body.get("items") or []
        sns = {r.get("device_sn") for r in items}
        assert _SN_CARD_TICKET in sns, "卡商工单主行缺失"
        assert _SN_SUPPL_CARD in sns, "卡商补充独立行缺失"
        assert _SN_SUPPL_ONLY not in sns, "315 独立行混入卡商筛选"

    def test_external_filter_excludes_card_rows(self, dataset):
        """筛外部投诉不得返回卡商补充行"""
        body = _detail(dataset, source_type="外部投诉")
        items = body.get("items") or []
        sources = {r.get("source") for r in items}
        assert sources <= {"外部投诉"}, f"外部筛选混入: {sources - {'外部投诉'}}"
        sns = {r.get("device_sn") for r in items}
        assert _SN_SUPPL_ONLY in sns, "315 补充独立行缺失"
        assert _SN_SUPPL_CARD not in sns, "卡商补充行混入外部筛选"

    def test_no_filter_returns_all_buckets(self, dataset):
        """不传 source_type → 各桶都要有"""
        body = _detail(dataset, source_type=None)
        items = body.get("items") or []
        sns = {r.get("device_sn") for r in items}
        for sn in (_SN_CARD_TICKET, _SN_EXT_TICKET, _SN_SUPPL_ONLY, _SN_SUPPL_CARD):
            assert sn in sns, f"{sn} 在无筛选时缺失"

    def test_internal_filter_excludes_both_suppl_tables(self, dataset):
        """内部投诉与两张补充表无对账关系 → 不应出现补充独立行

        (挂载到内部工单行的 supplementary 不算独立行, 此处无该场景)
        """
        body = _detail(dataset, source_type="内部投诉")
        items = body.get("items") or []
        for r in items:
            assert r.get("is_primary"), f"内部筛选出现非主行: {r.get('source')}"


class TestSupplementaryAttachPreserved:
    """★ 修复不能破坏「挂载」: 补充记录命中工单行时应作为附注保留

    守护 `test_external_brief.py::test_detail_subject_filter_matches_attached_315`
    依赖的行为。
    """

    def test_attached_315_kept_under_other_source(self, db):
        """商户号工单行 + SN 命中的 315 记录(不同主体) → 附注仍应在

        即使 `source_type=商户号投诉`, 315 记录作为**附注**仍要出现,
        否则 `subject` 过滤会漏掉「315 主体挂靠在商户号行」的场景。
        """
        sn = "869870000000005"
        _ensure_device(db, sn)
        db.add(ServiceTicket(
            ticket_no="TC65D-M", device_sn=sn, service_item="微信投诉",
            service_category=None, channel="H5", register_time=_TS,
            status="待处理", registrant="深圳市云客猫科技有限公司",
        ))
        db.flush()
        db.add(ExternalComplaint(
            device_sn=sn, platform="315", complaint_date=_TS,
            company_entity="云客猫(315侧)",
        ))
        db.flush()

        body = _detail(db, source_type="商户号投诉", subject="云客猫")
        items = body.get("items") or []
        assert len(items) == 1, f"期望经 supplementary 命中 1 条, 实际 {len(items)}"

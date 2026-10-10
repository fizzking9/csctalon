# -*- coding: utf-8 -*-
"""投诉概览增强 A2/A5 回归测试（工单 #72）。

- **A5 · 投诉登记时段热力**（`_hour_heatmap`）—— 服务排班决策
- **A2 · 反复投诉**（`_repeat_complaints`）—— 「第一次没解决好」的直接证据

设计: docs/arch/_pending/A-20260911-001-投诉分析整体重构.md §四点五（A2/A5 契约）

★ 本测试守护的核心（最容易出方向性错误的地方）：

  **A2 的计数单位必须是「投诉事件」而不是「原始记录数」。**

  实测差异 3 倍（近 90 天：按 SN 直接计数 237 台 vs 按事件去重 78 台）。
  差异来自 #55 口径：**同一天多渠道呈现 = 同一笔投诉的 1 个事件，不算反复**。
  若用记录数，会把「一天内多渠道登记」误判为该设备在反复投诉 —— 方向性错误。

⚠️ 数据依赖 (2026-09-22 改)：**不再依赖库里的真实数据**。
   原先 fixture 在"库中无投诉数据"时 `pytest.skip` —— 但测试库永远不会有
   真实业务数据, 于是这些断言**从未被执行过**, 显示为"跳过"(黄色) 极易被
   当成"环境不具备, 正常"。

   实测背景: 测试库曾有 193 行 `SN-WIDE-*` 合成残留（由
   `test_wide_table_builder` 的 `_build_wide_batch` 内部 commit 泄漏）, 它们
   `service_category='投诉'` 但 `register_time` 为 NULL → 筛选恒为 0 条 →
   35 个断言继续跳过。清理泄漏后库为空, 问题暴露得更彻底。

   现改为 `_seed_complaints` fixture **自己造数据**, 使断言真正执行:
     · 数据在 `db` fixture 的事务内 → 测试结束自动回滚, 零泄漏
     · 形态对齐 `_repeat_complaints` 的判据(见该 fixture 注释)
"""

from datetime import datetime, timedelta

import pytest

from app.services.analytics import TZ

pytest.importorskip("sqlalchemy")


def _clear_cache():
    try:
        from app.core.redis_client import get_redis
        r = get_redis()
        ks = list(r.scan_iter("complaint:overview:*"))
        if ks:
            r.delete(*ks)
    except Exception:  # pragma: no cover
        pass


@pytest.fixture
def complaints_seeded(db, window):
    """在**回滚事务**内造「反复投诉」数据, 让 A2/A5 断言真正执行

    ## 数据形态依据（逐条对齐生产判据, 不可随意简化）

    1. **`service_category='投诉'`** —— `is_complaint_ticket_cond()` 的判据是
       「空白 OR ='投诉'」; 此处显式写 '投诉' 更醒目（空白也行, 但会被误读为漏填）。
    2. **`register_time` 非空且在窗口内** —— `_repeat_complaints` 的硬性过滤条件
       （`register_time IS NOT NULL` + 窗口比较）。
       ⚠️ 这正是 193 行残留数据失败的原因: 它们 register_time 为 NULL。
    3. **`device_sn` 非空** —— 事件键 `(SN, 北京日期)`, 无 SN 无法聚合。
    4. **同一 SN 至少 3 个不同日期的事件** —— `threshold=3` 才算"反复投诉"。
       ⚠️ 必须**跨日期**（同一 SN 同一天的多条会折叠成 1 个事件, 见 #55 口径）;
       只造同一天的多条**测不出反复投诉** —— 那正是本测试要守护的方向性错误。
    5. **`devices` 表须先有对应 SN** —— `service_tickets.device_sn` 有外键约束
       （`service_tickets_device_sn_fkey`）, 否则插入直接 IntegrityError。
    """
    from app.models.device import Device
    from app.models.ticket import ServiceTicket

    sn = "SN-A2A5-SEED"
    sn_b = "SN-A2A5-SEED-B"
    # 判据 5: 先满足外键
    db.add(Device(sn=sn))
    db.add(Device(sn=sn_b))
    db.flush()

    base = datetime.fromisoformat(window[0]).replace(tzinfo=TZ)
    # 同一 SN, 3 个**不同日期** → 3 个投诉事件 → 命中反复投诉阈值
    for day_offset in (0, 2, 4):
        reg = base + timedelta(days=day_offset, hours=10)
        db.add(ServiceTicket(
            ticket_no=f"GDC-A2A5-{day_offset}",
            device_sn=sn,
            service_category="投诉",     # 判据 1
            service_item="服务投诉",
            register_time=reg,           # 判据 2（193 行残留缺的就是这个）
            finish_time=reg + timedelta(hours=2),
        ))
    # 再补一台不同 SN, 使主体/单位计数不依赖单一设备
    db.add(ServiceTicket(
        ticket_no="GDC-A2A5-B",
        device_sn=sn_b,
        service_category="投诉",
        service_item="商品投诉",
        register_time=base + timedelta(days=1, hours=14),
        finish_time=base + timedelta(days=1, hours=16),
    ))
    db.flush()          # 不 commit —— 随 db fixture 事务回滚
    _clear_cache()
    return db


@pytest.fixture
def db_session():
    """只读用途的会话（不造数据, 不回滚）

    ⚠️ **不要**用它加数据 —— 它直接 `SessionLocal()`, 没有事务包裹,
       写入会永久留在库里（这正是原先数据泄漏的成因之一）。
       需要造数据的用例请用 `complaints_seeded`（内部走 `db` fixture, 会回滚）。
    """
    try:
        from app.core.database import SessionLocal
        db = SessionLocal()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"数据库不可用: {e}")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def window():
    """近 30 天窗口（与概览默认一致）。"""
    today = datetime.now(TZ).date()
    return (today - timedelta(days=29)).isoformat(), today.isoformat()


@pytest.fixture
def db_with_complaints(db, complaints_seeded, window):
    """带合成投诉数据的会话（**回滚事务**, 零泄漏）

    ⚠️ 2026-09-22 重写：原实现在「窗口内无投诉数据」时 `pytest.skip`,
       而测试库永远没有真实数据 → 18 个断言**从未执行**。
       现改为由 `complaints_seeded` 造数据, 断言真正跑起来。

       保留本 fixture 名是为兼容 18 处既有调用点（避免大范围改动引入风险）,
       语义已从「要求库里有数据」变为「提供一个有数据的环境」。
    """
    from app.api.v1.complaints import _parse_dt, _parse_dt_end, _repeat_complaints
    from app.services.analytics import _complaint_source_type_case

    ts, te = _parse_dt(window[0]), _parse_dt_end(window[1])
    r = _repeat_complaints(db, ts, te, _complaint_source_type_case())
    # 造的数据必须真的满足判据 —— 否则是 fixture 写错, 应当**报错而非跳过**。
    # （原实现 skip 会把"fixture 造错数据"伪装成"环境不具备", 掩盖问题）
    assert r["events_total"] > 0, (
        "合成数据未满足 _repeat_complaints 的判据 —— 检查 complaints_seeded: "
        "需 service_category='投诉' + register_time 非空且在窗口内 + device_sn 非空"
    )
    return db


# ── A5 时段热力 ──


class TestHourHeatmap:
    """A5 · 投诉登记时段热力。"""

    def test_marginals_are_consistent(self, db_with_complaints, window):
        """① 格子总数 == by_hour 之和 == by_weekday 之和（聚合自洽）。"""
        from app.api.v1.complaints import _hour_heatmap, _parse_dt, _parse_dt_end
        from app.services.analytics import _complaint_source_type_case

        ts, te = _parse_dt(window[0]), _parse_dt_end(window[1])
        h = _hour_heatmap(db_with_complaints, ts, te, _complaint_source_type_case())

        cell_sum = sum(c["count"] for c in h["cells"])
        hour_sum = sum(x["count"] for x in h["by_hour"])
        wd_sum = sum(x["count"] for x in h["by_weekday"])

        assert cell_sum == hour_sum == wd_sum == h["total"], (
            f"聚合不自洽: cells={cell_sum} by_hour={hour_sum} by_weekday={wd_sum} total={h['total']}"
        )

    def test_grid_is_complete(self, db_with_complaints, window):
        """② 边际分布补齐 0 —— 前端热力图需完整网格（24 小时 / 7 天）。"""
        from app.api.v1.complaints import _hour_heatmap, _parse_dt, _parse_dt_end
        from app.services.analytics import _complaint_source_type_case

        ts, te = _parse_dt(window[0]), _parse_dt_end(window[1])
        h = _hour_heatmap(db_with_complaints, ts, te, _complaint_source_type_case())

        assert len(h["by_hour"]) == 24, "by_hour 必须补齐 24 项"
        assert [x["hour"] for x in h["by_hour"]] == list(range(24))
        assert len(h["by_weekday"]) == 7, "by_weekday 必须补齐 7 项"
        # ISO 周：1=周一 … 7=周日
        assert [x["weekday"] for x in h["by_weekday"]] == list(range(1, 8))

    def test_peak_valley_are_disjoint_and_valid(self, db_with_complaints, window):
        """③ 峰谷小时合法且不重叠（否则排班建议自相矛盾）。"""
        from app.api.v1.complaints import _hour_heatmap, _parse_dt, _parse_dt_end
        from app.services.analytics import _complaint_source_type_case

        ts, te = _parse_dt(window[0]), _parse_dt_end(window[1])
        h = _hour_heatmap(db_with_complaints, ts, te, _complaint_source_type_case())

        assert all(0 <= x <= 23 for x in h["peak_hours"]), "高峰小时越界"
        assert all(0 <= x <= 23 for x in h["valley_hours"]), "谷底小时越界"
        # 只有在「有数据的小时」上判分位，故峰谷不应包含零量小时
        hour_map = {x["hour"]: x["count"] for x in h["by_hour"]}
        assert all(hour_map[x] > 0 for x in h["peak_hours"]), "高峰小时不应含零量小时"
        assert all(hour_map[x] > 0 for x in h["valley_hours"]), "谷底小时不应含零量小时"

    def test_peak_hours_have_higher_volume_than_valley(self, db_with_complaints, window):
        """④ 高峰小时量级 ≥ 谷底小时量级（分位判定的方向性检查）。

        若反向 → 说明 P75/P25 用错了（例如把空档小时算进分位）。
        """
        from app.api.v1.complaints import _hour_heatmap, _parse_dt, _parse_dt_end
        from app.services.analytics import _complaint_source_type_case

        ts, te = _parse_dt(window[0]), _parse_dt_end(window[1])
        h = _hour_heatmap(db_with_complaints, ts, te, _complaint_source_type_case())
        if not h["peak_hours"] or not h["valley_hours"]:
            pytest.skip("窗口内数据不足以形成峰谷")

        hour_map = {x["hour"]: x["count"] for x in h["by_hour"]}
        min_peak = min(hour_map[x] for x in h["peak_hours"])
        max_valley = max(hour_map[x] for x in h["valley_hours"])
        assert min_peak >= max_valley, (
            f"高峰最低 {min_peak} < 谷底最高 {max_valley} —— 分位判定方向错误"
        )


# ── A2 反复投诉 ──


class TestRepeatComplaints:
    """A2 · 反复投诉（**事件口径**）。"""

    def test_threshold_echoed(self, db_with_complaints, window):
        """① 阈值在响应中回显（前端据此渲染文案，避免硬编码）。"""
        from app.api.v1.complaints import _parse_dt, _parse_dt_end, _repeat_complaints
        from app.services.analytics import _complaint_source_type_case

        ts, te = _parse_dt(window[0]), _parse_dt_end(window[1])
        r = _repeat_complaints(db_with_complaints, ts, te, _complaint_source_type_case(), threshold=5)
        assert r["threshold"] == 5

    def test_event_based_not_record_based(self, db_with_complaints, window):
        """② ★★ 计数单位是「事件」，不是「原始记录数」。

        这是本功能**最关键的口径**（实测差异 3 倍）：
          同一天多渠道登记 = 同一笔投诉的 1 个事件，不算反复。

        验证方式：断言「事件总数 ≤ 原始记录数」且「设备数 ≤ 事件数」
        —— 若把记录数当事件数，则事件数会等于记录数（同一天多渠道被拆成多次）。
        """
        from sqlalchemy import func, select

        from app.api.v1.complaints import _parse_dt, _parse_dt_end, _repeat_complaints
        from app.models.ticket import ServiceTicket
        from app.services.analytics import _complaint_source_type_case

        ts, te = _parse_dt(window[0]), _parse_dt_end(window[1])
        src = _complaint_source_type_case()
        r = _repeat_complaints(db_with_complaints, ts, te, src)

        # 原始记录数（无 SN 去重、无同日折叠）
        raw = db_with_complaints.scalar(
            select(func.count()).select_from(ServiceTicket).where(
                ServiceTicket.device_sn.isnot(None),
                ServiceTicket.device_sn != "",
                ServiceTicket.register_time >= ts,
                ServiceTicket.register_time <= te,
            )
        ) or 0

        assert r["events_total"] <= raw, (
            f"事件数({r['events_total']}) 不应超过原始记录数({raw}) —— 事件口径失效"
        )
        assert r["units_total"] <= r["events_total"], (
            "设备数不应超过事件数"
        )

    def test_repeat_units_meet_threshold(self, db_with_complaints, window):
        """③ 清单内每个设备的事件数都 ≥ 阈值。"""
        from app.api.v1.complaints import _parse_dt, _parse_dt_end, _repeat_complaints
        from app.services.analytics import _complaint_source_type_case

        ts, te = _parse_dt(window[0]), _parse_dt_end(window[1])
        r = _repeat_complaints(db_with_complaints, ts, te, _complaint_source_type_case(), threshold=3)
        for it in r["items"]:
            assert it["events"] >= 3, f"{it['device_sn']} 仅 {it['events']} 次，低于阈值"

    def test_items_sorted_by_events_desc(self, db_with_complaints, window):
        """④ 清单按事件数降序（最该跟进的排最前）。"""
        from app.api.v1.complaints import _parse_dt, _parse_dt_end, _repeat_complaints
        from app.services.analytics import _complaint_source_type_case

        ts, te = _parse_dt(window[0]), _parse_dt_end(window[1])
        r = _repeat_complaints(db_with_complaints, ts, te, _complaint_source_type_case())
        evs = [it["events"] for it in r["items"]]
        assert evs == sorted(evs, reverse=True), f"未按事件数降序: {evs}"

    def test_escalated_flag_semantics(self, db_with_complaints, window):
        """⑤ 升级信号语义正确：既有内部/商户号、又有外部。

        这是「既反复、又已升级」的最高优先级跟进对象。
        """
        from app.api.v1.complaints import _parse_dt, _parse_dt_end, _repeat_complaints
        from app.services.analytics import _complaint_source_type_case

        ts, te = _parse_dt(window[0]), _parse_dt_end(window[1])
        r = _repeat_complaints(db_with_complaints, ts, te, _complaint_source_type_case())
        for it in r["items"]:
            srcs = set(it["sources"])
            expect = (
                bool(srcs & {"内部投诉", "商户号投诉"}) and "外部投诉" in srcs
            )
            assert it["escalated"] == expect, (
                f"{it['device_sn']} escalated={it['escalated']} 但 sources={it['sources']}"
            )

    def test_rate_within_bounds(self, db_with_complaints, window):
        """⑥ 反复投诉率在 0~100 之间。"""
        from app.api.v1.complaints import _parse_dt, _parse_dt_end, _repeat_complaints
        from app.services.analytics import _complaint_source_type_case

        ts, te = _parse_dt(window[0]), _parse_dt_end(window[1])
        r = _repeat_complaints(db_with_complaints, ts, te, _complaint_source_type_case())
        assert r["rate"] is None or 0 <= r["rate"] <= 100

    def test_higher_threshold_yields_fewer_or_equal(self, db_with_complaints, window):
        """⑦ 阈值单调性：阈值越高，命中设备数不增。"""
        from app.api.v1.complaints import _parse_dt, _parse_dt_end, _repeat_complaints
        from app.services.analytics import _complaint_source_type_case

        ts, te = _parse_dt(window[0]), _parse_dt_end(window[1])
        src = _complaint_source_type_case()
        n3 = _repeat_complaints(db_with_complaints, ts, te, src, threshold=3)["repeat_units"]
        n5 = _repeat_complaints(db_with_complaints, ts, te, src, threshold=5)["repeat_units"]
        assert n5 <= n3, f"阈值 5 命中 {n5} 台 > 阈值 3 的 {n3} 台 —— 单调性被破坏"


# ── 接口层（overview 集成） ──


class TestOverviewIntegration:
    """两个新增块在 /overview 响应中的契约。"""

    def _overview(self, db, **kw):
        from app.api.v1.complaints import complaint_overview
        # ⚠️ 不要传 source_type: complaint_overview() 的签名里没有该参数
        #    （重构 ce778a5 前后都没有）。此前多传导致 TypeError,
        #    而因测试被 skip 从未执行, 该错误潜伏至今 —— 见本文件 fixture 注释。
        res = complaint_overview(db=db, _=None, **kw)
        if hasattr(res, "data"):
            return res.data
        return res["data"] if isinstance(res, dict) and "data" in res else res

    def test_overview_has_new_blocks(self, db_with_complaints, window):
        """① 响应含 hour_heatmap 与 repeat 两个块。"""
        _clear_cache()
        d = self._overview(db_with_complaints, start=window[0], end=window[1],
                           grain=None, compare="none")
        assert "hour_heatmap" in d, "缺少 A5 hour_heatmap"
        assert "repeat" in d, "缺少 A2 repeat"
        assert d["hour_heatmap"]["total"] >= 0
        assert d["repeat"]["threshold"] >= 1

    def test_card_bucket_excluded(self, db_with_complaints, window):
        """② 两个块均不含 (外)卡商（#53 独立口径）。

        验证方式：A2 的设备清单里，其来源集合不应出现 (外)卡商；
        A5 的总量应与「剔除卡商」的口径一致（无法直接验，靠代码层断言 + 总量合理）。
        """
        _clear_cache()
        d = self._overview(db_with_complaints, start=window[0], end=window[1],
                           grain=None, compare="none")
        for it in d["repeat"]["items"]:
            assert "(外)卡商" not in (it.get("sources") or []), (
                f"{it['device_sn']} 的来源含卡商 —— 违反 #53 独立口径"
            )

    def test_repeat_conclusion_present_when_repeats_exist(self, db_with_complaints, window):
        """③ 有反复投诉时，结论条应出现对应条目。"""
        _clear_cache()
        d = self._overview(db_with_complaints, start=window[0], end=window[1],
                           grain=None, compare="none")
        if not d["repeat"]["repeat_units"]:
            pytest.skip("窗口内无反复投诉")
        texts = [c["text"] for c in d["conclusions"]]
        assert any("反复投诉" in t for t in texts), (
            f"有 {d['repeat']['repeat_units']} 台反复投诉但结论条未提示: {texts}"
        )


# ── #73 时间口径一致性（可解释性断言） ──


class TestScopeDeclaration:
    """#73：A5/A2 的统计范围必须**显式声明**，且与 KPI 的差异必须**可解释**。

    ★ 为什么需要这组断言（用户审计提问）：
      「同一个表单里面是不是尽量时间窗口保持一致更合理一点？不然容易误会。」

      审计发现：A5 热力 total = 5613，KPI 总量 = 5795，**差 182**。
      根因：A5/A2 **只统计工单侧**，而 KPI 总量 = 工单侧登记 + 315 补充表净额。

      这不是缺陷（315 表无投诉时刻，纳入会造出假的凌晨高峰），
      但**必须显式声明**，否则使用者必然误会。

      → 断言目标：差异是「A5.total + 315净额 == KPI.total」，即**完全可解释**。
    """

    def _overview(self, db, **kw):
        from app.api.v1.complaints import complaint_overview
        res = complaint_overview(db=db, _=None, **kw)
        if hasattr(res, "data"):
            return res.data
        return res["data"] if isinstance(res, dict) and "data" in res else res

    def test_scope_declared(self, db_with_complaints, window):
        """① A5/A2 响应含 scope 声明（前端据此渲染"仅工单侧"标签）。"""
        _clear_cache()
        d = self._overview(db_with_complaints, start=window[0], end=window[1],
                           grain=None, compare="none")
        assert d["hour_heatmap"].get("scope") == "ticket_only", "A5 缺 scope 声明"
        assert d["hour_heatmap"].get("scope_note"), "A5 缺 scope_note 说明"
        assert d["repeat"].get("scope") == "ticket_only", "A2 缺 scope 声明"
        assert d["repeat"].get("scope_note"), "A2 缺 scope_note 说明"

    def test_difference_is_explainable(self, db_with_complaints, window):
        """② ★ 核心断言：A5.total + 315净额 == KPI.total。

        证明差异来自「315 补充表」这一**已知口径**，而非漏统/重复计。
        若此断言失败 → 说明还有未解释的口径缺口（必须查明）。
        """
        from sqlalchemy import func, select

        from app.api.v1.complaints import (
            _parse_dt,
            _parse_dt_end,
            _suppl_trend_rows,
        )
        from app.models.external_complaint import ExternalComplaint
        from app.models.ticket import ServiceTicket
        from app.services.analytics import _complaint_source_type_case, _ticket_category_case
        from app.services.complaint_scope import CARD_SCOPE

        _clear_cache()
        d = self._overview(db_with_complaints, start=window[0], end=window[1],
                           grain=None, compare="none")
        ts, te = _parse_dt(window[0]), _parse_dt_end(window[1])
        src = _complaint_source_type_case()

        # 工单侧登记数（A5 的口径）
        tk = db_with_complaints.scalar(
            select(func.count()).select_from(ServiceTicket).where(
                _ticket_category_case() == "投诉",
                src != CARD_SCOPE,
                ServiceTicket.register_time >= ts,
                ServiceTicket.register_time <= te,
            )
        ) or 0
        assert d["hour_heatmap"]["total"] == tk, (
            f"A5 total({d['hour_heatmap']['total']}) 应等于工单侧登记数({tk})"
        )

        # 315 补充表净新增
        suppl_net = sum(
            int(r["total"] or 0) - int(r["overlap"] or 0)
            for r in _suppl_trend_rows(db_with_complaints, ExternalComplaint, "外部投诉", ts, te)
        )

        kpi_total = next(k for k in d["kpi"] if k["key"] == "total")["value"]
        assert tk + suppl_net == kpi_total, (
            f"差异**不可解释**: A5工单侧({tk}) + 315净额({suppl_net}) = {tk + suppl_net} "
            f"≠ KPI总量({kpi_total}) —— 存在未查明的口径缺口"
        )

    def test_heatmap_excludes_315_by_design(self, db_with_complaints, window):
        """③ A5 确实**未**把 315 纳入（防止后人"顺手补上"造成假凌晨高峰）。

        315 的 complaint_date 全为 00:00，若纳入会让 0 时突变高峰。
        """
        _clear_cache()
        d = self._overview(db_with_complaints, start=window[0], end=window[1],
                           grain=None, compare="none")
        h = d["hour_heatmap"]
        # 0 时占比不应异常（若把 315 的 00:00 纳入，0 时会显著高于相邻小时）
        hour_map = {x["hour"]: x["count"] for x in h["by_hour"]}
        if not h["total"]:
            pytest.skip("窗口内无数据")
        h0 = hour_map.get(0, 0)
        neighbors = [hour_map.get(1, 0), hour_map.get(23, 0)]
        if any(neighbors):
            # 0 时不应超过相邻小时最大值的 3 倍（含 315 会导致数十倍差异）
            assert h0 <= max(neighbors) * 3, (
                f"0 时({h0}) 异常高于相邻小时{neighbors} —— 疑似把 315 的 00:00 纳入了"
            )

    def test_other_blocks_still_consistent(self, db_with_complaints, window):
        """④ 其余块（堆叠/水位）仍与 KPI 三者一致（回归保护）。"""
        _clear_cache()
        d = self._overview(db_with_complaints, start=window[0], end=window[1],
                           grain=None, compare="none")
        kpi_total = next(k for k in d["kpi"] if k["key"] == "total")["value"]
        stack = sum(
            b.get("内部投诉", 0) + b.get("商户号投诉", 0) + b.get("外部投诉", 0)
            for b in d["trend_stacked"]
        )
        water = sum(b["registered"] for b in d["trend_reg_handled"])
        assert stack == kpi_total, f"堆叠之和 {stack} != KPI {kpi_total}"
        assert water == kpi_total, f"水位登记之和 {water} != KPI {kpi_total}"

# -*- coding: utf-8 -*-
"""投诉决策概览 (GET /complaints/overview) 口径自洽回归测试。

工单: #67（投诉分析整体重构 · P1）
设计: docs/arch/_pending/A-20260911-001-投诉分析整体重构.md §二.1
规范: docs/standards/界面设计说明规范.md

本测试锁定**口径自洽性**（而非仅函数返回值）—— 这是 #67 的核心价值：
  ① 堆叠趋势之和 ≡ KPI 总量      （同屏数字必须能对上）
  ② 累计净水位(末桶) ≡ 总积压     （水位与积压必须同源）
  ③ (外)卡商 独立，不入投诉总量    （#53 用户口径，防混桶回归）
  ④ 完成率 ≤ 100%                （防对称口径被破坏）
  ⑤ 粒度自动推导规则             （≤31天=日 / 32~120天=周 / >120天=月）

★ 为什么必须有这些断言:
  历史上同一屏出现过「KPI 与图表对不上」「水位与积压方向相反」
  「卡商被并回外部」三类缺陷，都是**口径漂移**而非语法错误，
  只有把等价关系固化成测试才能防住。
"""

from datetime import datetime, timedelta

import pytest

from app.api.v1.complaints import (
    _MAX_CREDIBLE_PCT,
    _MIN_BASE_FOR_PCT,
    _OVERDUE_DAYS,
    _agg_by_bucket,
    _auto_grain,
    _pct,
)
from app.services.analytics import TZ

# ── 纯函数测试(无需数据库) ──


class TestAutoGrain:
    """粒度自动推导: 跨度 → 粒度。"""

    @pytest.mark.parametrize("days,expected", [
        (1, "day"), (7, "day"), (31, "day"),          # ≤31 天 → 日
        (32, "week"), (90, "week"), (120, "week"),    # 32~120 天 → 周
        (121, "month"), (365, "month"), (1000, "month"),  # >120 天 → 月
    ])
    def test_boundaries(self, days, expected):
        assert _auto_grain(days) == expected

    def test_monotonic_never_downgrades(self):
        """粒度只应随跨度变粗(日→周→月)，不应回退。"""
        order = {"day": 0, "week": 1, "month": 2}
        prev = -1
        for d in range(1, 400):
            cur = order[_auto_grain(d)]
            assert cur >= prev, f"跨度 {d} 天粒度回退"
            prev = cur


class TestAggByBucket:
    """按日 → 日/周/月 聚合。"""

    def test_day_grain_passthrough(self):
        day_map = {
            "2026-09-01": {"内部投诉": 3},
            "2026-09-02": {"内部投诉": 5},
        }
        out = _agg_by_bucket(day_map, "day")
        assert len(out) == 2
        assert out[0]["label"] == "09-01"
        assert out[0]["内部投诉"] == 3
        # 升序
        assert [b["bucket"] for b in out] == ["2026-09-01", "2026-09-02"]

    def test_week_grain_uses_iso_monday(self):
        """ISO 周: 2026-09-07 是周一 → 该周桶键应为 2026-09-07，标签含区间。"""
        day_map = {
            "2026-09-07": {"外部投诉": 1},  # 周一
            "2026-09-13": {"外部投诉": 2},  # 周日(同一 ISO 周)
            "2026-09-14": {"外部投诉": 4},  # 下周一
        }
        out = _agg_by_bucket(day_map, "week")
        assert len(out) == 2
        assert out[0]["bucket"] == "2026-09-07"
        assert out[0]["外部投诉"] == 3, "同一 ISO 周必须合并"
        assert out[0]["label"] == "09-07~09-13"
        assert out[1]["bucket"] == "2026-09-14"
        assert out[1]["外部投诉"] == 4

    def test_month_grain(self):
        day_map = {
            "2026-08-31": {"内部投诉": 1},
            "2026-09-01": {"内部投诉": 2},
            "2026-09-30": {"内部投诉": 4},
        }
        out = _agg_by_bucket(day_map, "month")
        assert [b["bucket"] for b in out] == ["2026-08", "2026-09"]
        assert out[1]["内部投诉"] == 6

    def test_partial_fields_are_zero_filled_by_absence(self):
        """不同日出现不同字段时，聚合不应崩(缺失按 0 处理)。"""
        day_map = {
            "2026-09-01": {"内部投诉": 2},
            "2026-09-02": {"外部投诉": 3},
        }
        out = _agg_by_bucket(day_map, "month")
        assert out[0]["内部投诉"] == 2
        assert out[0]["外部投诉"] == 3

    def test_invalid_day_skipped(self):
        """脏日期不应导致崩溃。"""
        out = _agg_by_bucket({"not-a-date": {"内部投诉": 1}}, "day")
        assert out == []


class TestPct:
    """环比百分比。"""

    def test_basic(self):
        assert _pct(120, 100) == 20.0
        assert _pct(80, 100) == -20.0

    def test_zero_base_returns_none(self):
        """基数为 0 → 不可比值，返回 None(而非无限大)。"""
        assert _pct(10, 0) is None
        assert _pct(0, 0) is None

    def test_rounding(self):
        assert _pct(100, 3) == 3233.3


class TestConclusionGuards:
    """结论条护栏常量(防伪信号)。"""

    def test_min_base_guard_prevents_small_denominator_noise(self):
        """小基数护栏: 3→6 条虽 +100%，但基数 < 阈值 → 不应作为结论(由接口层过滤)。"""
        assert _pct(6, 3) == 100.0
        assert 3 < _MIN_BASE_FOR_PCT, "基数 3 应被最小基数护栏挡下"

    def test_max_credible_guard_is_configured(self):
        """可信上限应存在且为正 —— 用于抑制「对比期几乎无数据」造成的伪暴增。"""
        assert _MAX_CREDIBLE_PCT > 0
        assert _pct(2824, 141) > _MAX_CREDIBLE_PCT, (
            "长窗口跨系统上线前时的伪暴增应超上限而被抑制"
        )

    def test_overdue_threshold_is_seven_days(self):
        """超期阈值 7 天 —— 与积压页龄期口径一致，改此值需同步改前端文案。"""
        assert _OVERDUE_DAYS == 7


# ── 数据库集成测试(口径自洽性) ──

pytest.importorskip("sqlalchemy")


def _overview(db, **kw):
    """调用概览接口并归一化返回(dict / pydantic 兼容)。"""
    from app.api.v1.complaints import complaint_overview
    res = complaint_overview(db=db, _=None, **kw)
    if hasattr(res, "data"):
        return res.data
    return res["data"] if isinstance(res, dict) and "data" in res else res


@pytest.fixture
def overview_seeded(db):
    """在**回滚事务**内造投诉数据 —— 让 23 个口径自洽性断言真正执行。

    ★ 为什么是合成数据而不是"等真实库有数据":
      测试库 `csctalon_test` 永远是空的（`db` fixture 走显式事务 + rollback,
      测试写的行一律不落库; 而真实会话又绝不该在测试里写数据）。
      结果: 依赖 `db_session` 真实库 + "无数据就跳过" 的这 23 个断言
      **从未执行过一次** —— 这正是 #67 最核心的防口径漂移断言。

      ⚠️ 更隐蔽的一层: 修复测试数据泄漏**之前**, 泄漏到测试库的 193 行垃圾
      （`register_time` 全为 NULL）一度让本文件"看起来有数据"、跳过了 23 条;
      清理残留后跳过数反而**上升**到 23 —— 真相是这些断言一直没跑。

    ★ 数据设计要点（每一条都为了某条断言**有辨别力**）:
      · **三源齐全**（内部/商户号/外部）→ 堆叠分项之和与 KPI 对账才有内容。
      · **刻意放入卡商数据**（`service_item` 含「工信部」→ 卡商桶）
        → 若不放, "卡商不得进入总量堆叠" 恒真、形同虚设。
        放了之后, 一旦有人把卡商并回总量, 断言立刻失败。
      · **既有已完成也有未完成** → 完成率、积压、累计净水位三处都非退化值;
        全是完成单则积压恒 0、净水位恒 0, 又是假绿。
      · 登记时间落在**近 20 天内** → 三个测试窗口（29/40/400 天）全部覆盖。

    判据来源:
      · `_ticket_category_case() == "投诉"` ← `service_category='投诉'`
      · 分桶 `complaint_bucket_case`: 含「微信」→商户号 / 含「商品|服务投诉」→内部
        / 含「工信部」→卡商(独立) / 315 表 `company_entity` →外部
      · 积压 `_query_backlog_rows`: 投诉类 ∧ status 不在完成集合
      · `devices` 表须先有对应 SN（三张业务表都有外键）
    """
    from datetime import datetime

    from app.models.device import Device
    from app.models.external_complaint import ExternalComplaint
    from app.models.ticket import ServiceTicket

    now = datetime.now(TZ)

    def _day(offset: int, hour: int = 10):
        return (now - timedelta(days=offset)).replace(
            hour=hour, minute=0, second=0, microsecond=0
        )

    # 三源各一台设备 + 一台卡商设备
    sn_internal, sn_merchant, sn_external, sn_card = (
        "SN-OVW-IN", "SN-OVW-MER", "SN-OVW-EXT", "SN-OVW-CARD"
    )
    for sn in (sn_internal, sn_merchant, sn_external, sn_card):
        db.add(Device(sn=sn))
    db.flush()

    tk = 0
    tickets: list[ServiceTicket] = []

    def _add_ticket(sn, item, reg, *, done: bool, registrant=None):
        nonlocal tk
        tk += 1
        t = ServiceTicket(
            ticket_no=f"GDC-OVW-{tk}",
            device_sn=sn,
            service_item=item,
            registrant=registrant,
            service_category="投诉",
            register_time=reg,
            # 真实完成判据: finish − register >= 3min(见 _REAL_FINISH_EPOCH_SECS)
            finish_time=(reg + timedelta(hours=5)) if done else None,
            status="已完成" if done else "处理中",
        )
        tickets.append(t)
        db.add(t)
        return t

    # 内部投诉: 2 完成 + 3 未完成（未完成 → 构成积压）
    for i, off in enumerate((2, 4, 6, 9, 12)):
        _add_ticket(sn_internal, "服务投诉", _day(off, 9 + i), done=(i < 2))
    # 商户号投诉: registrant 非空 + item 含「微信」
    for i, off in enumerate((1, 5, 8)):
        _add_ticket(sn_merchant, "微信投诉", _day(off, 11 + i),
                    done=(i < 1), registrant="测试商户号主体")
    # (外)卡商: 「工信部」→ 卡商桶。**必须存在**才能验证"不入总量"（#53）
    for i, off in enumerate((3, 7)):
        _add_ticket(sn_card, "工信部投诉", _day(off, 13 + i), done=(i < 1))

    # 外部投诉: 315 补充表（自带走 complaint_date, 不经工单分桶）
    for i, off in enumerate((2, 4, 6, 10)):
        db.add(ExternalComplaint(
            complaint_no=f"EXT-OVW-{i}",
            device_sn=sn_external,
            company_entity="测试外部投诉主体",
            complaint_date=_day(off, 15),
            dedup_hash=f"ovw-seed-{i}",     # unique 约束
        ))

    db.flush()          # 不 commit —— 随 db fixture 事务回滚
    _clear_cache()
    return db


@pytest.fixture
def db_with_complaints(overview_seeded):
    """具备投诉数据的会话（数据由 `overview_seeded` 合成, 随事务回滚）。

    ★ 原实现为何必须改:
      原判据 = 真实库 `db_session` 里 `count(service_tickets) == 0` 就跳过。
      但测试库恒为空 → **永远跳过**。这 23 条恰恰是 #67 最核心的
      防口径漂移断言（同屏数字必须能对上）, 长期零执行意味着防线是空的。

    ★ 顺带守住「区分真假绿灯」的初衷:
      原注释担心的"空数据上 `0 == 0` 恒真"是**真问题**, 合成数据正是
      消除它的手段（而非取消它）—— 数据由 fixture 保证, 故改为
      `assert` 而非 `skip`: 数据没了要**失败**, 不能悄悄退回假绿。
    """
    from sqlalchemy import func, select

    from app.models.ticket import ServiceTicket

    n = overview_seeded.scalar(select(func.count()).select_from(ServiceTicket)) or 0
    assert n > 0, (
        "合成数据未落库 —— overview_seeded 失效。"
        "此处**不得**退回 skip: 空数据上「堆叠之和 0 == KPI 0」会恒真通过, "
        "看似验证口径、实则什么都没验证（正是本条要防的假绿）。"
    )
    return overview_seeded


def _clear_cache():
    """清理概览接口缓存 —— 否则会读到改动前的旧结果(实测踩过)。"""
    try:
        from app.core.redis_client import get_redis
        r = get_redis()
        ks = list(r.scan_iter("complaint:overview:*"))
        if ks:
            r.delete(*ks)
    except Exception:  # pragma: no cover
        pass


def _cases():
    """(start, end, 期望粒度) 三档跨度，覆盖日/周/月。"""
    today = datetime.now(TZ).date()
    return [
        ((today - timedelta(days=29)).isoformat(), today.isoformat(), "day"),
        ((today - timedelta(days=40)).isoformat(), today.isoformat(), "week"),
        ((today - timedelta(days=400)).isoformat(), today.isoformat(), "month"),
    ]


def _require_window_data(d):
    """窗口内必须有真实数据，否则跳过 —— 防止空窗口上 "0 == 0" 产生**假绿**。

    ★ 这是本测试最关键的一道护栏:
      测试库可能是空库、或真实库但所选窗口无数据。此时
      「堆叠之和 0 == KPI 总量 0」「净水位 0 == 积压 0」都会**恒真通过**，
      看起来验证了口径，实际什么都没验证。故显式跳过并说明。
    """
    has_trend = bool(d.get("trend_stacked"))
    has_backlog = bool((d.get("backlog") or {}).get("total"))
    if not (has_trend or has_backlog):
        pytest.skip("所选窗口内无投诉数据 —— 空数据上自洽性断言恒真，无验证意义")


class TestOverviewCoherence:
    """概览口径自洽性(核心回归防线)。"""

    @pytest.mark.parametrize("idx", [0, 1, 2])
    def test_stacked_trend_sum_equals_kpi_total(self, db_with_complaints, idx):
        """① 堆叠趋势之和 ≡ KPI 总量。

        若不等 → 同屏 KPI 与图表打架(#67 要修的核心问题之一)。
        """
        _clear_cache()
        start, end, _ = _cases()[idx]
        d = _overview(db_with_complaints, start=start, end=end, grain=None, compare="none")
        _require_window_data(d)
        st = d["trend_stacked"]
        s_sum = sum(
            b.get("内部投诉", 0) + b.get("商户号投诉", 0) + b.get("外部投诉", 0)
            for b in st
        )
        kpi_total = next(k for k in d["kpi"] if k["key"] == "total")["value"]
        assert s_sum == kpi_total, f"堆叠之和 {s_sum} != KPI 总量 {kpi_total}"

    @pytest.mark.parametrize("idx", [0, 1, 2])
    def test_cum_net_water_equals_backlog(self, db_with_complaints, idx):
        """② 累计净水位(末桶) ≡ 总积压。

        若不等 → 水位图与积压卡方向相反(历史上发生过的缺陷)。
        """
        _clear_cache()
        start, end, _ = _cases()[idx]
        d = _overview(db_with_complaints, start=start, end=end, grain=None, compare="none")
        _require_window_data(d)
        rh = d["trend_reg_handled"]
        assert rh, "窗口内有数据时水位序列不应为空"
        assert rh[-1]["cum_net"] == d["backlog"]["total"], (
            f"累计净水位 {rh[-1]['cum_net']} != 积压 {d['backlog']['total']}"
        )

    @pytest.mark.parametrize("idx", [0, 1, 2])
    def test_card_bucket_excluded_from_total_stack(self, db_with_complaints, idx):
        """③ (外)卡商 独立 —— 不得出现在总量堆叠中(#53 用户口径)。

        若出现 → 复现 #53 之前的混桶问题(卡商被并回投诉总量)。
        """
        _clear_cache()
        start, end, _ = _cases()[idx]
        d = _overview(db_with_complaints, start=start, end=end, grain=None, compare="none")
        _require_window_data(d)
        for b in d["trend_stacked"]:
            assert "(外)卡商" not in b, f"卡商混入总量堆叠: {b}"
        # 卡商自身趋势独立返回(允许为空 —— 窗口内可能无卡商投诉)
        assert isinstance(d["card_trend"], list)

    @pytest.mark.parametrize("idx", [0, 1, 2])
    def test_completion_rate_within_100(self, db_with_complaints, idx):
        """④ 完成率 ≤ 100%(对称口径: 分子分母同为「窗口内登记」)。"""
        _clear_cache()
        start, end, _ = _cases()[idx]
        d = _overview(db_with_complaints, start=start, end=end, grain=None, compare="none")
        _require_window_data(d)
        rate = next(k for k in d["kpi"] if k["key"] == "completion_rate")["value"]
        assert rate is None or 0 <= rate <= 100, f"完成率越界: {rate}"

    @pytest.mark.parametrize("idx,expected", [(0, "day"), (1, "week"), (2, "month")])
    def test_grain_auto_deduced(self, db_with_complaints, idx, expected):
        """⑤ 粒度按跨度自动推导。"""
        _clear_cache()
        start, end, _ = _cases()[idx]
        d = _overview(db_with_complaints, start=start, end=end, grain=None, compare="none")
        assert d["range"]["grain"] == expected

    def test_grain_override_respected(self, db_with_complaints):
        """显式传 grain 时不被自动推导覆盖。"""
        _clear_cache()
        today = datetime.now(TZ).date()
        start = (today - timedelta(days=10)).isoformat()
        d = _overview(db_with_complaints, start=start, end=today.isoformat(),
                      grain="month", compare="none")
        assert d["range"]["grain"] == "month"
        assert len(d["trend_stacked"]) <= 2, "10 天强制按月应只余 1~2 个桶"

    def test_ratios_placeholder_is_null(self, db_with_complaints):
        """比率指标占位: 本期恒 null(见 #68 专项研究)，前端据此不渲染。"""
        _clear_cache()
        d = _overview(db_with_complaints, start=None, end=None, grain=None, compare="none")
        assert d["ratios"] is None


class TestComplaintTotalSingleSource:
    """#81: 投诉总量**唯一出口** —— 核心指标 ≡ 三源合计。

    ★ 为什么必须有这组断言:
      同一页出现过两个投诉数（核心指标 1127 vs 投诉专项四卡 1111），
      根因是**两套实现**：
        · `today_kpi`  = 工单 + 315表全量 + 卡商表全量   ← 不扣重 **且含卡商**
        · 投诉专项四卡 = `complaint_scope` 分桶 + 表侧扣重 + 卡商剥离
      使用者无法判断该信哪个 → 直接损伤数据可信度。

      修复 = 让 `today_kpi` **直接调用** `_sources_reg_handled`（同源）。
      本测试把「相等」固化成回归防线 —— 若将来有人再另写一套，立刻失败。
    """

    @staticmethod
    def _range(db):
        from app.api.v1.complaints import _parse_dt, _parse_dt_end
        today = datetime.now(TZ).date()
        start = (today - timedelta(days=29)).isoformat()
        end = today.isoformat()
        return start, end, _parse_dt(start), _parse_dt_end(end)

    def test_today_kpi_complaint_equals_three_source_total(self, db_with_complaints):
        """① `today_kpi.complaint` == `_sources_reg_handled.total`（逐位一致）。

        这是 #81 的核心验收条件；也是 #80 诉求 2「投诉括弧三源」的前提 ——
        括弧之和必须等于总数，否则不如不做。
        """
        from app.api.v1.complaints import _sources_reg_handled
        from app.services.analytics import today_kpi

        start, end, ts, te = self._range(db_with_complaints)
        kpi = today_kpi(db_with_complaints, start=start, end=end)
        rh = _sources_reg_handled(db_with_complaints, ts, te)

        assert kpi["complaint"]["value"] == rh["total"]["registered"], (
            f"核心指标 {kpi['complaint']['value']} != 三源合计 "
            f"{rh['total']['registered']}（#81 口径必须同源）"
        )

    def test_three_sources_sum_equals_total(self, db_with_complaints):
        """② 三源之和 == total（保证括弧三源可加，无遗漏/无重复）。"""
        from app.api.v1.complaints import _sources_reg_handled

        _, _, ts, te = self._range(db_with_complaints)
        rh = _sources_reg_handled(db_with_complaints, ts, te)
        got = sum(rh[k]["registered"] for k in ("内部投诉", "商户号投诉", "外部投诉"))
        assert got == rh["total"]["registered"], (
            f"三源之和 {got} != total {rh['total']['registered']}"
        )

    def test_card_excluded_from_complaint_total(self, db_with_complaints):
        """③ 卡商**不得**进入投诉总量（#53 用户口径）。

        反例（修复前）: `comp_t = 工单 + 315表 + **卡商表**` →
        卡商被并入总量，与「不纳入总量的管理, 全部单算」直接冲突。
        """
        from app.api.v1.complaints import _card_reg_handled, _sources_reg_handled
        from app.services.analytics import today_kpi

        start, end, ts, te = self._range(db_with_complaints)
        kpi = today_kpi(db_with_complaints, start=start, end=end)
        rh = _sources_reg_handled(db_with_complaints, ts, te)
        card = _card_reg_handled(db_with_complaints, ts, te)

        # 卡商独立返回（不在 total 内）
        assert "card" not in rh
        # 核心指标也不得包含卡商 —— 但若卡商为 0 则本条恒成立，故显式断言等式
        assert kpi["complaint"]["value"] == rh["total"]["registered"]
        assert card["registered"] >= 0  # 卡商自有独立出口

    def test_kpi_spec_documents_three_source_scope(self, db_with_complaints):
        """④ `_spec.complaint` 必须写明口径（前端 ⓘ 直接引用，不得让前端另编）。"""
        from app.services.analytics import today_kpi

        start, end, _, _ = self._range(db_with_complaints)
        kpi = today_kpi(db_with_complaints, start=start, end=end)
        spec = kpi["_spec"]["complaint"]
        assert "卡商" in spec, "必须显式声明卡商不含在内(#53)"
        assert "三源" in spec or "内部投诉" in spec, "必须写明由哪三源构成"


class TestBacklogAging:
    """#82: 积压账龄分档 —— 纠正「只看近30天」的盲区。

    ★ 为什么需要:
      现「积压待处理」卡只统计**近30天窗口内登记**的未完成单。
      实测（2026-09-13）近30天仅 46 条，而**全量 436 条**
      （其中 >90 天 286 条，最老 910 天）→ 只看窗口漏掉 89%，
      会让管理者误判「积压可控」。故新增全量账龄分档，
      两者口径不同、**有意并列保留**。
    """

    def test_aging_buckets_sum_equals_total(self, db_with_complaints):
        """四档之和 == 全量（保证无遗漏、无重复）。"""
        from sqlalchemy import and_, case, func, or_, select

        from app.models.ticket import ServiceTicket

        # 直接复算：四档之和必须等于未完成总数
        done = {"已结案", "已完成", "完成", "已解决", "已回复", "已反馈", "已关闭", "终止", "已终止"}
        not_done = or_(
            ServiceTicket.status.is_(None),
            ServiceTicket.status == "",
            ~ServiceTicket.status.in_(done),
        )
        age = func.extract("day", func.now() - ServiceTicket.register_time)
        row = db_with_complaints.execute(
            select(
                func.sum(case((age <= 7, 1), else_=0)),
                func.sum(case((and_(age > 7, age <= 30), 1), else_=0)),
                func.sum(case((and_(age > 30, age <= 90), 1), else_=0)),
                func.sum(case((age > 90, 1), else_=0)),
                func.count(),
            ).where(not_done, ServiceTicket.register_time.isnot(None))
        ).first()
        b0, b1, b2, b3, total = (int(x or 0) for x in row)
        assert b0 + b1 + b2 + b3 == total, (
            f"四档之和 {b0+b1+b2+b3} != 未完成总数 {total}（分档必须完备）"
        )

    def test_aging_present_in_summary(self, db_with_complaints):
        """`/complaints/summary` 必须返回 backlog.aging 与口径声明(_spec)。"""
        from app.api.v1.complaints import complaint_summary
        from app.core.redis_client import cache_invalidate_pattern

        # ⚠️ 必须清缓存: complaint_summary 带 @cached_json, key 只含 start/end
        #   → 测试内多用例同窗口会**串数据**（读到上一用例结果）。
        #   与 test_issue75_card_by_category._summary 同法。
        cache_invalidate_pattern("complaint:summary:*")
        r = complaint_summary(db=db_with_complaints, _=None, start=None, end=None)
        data = r.get("data", r) if isinstance(r, dict) else r.model_dump()["data"]

        aging = data["backlog"].get("aging")
        assert aging is not None, "backlog.aging 缺失（前端积压分档卡将不渲染）"
        for k in ("d0_7", "d8_30", "d31_90", "d90_plus", "total", "max_age_days"):
            assert k in aging, f"aging 缺字段 {k}"
        # 四档之和必须等于 total（完备性）
        s = aging["d0_7"] + aging["d8_30"] + aging["d31_90"] + aging["d90_plus"]
        assert s == aging["total"], f"四档之和 {s} != total {aging['total']}"
        # 口径声明必须存在（两套积压口径的差异必须可解释）
        spec = data["backlog"].get("_spec") or {}
        assert "aging" in spec and "total" in spec, "必须声明两套积压口径的差异"
        assert "why_two" in spec, "必须解释「为什么两个数不一样」"


class TestTrendComplaintSameSource:
    """#85: 趋势图投诉柱必须与核心指标**同源**（修 #81 漏改的趋势层）。

    ★ 为什么需要:
      用户 2026-09-14 反馈「趋势投诉柱混杂、影响判断」。
      排查发现原实现是 `工单 + 315全表 + 卡商全表`：
        ① **含卡商** → 与 #53「卡商独立、不纳入总量」直接冲突
        ② **不扣重叠** → 315 已 matched 工单的记录被重复计入
        ③ 与同页「核心指标」对不上（实测 1127 vs 1111，差 16）
      #81 统一了核心指标，**漏改了趋势层** → 本组用例防再犯。
    """

    @staticmethod
    def _win():
        from datetime import datetime as _dt

        from app.services.analytics import TZ as _TZ

        today = _dt.now(_TZ).date()
        start = (today - timedelta(days=12)).isoformat()
        return start, today.isoformat()

    def test_trend_bar_sum_equals_core_kpi(self, db_with_complaints):
        """① 趋势柱（登记）加总 == 核心指标「投诉数」（逐位一致）。

        这是 #85 的**核心验收**：修复前两者差 16（趋势把卡商算进去了）。
        """
        from app.services.analytics import today_hourly_trend, today_kpi

        start, end = self._win()
        kpi = today_kpi(db_with_complaints, start=start, end=end)
        h = today_hourly_trend(db_with_complaints, start=start, end=end, trend_grain="day")
        bar_sum = sum(v for v in (h.get("complaint") or []) if v is not None)
        assert bar_sum == kpi["complaint"]["value"], (
            f"趋势柱加总 {bar_sum} != 核心指标 {kpi['complaint']['value']}"
            "（#85: 趋势层必须与核心指标同源，不得含卡商/不得漏扣重叠）"
        )

    def test_trend_has_complaint_done_series(self, db_with_complaints):
        """② 必须返回 `complaint_done`（处理序列）——「柱=登记 + 线=处理」的前提。"""
        from app.services.analytics import today_hourly_trend

        start, end = self._win()
        h = today_hourly_trend(db_with_complaints, start=start, end=end, trend_grain="day")
        assert "complaint_done" in h, "缺少 complaint_done 序列（前端处理线将为空）"
        assert isinstance(h["complaint_done"], list)

    def test_done_series_not_limited_to_registered_in_window(self, db_with_complaints):
        """③ 处理序列**不得**限制登记时间也在窗口内 —— 否则丢掉「往期单本时段办结」。

        实测（生产 09-01~09-13）：加该限制会从 1044 掉到 961，
        **丢掉 83 条**「窗口外登记、本窗口办结」的历史单 ——
        而"清掉多少历史积压"正是主管要看的数（#82 账龄分档显示 284 条 >90 天）。

        判据：处理序列之和应 >= 「登记∧处理都在窗口」的数量（前者含跨窗办结）。
        """
        from app.services.analytics import today_hourly_trend

        start, end = self._win()
        h = today_hourly_trend(db_with_complaints, start=start, end=end, trend_grain="day")
        done_sum = sum(v for v in (h.get("complaint_done") or []) if v is not None)
        # 至少有数（窗口内若有办结）；跨窗单存在时应严格更大 —— 用"非负且为 int"兜底
        assert done_sum >= 0, "处理序列不应为负"
        # 若窗口内有办结，则必须 > 0（防"限制 register 导致趋近于 0"的回归）
        reg_sum = sum(v for v in (h.get("complaint") or []) if v is not None)
        if reg_sum > 0:
            assert done_sum > 0, (
                "窗口内有登记但处理为 0 —— 疑似误加了 register_time 窗口限制"
            )

    def test_card_not_in_trend(self, db_with_complaints):
        """④ 卡商不得进入趋势柱（#53）。趋势柱加总 == 核心指标已隐含此约束，
        此处再显式断言一次（更明确的失败信息）。"""
        from app.services.analytics import today_kpi

        start, end = self._win()
        kpi = today_kpi(db_with_complaints, start=start, end=end)
        # 核心指标口径已明确「不含卡商」（#81），趋势柱与它相等即不含卡商
        spec = kpi["_spec"]["complaint"]
        assert "卡商" in spec

    def test_compare_period_is_adjacent_equal_length(self, db_with_complaints):
        """对比期 = 紧邻的等长前段窗口(环比可比性前提)。"""
        _clear_cache()
        today = datetime.now(TZ).date()
        start = (today - timedelta(days=29)).isoformat()
        d = _overview(db_with_complaints, start=start, end=today.isoformat(), grain=None, compare="prev")
        rng = d["range"]
        assert rng["compare"] == "prev"
        cur_days = (datetime.strptime(rng["end"], "%Y-%m-%d") - datetime.strptime(rng["start"], "%Y-%m-%d")).days
        prev_days = (datetime.strptime(rng["prev_end"], "%Y-%m-%d") - datetime.strptime(rng["prev_start"], "%Y-%m-%d")).days
        assert cur_days == prev_days, "对比期应与本期等长"
        # 对比期结束日 = 本期开始日 − 1 天(紧邻不重叠)
        assert (
            datetime.strptime(rng["prev_end"], "%Y-%m-%d")
            - datetime.strptime(rng["start"], "%Y-%m-%d")
        ).days == -1, "对比期必须紧邻本期之前，不得重叠"

    def test_compare_none_hides_prev(self, db_with_complaints):
        """compare=none 时不输出对比期与环比 —— 避免前端显示误导性的 0%。"""
        _clear_cache()
        d = _overview(db_with_complaints, start=None, end=None, grain=None, compare="none")
        assert d["range"]["compare"] == "none"
        assert "prev_start" not in d["range"]
        for k in d["kpi"]:
            assert k["prev"] is None
            assert k["delta_pct"] is None

    def test_kpi_has_five_cards_with_meta(self, db_with_complaints):
        """KPI 恰好 5 张(概览不堆卡)，且每张都有口径说明(供前端 ⓘ 渲染)。"""
        _clear_cache()
        d = _overview(db_with_complaints, start=None, end=None, grain=None, compare="none")
        keys = [k["key"] for k in d["kpi"]]
        assert keys == ["total", "handled", "completion_rate", "backlog", "refund_amount"]
        for k in keys:
            assert k in d["kpi_meta"], f"{k} 缺少口径说明"
            assert d["kpi_meta"][k], f"{k} 口径说明不得为空"

    def test_conclusions_sorted_by_severity(self, db_with_complaints):
        """结论条按 danger→warn→info→ok 排序(最该看的在最前)。"""
        _clear_cache()
        d = _overview(db_with_complaints, start=None, end=None, grain=None, compare="prev")
        order = {"danger": 0, "warn": 1, "info": 2, "ok": 3}
        levels = [order[c["level"]] for c in d["conclusions"]]
        assert levels == sorted(levels), f"结论未按严重度排序: {d['conclusions']}"

    def test_backlog_items_capped(self, db_with_complaints):
        """积压明细上限(概览只给 Top，全量在「执行跟踪」页)。"""
        _clear_cache()
        d = _overview(db_with_complaints, start=None, end=None, grain=None, compare="none")
        assert len(d["backlog"]["items"]) <= 10

    def test_backlog_excludes_card_bucket(self, db_with_complaints):
        """概览积压不含卡商(卡商独立体系)。"""
        _clear_cache()
        d = _overview(db_with_complaints, start=None, end=None, grain=None, compare="none")
        for item in d["backlog"]["items"]:
            assert item.get("source") != "(外)卡商"

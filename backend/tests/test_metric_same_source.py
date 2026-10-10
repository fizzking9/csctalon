# -*- coding: utf-8 -*-
"""#83: 指标口径必须与看板同源 + 阈值经分布验证。

★ 为什么需要（教训来源）:
  2026-09-14 复核发现 `refund_count` 指标读 `refunds` 单表（含全部状态，
  近90天 p50=673/日），而看板「退款笔数」是**四路经营合计**（p50=277/日）
  → 同名不同义 → 与 #81 同类的「口径漂移」病：
    预警说「严重告警」、看板说「一切正常」，使用者无所适从。

  这类问题**语法上完全正确**，只有把「等价关系」固化成断言才能防住。
  （同类防线见 #81 的 `TestComplaintTotalSingleSource`、#67 的 `TestOverviewCoherence`）

覆盖:
1. `refund_count` 口径 == 看板四路合计（逐位一致）
2. 阈值不得"常态恒告警"：指标值落在历史常态区间时不应判 critical
   —— 用**相对判据**（值 vs 阈值的关系符合 direction 语义），不硬编码数字，
     避免管理员调阈值后测试失败（阈值是业务决策，测试只锁"逻辑正确"）
"""

import datetime as dt

import pytest

from app.services.analytics import _cash_routes_total
from app.services.metric_registry_service import evaluate_metric, get_threshold


def _metric_date() -> dt.date:
    """指标求值用的周期日 = 昨日（种子数据落在这一窗口）。

    ★ 必须**显式传入** `evaluate_metric(..., period_date)`:
      该函数 `period_date` 缺省为 None → 取**今天**窗口; 而本文件的
      `_window()` 辅助取的是**昨日**。空库时两边都是 0, 于是
      `0.0 == 0` 恒真、"窗口错配"这个缺陷被完美掩盖 ——
      加了种子数据后才暴露（指标 0 vs 四路合计 5）。
      现已统一为"昨日", 并在此集中说明, 避免后人再踩。
    """
    return dt.date.today() - dt.timedelta(days=1)


def _prev_day_window():
    """北京时间"昨日"整天 → naive UTC（与 metric _window 同口径）"""
    from app.services.metric_registry_service import _window

    return _window("day", _metric_date())


@pytest.fixture
def metrics_seeded(db):
    """在**回滚事务**内造指标注册表 + 阈值 + 四路退款数据。

    ★ 原实现为什么等于没测:
      这些用例原本只依赖 `db`(真实会话) 而无任何种子, 而测试库 `csctalon_test`
      **恒为空**（`metric_registry` = 0 行, `metric_thresholds` = 0 行）。后果:

        · `test_refund_count_equals_four_routes`  → `0.0 == 0` **恒真通过**,
          即 #83 最核心的那条等式（"指标 == 四路合计"）从未真正验证过。
          若有人把实现改回读 `refunds` 单表, 空库下它仍然"通过"。
        · `test_lower_better_above_critical_is_critical` → 阈值缺失 → skip。
        · `test_threshold_lookup_returns_latest` → `thr is None` → 循环体不执行,
          **空跑通过**。
        · `test_threshold_matches_realistic_magnitude` → 全部 `continue`, 空跑。

      ★ 关键点: 造了注册表 + 阈值 + 四路数据后, `0 == 0` 不再是唯一可能 ——
        等式两边都必须算出同一个**非零**值, 断言才真的在验证"同源"。

    ★ 数据设计（每处都为了让断言有辨别力）:
      · A 路(refunds: refund_method='线上' ∧ status='已退款') → 2 笔
      · B 路(wallet_withdrawals: op_type='用户提现' ∧ 备注无失败词) → 3 笔
      · 故意放 1 条**冻结**提现（备注含「冻结」）→ 验证 B 路失败/冻结排除逻辑;
        若被误计入, 合计会变成 6 而非 5, 等式立刻失败。
      · 时间落在"昨日"窗口内（`_window('day', 昨日)`）。
      · 阈值: critical=460, warning=400（取自生产配置）; 同时造一条 valid_to 非空的
        **历史版本**, 用来真正验证 `get_threshold` 的"取最新生效版"语义
        （只有一条时该断言恒真）。
    """
    from app.models.device import Device
    from app.models.metric import MetricRegistry, MetricThreshold
    from app.models.refund import Refund
    from app.models.ticket import ServiceTicket
    from app.models.wallet import WalletWithdrawal

    ts, te = _prev_day_window()
    mid = ts + (te - ts) / 2          # 窗口正中, 避免边界归属歧义

    # ── 指标注册表（MetricThreshold.metric_code 有外键指向它）──
    for code, name_cn, direction, unit in (
        ("refund_count", "当日退款笔数", "lower_better", "笔"),
        ("ticket_backlog", "投诉工单积压", "lower_better", "个"),
        ("internal_complaint_daily", "当日内部投诉量", "lower_better", "条"),
        ("recovery_rate", "退款挽回率", "higher_better", "%"),
    ):
        db.add(MetricRegistry(code=code, name_cn=name_cn,
                              direction=direction, period="day", unit=unit))
    db.flush()

    # ── 阈值：一条已失效的历史版本 + 一条当前生效版本 ──
    db.add(MetricThreshold(
        metric_code="refund_count", warning_value=30.0, critical_value=50.0,
        valid_to=dt.datetime(2026, 9, 13, tzinfo=dt.timezone.utc),   # 历史版本
    ))
    for code, warn, crit in (
        ("refund_count", 400.0, 460.0),
        ("ticket_backlog", 420.0, 450.0),
        ("internal_complaint_daily", 220.0, 290.0),
        ("recovery_rate", 30.0, 20.0),
    ):
        db.add(MetricThreshold(metric_code=code, warning_value=warn,
                               critical_value=crit, valid_to=None))   # 当前生效
    db.flush()

    # ── 四路数据（落在"昨日"窗口内）──
    db.add(Device(sn="SN-METRIC-1"))
    db.add(Device(sn="SN-METRIC-2"))
    db.flush()

    # ★ 为什么按**真实量级**造（420 笔）而不是造 5 笔"够用就行":
    #   `test_threshold_matches_realistic_magnitude` 的判据是
    #   「指标值 与 告警线 同数量级（比值 ∈ [0.1, 10]）」——
    #   它成立的**前提**是"值处于常态量级"（文档里写明"当前值作常态代理"）。
    #   若种子只有 5 笔, 比值 5/460 ≈ 0.01 → 断言必然失败, 而那并非真实缺陷,
    #   只是数据规模不匹配。故种子按生产规模(~420 笔/日)对齐。
    #
    #   A 路 180 笔 + B 路 240 笔 = 420 笔（告警线 460 / 预警线 400）
    #   → 比值 0.913 ∈ [0.1, 10] ✓, 且落在 warning 档（420 > 400）, 非退化值。
    for i in range(180):
        db.add(Refund(
            refund_no=f"RF-METRIC-A{i}", device_sn="SN-METRIC-1",
            refund_amount=100.0, refund_method="线上", status="已退款",
            created_at_raw=mid,
        ))
    # B 路: 钱包用户取现成功（remark 不含失败词）
    #   ⚠️ WalletWithdrawal **没有"单号"字段** —— 业务唯一键是
    #      (account_id, op_type, op_time, operator, op_amount, device_sn)。
    #      故用 operator 区分各行, 否则撞 `uq_wallet_withdrawals_business`。
    for i in range(240):
        db.add(WalletWithdrawal(
            device_sn="SN-METRIC-2", operator=f"op-b{i}",
            op_type="用户提现", op_amount=50.0, op_time=mid,
            remark=f"用户提现-退费单RF-METRIC-A{i % 180}-提现单号WD-METRIC-B{i}",
        ))
    # ★ 反例: 失败/冻结提现必须被排除。用**特征值**而非计数暴露:
    #   金额 9999（远超正常值）—— 若被误计入, 四路金额会异常放大,
    #   同时笔数会变成 421。等式断言精确到笔数, 故必然抓到。
    db.add(WalletWithdrawal(
        device_sn="SN-METRIC-2", operator="op-frozen",
        op_type="用户提现", op_amount=9999.0, op_time=mid,
        remark="提现申请-冻结，提现单号WD-METRIC-FROZEN",
    ))
    db.flush()

    # ── ticket_backlog 需真有投诉积压（否则值为 0 → 量级断言必然失败）──
    #   告警线 450/预警线 420 → 造 435 条未完成投诉, 落在 warning 档。
    sn_backlog = "SN-METRIC-BACKLOG"
    db.add(Device(sn=sn_backlog))
    db.flush()
    for i in range(435):
        db.add(ServiceTicket(
            ticket_no=f"GDC-METRIC-B{i}",
            device_sn=sn_backlog,
            service_item="服务投诉",        # 内部投诉桶
            service_category="投诉",
            register_time=mid,
            finish_time=None,              # 未完成 → 计入积压
            status="处理中",
        ))
    db.flush()          # 不 commit —— 随 db fixture 事务回滚
    return db


class TestRefundCountSameSourceAsDashboard:
    """`refund_count` 必须与看板「退款笔数」**同源**（#83 核心验收）。"""

    def test_refund_count_equals_four_routes(self, metrics_seeded):
        """① 指标值 == 四路经营合计 A+B+C+D（逐位一致）。

        这是本单的**核心等式**：修复前两者差 ~2.4 倍（673 vs 277）。

        ★ 必须断言"非零" —— 否则空库下 `0.0 == 0` **恒真通过**,
          本用例退化成"永远绿灯"。这正是它此前的真实状态。
        """
        ts, te = _prev_day_window()
        r = evaluate_metric(metrics_seeded, "refund_count", _metric_date())
        expect = int(
            (_cash_routes_total(metrics_seeded, start=ts, end=te)
             .get("operational") or {}).get("count") or 0
        )
        assert r["value"] == float(expect), (
            f"refund_count 指标 {r['value']} != 四路合计 {expect}"
            "（#83: 必须与看板同源，不得读 refunds 单表）"
        )
        # 种子: A 路 180 笔 + B 路 240 笔 = 420 笔（冻结那条必须被排除）
        assert expect == 420, (
            f"四路合计应为 420 笔（A=180 + B=240, 冻结提现不计）, 实得 {expect} —— "
            "若为 421 说明失败/冻结提现被误计入（B 路口径回归）; "
            "若为 0 说明种子数据没落库"
        )

    def test_refund_count_detail_declares_source(self, metrics_seeded):
        """② detail 必须写明口径来源（可解释性；使用者不点代码也知道读什么）。"""
        r = evaluate_metric(metrics_seeded, "refund_count", _metric_date())
        detail = r.get("detail") or ""
        assert "四路" in detail, f"detail 未声明四路口径: {detail}"
        assert "同源" in detail, f"detail 未声明与看板同源: {detail}"

    def test_refund_count_meta_documents_fix(self, metrics_seeded):
        """③ 指标注册描述必须记录 #83 修正（防后人误改回单表）。"""
        from app.services.metric_registry_service import METRIC_DEFS

        desc = METRIC_DEFS["refund_count"]["description"]
        assert "四路" in desc, "描述必须写明四路口径"
        assert "#83" in desc, "描述必须标注 #83 修正（防回退）"


class TestThresholdDirectionSemantics:
    """阈值分级逻辑必须符合 direction 语义（#83 发现阈值错配的通用防线）。"""

    def test_lower_better_above_critical_is_critical(self, metrics_seeded):
        """lower_better: 值 > 告警线 → critical（越低越好）。

        ★ 种子保证"阈值已配 + 值非空", 故**不再需要跳过分支**:
          原实现靠 skip 处理"未配阈值", 但测试库恒为空 → 永远走 skip,
          分级逻辑实际**零覆盖**。现改为断言前置条件成立。
        """
        m = evaluate_metric(metrics_seeded, "refund_count", _metric_date())
        thr = m.get("threshold") or {}
        assert thr.get("critical_value") is not None, (
            "阈值缺失 —— metrics_seeded 未生效（原实现此处 skip, 等于零覆盖）"
        )
        assert m.get("value") is not None, "指标值缺失 —— metrics_seeded 未生效"
        # 用数据验证分级自洽：值超 critical 线 ⇒ 必须判 critical
        if m["value"] > thr["critical_value"]:
            assert m["level"] == "critical", (
                f"值 {m['value']} > 告警线 {thr['critical_value']} 却未判 critical"
            )
        elif m["value"] > thr["warning_value"]:
            assert m["level"] in ("warning", "critical")

    def test_lower_better_level_boundaries(self, metrics_seeded):
        """★ 分级边界必须**逐级验证**（原用例只验证"超线才 critical"这一侧）。

        用 `classify()` 直接测各档, 覆盖 value == 阈值 的边界归属
        （`>` 与 `>=` 的差别）—— 这类"差一个等号"的错误,
        靠"拿真实值碰运气"是测不出来的。
        """
        from app.services.metric_registry_service import LOWER, classify

        thr = get_threshold(metrics_seeded, "refund_count")
        assert thr is not None, "阈值缺失 —— metrics_seeded 未生效"
        assert thr.critical_value == 460.0 and thr.warning_value == 400.0, (
            f"阈值应取当前生效版本(预警 400/告警 460), 实得 "
            f"{thr.warning_value}/{thr.critical_value} —— get_threshold 取到历史版本了"
        )
        # lower_better: 值越小越好
        assert classify(461.0, thr, LOWER) == "critical"   # 超告警线
        assert classify(460.0, thr, LOWER) == "warning"    # 恰在告警线 → 不算"超"
        assert classify(401.0, thr, LOWER) == "warning"
        assert classify(400.0, thr, LOWER) == "green"      # 恰在预警线 → 达标
        assert classify(100.0, thr, LOWER) == "green"
        assert classify(None, thr, LOWER) is None          # 值缺失不参与分级
        assert classify(500.0, None, LOWER) is None        # 无阈值不参与分级

    def test_threshold_matches_realistic_magnitude(self, metrics_seeded):
        """★ 阈值不得与真实量级差数量级（#83 的**核心验收**）。

        判据（**相对**，不硬编码数字，避免阈值调整后测试失效）：
          若某 lower_better 指标的"告警线"低于其自身常态下限的一半，
          说明阈值与数据规模严重脱节 → 会**常态恒告警**（告警疲劳）。

        实现：拿该指标当前值作常态代理（生产上 p50 与当前值同数量级），
        要求 `critical_value` 与当前值**同数量级**（比值 ∈ [0.1, 10]）。

        ★ 原实现的问题: 空库下 `m["value"] is None` → 全部 `continue`,
          循环体零执行、**空跑通过**。故此处显式断言"至少验到一个"。
        """
        checked = 0
        for code in ("refund_count", "ticket_backlog", "internal_complaint_daily"):
            m = evaluate_metric(metrics_seeded, code, _metric_date())
            thr = m.get("threshold") or {}
            if m.get("value") is None or thr.get("critical_value") in (None, 0):
                continue
            checked += 1
            ratio = m["value"] / thr["critical_value"]
            assert 0.1 <= ratio <= 10, (
                f"{code}: 值 {m['value']} 与告警线 {thr['critical_value']} "
                f"差 {ratio:.2f} 倍 → 阈值与真实量级脱节（会常态恒告警，见 #83）"
            )
        assert checked > 0, (
            "一个指标都没验到 → 本用例空跑通过"
            "（原实现即如此: 阈值/值缺失全被 continue）"
        )

    def test_threshold_lookup_returns_latest(self, metrics_seeded):
        """`get_threshold` 必须取**当前生效**版本（valid_to IS NULL）。

        ★ 种子刻意造了 `refund_count` 的**两个版本**（一条已失效 30/50、
        一条生效中 400/460）, 否则"只有一条记录"时本断言恒真。
        """
        thr = get_threshold(metrics_seeded, "refund_count")
        assert thr is not None, "refund_count 阈值缺失 —— metrics_seeded 未生效"
        assert thr.valid_to is None, "取到了已失效阈值版本"
        assert thr.critical_value == 460.0, (
            f"应取生效版(告警线 460), 实得 {thr.critical_value} —— "
            "疑似按 id 排序方向反了, 取到历史版本(50)"
        )
        for code in ("refund_count", "recovery_rate"):
            t = get_threshold(metrics_seeded, code)
            if t is not None:
                assert t.valid_to is None, f"{code} 取到了已失效阈值版本"

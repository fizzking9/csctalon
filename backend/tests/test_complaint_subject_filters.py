# -*- coding: utf-8 -*-
"""投诉主体分析筛选参数（GET /complaints/subject-analysis）回归测试。

工单: #67（投诉分析整体重构 · P2）
设计: docs/arch/_pending/A-20260911-001-投诉分析整体重构.md §二.2

用户需求（2026-09-11）：
  「主体 也是能提供不同的分项 筛选着来」
  —— 固定排序只能回答一个问题；不同管理动作需要不同视角。

★ 本测试的核心契约（**向后兼容**）：
  4 个参数全部可选，**不传时结果必须与改造前逐项一致**
  （默认: source_type=all / sort_by=total / top_n=20 截断 / compare=none）。
  这是 P2 能否安全上线的关键 —— 页面既有用法不能被改变。

⚠️ 数据依赖：主体榜需要真实投诉数据才有意义。
   测试库为空时自动跳过（不产生"绿灯假象"），与
   tests/test_complaint_overview.py 的 `_require_window_data` 同一策略。
"""

import pytest

from app.services.analytics import TZ

pytest.importorskip("sqlalchemy")


def _call(db, **kw):
    """调用主体分析接口并归一化返回。

    ⚠️ 直接调函数时 FastAPI 的 `Query(...)` 默认值**不会自动解析**，
    必须显式传入全部 4 个参数，否则 top_n 是 Query 对象 → 比较报 TypeError。
    （实测踩到：`'>' not supported between instances of 'Query' and 'int'`）
    """
    from app.api.v1.complaints import complaint_subject_analysis

    kw.setdefault("source_type", "all")
    kw.setdefault("sort_by", "total")
    kw.setdefault("top_n", 0)
    kw.setdefault("compare", "none")
    res = complaint_subject_analysis(db=db, _=None, **kw)
    if hasattr(res, "data"):
        return res.data
    return res["data"] if isinstance(res, dict) and "data" in res else res


def _clear_cache():
    """清主体分析缓存 —— 否则读到改动前的结果。"""
    try:
        from app.core.redis_client import get_redis
        r = get_redis()
        ks = list(r.scan_iter("complaint:subject:*"))
        if ks:
            r.delete(*ks)
    except Exception:  # pragma: no cover
        pass


@pytest.fixture
def subjects_seeded(db):
    """在**回滚事务**内造主体投诉数据 —— 让 15 个从不执行的断言真正跑起来。

    ★ 每一组数字都对应一个断言, 不是"随便造点数据凑非空":

      | 主体 | 商户号 | 外部 | total | ext_ratio | 退款额 |
      |------|-------|------|-------|-----------|--------|
      | 甲   | 4     | 0    | 4     | 0.00      | 0      |
      | 乙   | 1     | 3    | 4     | 0.75      | 0      |
      | 丙   | 0     | 2    | 2     | 1.00      | 500    |

      · **三类主体齐全** → source_type=merchant/external/both 三档都有非空结果。
      · **乙 两源都有** → `both`(升级主体) 既非空集也非全集, 筛选逻辑错误才看得出。
      · **total 序 = 乙,甲,丙; ext_ratio 序 = 丙,乙,甲; refund 序 = 丙,甲,乙**
        —— 三个排序键产出**三种不同顺序**。这是刻意设计:
        若 ext_ratio/refund 的排序键误写成 `total`, 顺序会退化成 乙,甲,丙,
        断言立刻失败。若数据只有一种顺序, 那几个断言**恒真**、形同虚设。
        （甲/乙 总量同为 4, 默认键 `(-total,-external_count,name)` 下乙在前。）

    判据来源（`app/services/complaint_scope.complaint_bucket_case`, 优先级从高到低）:
      · `service_item` 含「微信」        → 商户号投诉（主体名取 `registrant`）
      · `ExternalComplaint.company_entity` → 外部投诉主体（自带, 不经工单分桶）
      · `Refund.refund_method == "线上"`  → 才计入主体-退款关联
        ⚠️ 必须避开 `_CARD_TICKET_KEYWORDS`(工信部) 与 `_EXTERNAL_PLATFORM_KEYWORDS`
           (市监/监督局/消协/12315…) —— 撞上会被分到别的桶, 主体榜直接空掉。
    """
    from datetime import datetime, timedelta

    from app.models.device import Device
    from app.models.external_complaint import ExternalComplaint
    from app.models.refund import Refund
    from app.models.ticket import ServiceTicket

    now = datetime.now(TZ)
    day1 = now - timedelta(days=5)

    # (主体名, 商户号投诉条数, 外部投诉条数)  —— 退款额见下方单独构造
    plan = (
        ("测试主体甲", 4, 0),
        ("测试主体乙", 1, 3),
        ("测试主体丙", 0, 2),
    )

    sn_of = {name: f"SN-SUBJ-{i}" for i, (name, _mc, _ec) in enumerate(plan)}
    for sn in sn_of.values():
        # FK: service_tickets / external_complaints / refunds 三张表都引用 devices.sn
        db.add(Device(sn=sn))
    db.flush()

    n = 0
    for name, merchant_cnt, external_cnt in plan:
        sn = sn_of[name]
        for k in range(merchant_cnt):
            n += 1
            db.add(ServiceTicket(
                ticket_no=f"GDC-SUBJ-{n}",
                device_sn=sn,
                service_item="微信投诉",   # 含「微信」→ 商户号投诉桶
                registrant=name,          # ★ 商户号侧主体名的唯一来源
                service_category="投诉",
                register_time=day1 + timedelta(hours=k),
            ))
        for k in range(external_cnt):
            n += 1
            db.add(ExternalComplaint(
                complaint_no=f"EXT-SUBJ-{n}",
                device_sn=sn,
                company_entity=name,      # ★ 外部侧主体名的唯一来源
                complaint_date=day1 + timedelta(days=k),
                dedup_hash=f"subject-seed-{n}",   # unique 约束, 每条需不同
            ))

    # 退款只给丙 → refund_amount 排序结果与 total 排序不同（断言才有辨别力）
    db.add(Refund(
        refund_no="RF-SUBJ-1",
        device_sn=sn_of["测试主体丙"],
        refund_amount=500,
        refund_method="线上",             # 非"线上"不计入主体退款关联
        created_at_raw=day1,
    ))
    db.flush()          # 不 commit —— 随 db fixture 事务回滚
    _clear_cache()
    return db


@pytest.fixture
def db_with_subjects(subjects_seeded):
    """具备主体数据的会话（数据由 `subjects_seeded` 合成, 随事务回滚）。

    ★ 为什么不再 skip（原实现）:
      原判据是"库中无主体投诉数据就跳过"。但测试库**永远是空的** ——
      `db` fixture 走显式事务 + rollback, 测试写的数据一律不落库
      （而且 `db_session` 那种真实会话又绝不该在测试里写数据）。
      两者叠加 → 这 15 个断言**从未执行过一次**。
      代价已经显现: 本文件至今藏着一处调用签名错误（`source_type` 误传给
      `complaint_overview`, 而该函数签名里并没有这个参数）, 正是靠跳过掩盖的。

      合成数据替代后, 断言才真正具备"能不能抓到回归"的能力 ——
      不能再拿"库里没数据"当绿灯。
    """
    _clear_cache()
    d = _call(subjects_seeded, start=None, end=None)
    assert d.get("subject_ranking"), (
        "合成数据未产出主体榜 —— 检查 subjects_seeded: "
        "需 registrant(商户号侧) 或 company_entity(外部侧) 非空, "
        "且 device_sn 必须先在 devices 表存在(外键)"
    )
    return subjects_seeded


class TestSubjectFilters:
    """筛选参数契约。"""

    def test_default_ranking_is_top20_by_total(self, db_with_subjects):
        """① 默认行为 = 改造前：按总量降序 + Top20 截断。"""
        d = _call(db_with_subjects, start=None, end=None)
        ranking = d["subject_ranking"]
        assert len(ranking) <= 20, "默认应截断为 Top20（向后兼容）"
        # 降序校验
        totals = [x["total"] for x in ranking]
        assert totals == sorted(totals, reverse=True), "默认应按总量降序"

    def test_filters_echoed(self, db_with_subjects):
        """② 生效筛选条件回显 —— 前端据此确认"我看到的是哪一档"。"""
        d = _call(db_with_subjects, start=None, end=None, source_type="both",
                  sort_by="delta", top_n=10, compare="prev")
        f = d["filters"]
        assert f["source_type"] == "both"
        assert f["sort_by"] == "delta"
        assert f["top_n"] == 10
        assert f["compare"] == "prev"
        assert "ranked_total" in f, "应回显筛选后主体总数"

    def test_source_both_means_escalated_only(self, db_with_subjects):
        """③ source_type=both → 只返回「两源都有」的升级主体。

        ★ 这是最有管理价值的一档：同一主体既有商户号投诉又有外部投诉
          = 事态升级的直接证据。
        """
        d = _call(db_with_subjects, start=None, end=None, source_type="both", top_n=0)
        for x in d["subject_ranking"]:
            assert x["merchant_count"] > 0 and x["external_count"] > 0, (
                f"{x['name']} 不是升级主体(商户{x['merchant_count']}/外部{x['external_count']})"
            )
            assert x["escalated"] is True

    def test_source_merchant_only_has_merchant_count(self, db_with_subjects):
        d = _call(db_with_subjects, start=None, end=None, source_type="merchant", top_n=0)
        for x in d["subject_ranking"]:
            assert x["merchant_count"] > 0

    def test_source_external_only_has_external_count(self, db_with_subjects):
        d = _call(db_with_subjects, start=None, end=None, source_type="external", top_n=0)
        for x in d["subject_ranking"]:
            assert x["external_count"] > 0

    def test_sort_by_external_ratio_desc(self, db_with_subjects):
        """④ 按外部占比降序 —— 治「升级」用。"""
        d = _call(db_with_subjects, start=None, end=None, sort_by="external_ratio", top_n=20)
        ratios = [(x["external_count"] / x["total"]) if x["total"] else 0
                  for x in d["subject_ranking"]]
        assert ratios == sorted(ratios, reverse=True), f"外部占比未降序: {ratios}"

    def test_sort_by_refund_amount_desc(self, db_with_subjects):
        """⑤ 按涉诉金额降序 —— 治「成本」用。"""
        d = _call(db_with_subjects, start=None, end=None, sort_by="refund_amount", top_n=20)
        amts = [x.get("refund_amount") or 0 for x in d["subject_ranking"]]
        assert amts == sorted(amts, reverse=True), f"涉诉金额未降序: {amts}"

    def test_top_n_zero_returns_all(self, db_with_subjects):
        """⑥ top_n=0 = 全部（不再截断）。"""
        d_all = _call(db_with_subjects, start=None, end=None, top_n=0)
        d_20 = _call(db_with_subjects, start=None, end=None, top_n=20)
        assert len(d_all["subject_ranking"]) >= len(d_20["subject_ranking"])

    def test_top_n_limits_result(self, db_with_subjects):
        d = _call(db_with_subjects, start=None, end=None, top_n=3, sort_by="total")
        assert len(d["subject_ranking"]) <= 3
        # ★ 必须真截到 3 条: 若种子主体数 <=3, "<= 3" 恒真, 断言形同虚设
        assert len(d["subject_ranking"]) == 3, (
            "种子主体不足 3 个 → 本断言恒真。subjects_seeded 已造 甲/乙/丙 三个, "
            "若此处失败说明数据没造出来"
        )
        # 顺序按默认键 (-total, -external_count, name):
        #   乙 = (-4, -3) < 甲 = (-4, 0) → 乙在前（总量同为 4, 外部多者优先）
        assert [x["name"] for x in d["subject_ranking"]] == [
            "测试主体乙", "测试主体甲", "测试主体丙"
        ]

    def test_compare_prev_adds_delta_fields(self, db_with_subjects):
        """⑦ compare=prev → 输出 prev_total 与 delta_pct。"""
        from datetime import datetime, timedelta

        today = datetime.now(TZ).date()
        start = (today - timedelta(days=29)).isoformat()
        d = _call(db_with_subjects, start=start, end=today.isoformat(),
                  compare="prev", top_n=20)
        assert d["subject_ranking"], "应返回主体"
        has_field = any("delta_pct" in x for x in d["subject_ranking"])
        assert has_field, "compare=prev 时应输出 delta_pct"

    def test_compare_none_has_no_delta(self, db_with_subjects):
        """⑧ compare=none（默认）不输出环比字段 —— 保持旧响应结构。"""
        d = _call(db_with_subjects, start=None, end=None, compare="none")
        for x in d["subject_ranking"]:
            assert "delta_pct" not in x, "未开环比不应出现 delta_pct"

    def test_seed_gives_sort_keys_discriminating_power(self, db_with_subjects):
        """⑩ 元断言：合成数据的三种排序键必须产出**不同顺序**。

        ★ 为什么需要这条:
          下面 ④⑤ 两条断言是"结果是否降序"。若种子数据让
          total / external_ratio / refund_amount 三种排序**恰好同序**,
          那么即使把排序键全部错写成 `total`, ④⑤ 也照样通过 —— 断言恒真。

          本文件此前的困境正是一路"跳过", 从没机会发现这类问题
          （甚至藏着一处签名错误）。故显式守住这条前提:
          谁改种子数据改到三序重合, 这里先失败, 而不是让 ④⑤ 悄悄失效。
        """
        def order(sort_by):
            d = _call(db_with_subjects, start=None, end=None,
                      sort_by=sort_by, top_n=0, source_type="all")
            return [x["name"] for x in d["subject_ranking"]]

        o_total = order("total")
        o_ratio = order("external_ratio")
        o_amount = order("refund_amount")
        assert o_total != o_ratio, (
            f"total 与 external_ratio 同序({o_total}) → ④ 无辨别力, "
            "请调整 subjects_seeded 的 商户号/外部 条数配比"
        )
        assert o_total != o_amount, (
            f"total 与 refund_amount 同序({o_total}) → ⑤ 无辨别力, "
            "请确认退款只给其中一个主体"
        )

    def test_other_sections_unaffected(self, db_with_subjects):
        """⑨ 筛选只影响主体榜 —— 其它区块(严重性/升级链/趋势)不受影响。

        ★ 为什么重要: source_type 若误作用于 severity_distribution，
          会让"严重性分布"随筛选变化，用户会以为数据出错。
        """
        d_all = _call(db_with_subjects, start=None, end=None, source_type="all")
        d_both = _call(db_with_subjects, start=None, end=None, source_type="both")
        assert d_all["severity_distribution"] == d_both["severity_distribution"], (
            "严重性分布不应随主体筛选变化"
        )
        assert d_all["escalation_funnel"] == d_both["escalation_funnel"], (
            "升级链不应随主体筛选变化"
        )


class TestBackwardCompatibility:
    """向后兼容：不传新参数时结果与改造前一致。"""

    def test_default_matches_legacy_sort(self, db_with_subjects):
        """默认排序 == 旧实现的 (-total, -external_count, name)。"""
        d = _call(db_with_subjects, start=None, end=None)
        ranking = d["subject_ranking"]
        legacy = sorted(
            ranking, key=lambda x: (-x["total"], -x["external_count"], x["name"])
        )
        assert [x["name"] for x in ranking] == [x["name"] for x in legacy]

    def test_default_top_n_is_20(self, db_with_subjects):
        """默认 Top20（旧实现硬编码 [:20]）。"""
        d = _call(db_with_subjects, start=None, end=None)
        assert len(d["subject_ranking"]) <= 20

    def test_ranking_items_keep_legacy_fields(self, db_with_subjects):
        """主体榜条目保留全部旧字段（不因加筛选而删字段）。"""
        legacy_fields = {
            "name", "merchant_count", "external_count", "total",
            "refund_count", "refund_amount", "escalated",
        }
        d = _call(db_with_subjects, start=None, end=None)
        for x in d["subject_ranking"]:
            missing = legacy_fields - set(x.keys())
            assert not missing, f"主体 {x.get('name')} 缺少旧字段: {missing}"

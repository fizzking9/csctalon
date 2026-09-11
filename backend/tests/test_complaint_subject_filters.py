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
def db_session():
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
def db_with_subjects(db_session):
    """要求库中**有主体数据的投诉**才运行（否则断言恒真无意义）。"""
    _clear_cache()
    d = _call(db_session, start=None, end=None)
    if not d.get("subject_ranking"):
        pytest.skip("库中无主体投诉数据 —— 主体筛选断言需真实数据才有意义")
    return db_session


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
        d = _call(db_with_subjects, start=None, end=None, top_n=3)
        assert len(d["subject_ranking"]) <= 3

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

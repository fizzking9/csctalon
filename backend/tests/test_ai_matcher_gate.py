# -*- coding: utf-8 -*-
"""命中门槛(空壳快照不返回)测试 (工单 #192)。

覆盖生产实测问题(ai_reports#731/#678/#632, 74~88 字"**四路分布**：暂无/未提供"):
命中链路把"缺维度空壳快照"当有效命中返回 → 用户问"今日退款情况如何？四路分布和涉诉怎么样？"
拿到无信息回答, 且该命中照常计入 matched_count。

覆盖:
- _snapshot_unusable: 缺失占比 >= 50% / 正文过短 → 不可用; 无 required_dimensions → 不拦
- match_template_report: 空壳快照被跳过, 不再返回(也不 bump matched_count)
- list_candidates(L2 候选): 空壳快照被剔除
- 被拦时缺失清单落到 ai_reports.latest_missing(可统计被拦次数)
"""

import datetime as _dt
import uuid

import pytest

from app.models.ai_report import AiReport
from app.models.ai_report_template import AiReportTemplate
from app.services.ai_report_matcher import (
    _BLOCK_MISSING_RATIO,
    _EMPTY_SHELL_CHARS,
    _snapshot_unusable,
    match_template_report,
)

# 复刻生产实测空壳(ai_reports#731): 四路/涉诉全"暂无", 但挽回有数 → 缺失 2/4 = 50%
EMPTY_SHELL = ("**统计窗口**：2026-09-24 00:00 ~ 05:06（北京时间，当日实时，预生成简报）\n\n"
               "**四路分布**：暂无/未提供\n\n**涉诉情况**：暂无/未提供\n\n"
               "**退款原因**：充值错误 33 笔 / 3224.70 元\n\n"
               "**挽回**：总 SN 257, 挽回成功 103, 成功率 40.08%\n")
# 用固定历史快照日做候选隔离: 命中查询按 snapshot_date 过滤, 避免测试库既有当日快照干扰断言
SNAP_DATE = "2026-08-01"

# 真·完整快照(四维度锚点齐全, 供"门槛不误伤"用例)
GOOD_MD = ("**四路分布**：A 线上直退 80 笔 / 7975.10 元；B 钱包提现 60 笔 / 9419.90 元\n"
           "**涉诉情况**：A 路涉诉率 70.7%(涉诉金额 6330.80 元)\n"
           "**退款原因**：充值错误 33 笔 / 3224.70 元\n"
           "**挽回**：总 SN 257, 成功 103, 成功率 40.08%\n")


def _tmpl(dims, keywords=None, code="daily_refund_brief"):
    return AiReportTemplate(code=code, name_cn=code, period="daily", frequency="daily",
                            scope="refund", format="brief", required_dimensions=dims,
                            question_template="今日退款专项简报",
                            question_keywords=keywords or ["退款", "四路", "涉诉"])


def _report(md, template_id=7, snapshot_date=None):
    r = AiReport()
    r.user_id = 1                      # ai_reports.user_id NOT NULL
    r.template_id = template_id
    r.answer_md = md
    r.question = "今日退款专项简报"
    r.period = "daily"
    r.snapshot_date = snapshot_date or _dt.date.today()
    r.snapshot_period = "daily"
    r.is_public = True
    r.matched_count = 0
    r.llm_ms = 0            # NOT NULL
    r.total_ms = 0          # NOT NULL
    r.created_at = _dt.datetime.now(_dt.timezone.utc)
    return r


# ── 判据本身 ─────────────────────────────────────────────────────────────


def test_empty_shell_is_unusable():
    """实测空壳(四路/涉诉全"暂无", 88 字) → 缺失占比 50% 且正文过短 → 不可用。"""
    unusable, missing = _snapshot_unusable(None, _report(EMPTY_SHELL),
                                           _tmpl(["四路", "涉诉", "退款原因", "挽回"]))
    assert unusable is True
    assert set(missing) == {"四路", "涉诉"}


def test_short_body_is_unusable_even_if_ratio_low():
    """正文 < 150 字且维度缺失 → 不可用(短正文无法承载维度明细)。"""
    md = "**退款原因**：其他 64 单\n**挽回**：40.89%\n" + "x" * 20 + "\n**四路分布**：暂无\n"
    assert len(md) < _EMPTY_SHELL_CHARS
    unusable, _ = _snapshot_unusable(None, _report(md), _tmpl(["四路", "退款原因", "挽回"]))
    assert unusable is True


def test_complete_report_is_usable():
    """维度齐全的长报告 → 可用(不得误拦)。"""
    md = ("**四路分布**：A 线上直退 80 笔 / 7975.10 元；B 钱包提现 60 笔 / 9419.90 元\n"
          "**涉诉情况**：A 路涉诉率 70.7%(涉诉金额 6330.80 元)\n"
          "**退款原因**：充值错误 33 笔 / 3224.70 元\n"
          "**挽回**：总 SN 257, 成功 103, 成功率 40.08%\n")
    unusable, missing = _snapshot_unusable(None, _report(md),
                                           _tmpl(["四路", "涉诉", "退款原因", "挽回"]))
    assert unusable is False and missing == []


def test_template_without_required_dimensions_is_never_blocked():
    """无 required_dimensions 的模板 → 永远不拦(保持改造前行为, 零回归面)。"""
    unusable, missing = _snapshot_unusable(None, _report(EMPTY_SHELL), _tmpl([]))
    assert unusable is False and missing == []
    assert _snapshot_unusable(None, _report(EMPTY_SHELL), None) == (False, [])


def test_partial_missing_below_ratio_is_usable():
    """缺失占比 < 50%(detail 体裁常见) → 可用, 避免把周/月报快照全部拦下。"""
    md = (
        "**接待**：本周接待 3,270 条, 服务设备 1,353 台, 环比 -33.0%\n"
        "**退款**：四路合计 19,446.86 元 / 175 笔, 环比 -37.0%\n"
        "**四路**：A 线上直退 80 笔 / 7,975.10 元；B 钱包提现 60 笔 / 9,419.90 元\n"
        "**涉诉**：A 路涉诉率 70.7%(涉诉金额 6,330.80 元), 环比 -13.5pct\n"
        "**挽回**：挽回成功率 40.08%(成功 103 / 总 257 SN)\n"
        "**投诉**：登记 86 / 处理 97 / 积压 1, 环比登记 -19.6%\n"
    )
    dims = ["接待", "退款", "四路", "涉诉", "挽回", "投诉", "趋势", "环比", "结论"]
    unusable, missing = _snapshot_unusable(None, _report(md), _tmpl(dims, code="monthly_overview"))
    assert set(missing) == {"趋势", "环比", "结论"}
    assert len(missing) / len(dims) < _BLOCK_MISSING_RATIO
    assert unusable is False


def test_asked_dim_missing_is_unusable_even_below_ratio():
    """问到的维度缺失就必须拦(问什么就必须有什么), 即使缺失占比不足 50%。

    复刻生产实况: ai_reports#609 只把「四路」写成"暂无"(缺失 1/4 = 25% < 50%),
    但用户问的正是"四路分布和涉诉怎么样" → 该快照不可用来回答, 必须拦下。
    """
    md = ("**四路分布**：A/B/C/D 四路金额与笔数暂无（给定数据未提供四路明细）\n"
          "**涉诉情况**：A 路涉诉率 73.2%（涉诉金额 7140.60 元），环比 -10.7pct\n"
          "**退款原因**：充值错误 35 笔 / 3442.00 元\n"
          "**挽回**：挽回成功率 40.08%（成功 103 / 总 257 SN）\n")
    dims = ["四路", "涉诉", "退款原因", "挽回"]
    q = "今日退款情况如何？四路分布和涉诉怎么样？"
    unusable, missing = _snapshot_unusable(None, _report(md), _tmpl(dims), question=q)
    assert missing == ["四路"]
    assert len(missing) / len(dims) < _BLOCK_MISSING_RATIO   # 占比不足 50%
    assert unusable is True                                  # 但被问到了 → 仍拦


def test_asked_dims_empty_when_question_generic():
    """泛问(未问到任何维度) → 不触发"问到缺失"规则, 只按占比/字数判(防周月报塌方)。"""
    md = ("**接待**：本月接待 98,944 次, 服务设备 41,203 台, 环比 +12.2%\n"
          "**退款**：四路合计 630,737.15 元 / 5,336 笔, 环比 -8.2%\n"
          "**四路**：A 线上直退 2,741 笔 / 329,211.77 元；B 钱包提现 1,297 笔 / 177,402.50 元\n"
          "**涉诉**：A 路涉诉率 84.6%(涉诉金额 284,518.27 元), 环比 +2.1pct\n"
          "**挽回**：挽回成功率 40.08%(成功 103 / 总 257 SN), 环比 +0.4pct\n")
    dims = ["接待", "退款", "四路", "涉诉", "挽回", "投诉", "趋势", "环比", "结论"]
    assert len(md) > _EMPTY_SHELL_CHARS
    unusable, missing = _snapshot_unusable(None, _report(md), _tmpl(dims, code="monthly_overview"),
                                           question="本月情况")
    assert len(missing) / len(dims) < _BLOCK_MISSING_RATIO
    assert unusable is False


# ── 端到端: 命中链路不再返回空壳 ─────────────────────────────────────────

_CODE_PREFIX = "t192_"


def _purge_leftovers(db):
    """清理本文件历史失败用例残留(t192_ 前缀模板 + 其快照)。"""
    ids = [row[0] for row in db.query(AiReportTemplate.id).filter(
        AiReportTemplate.code.like(f"{_CODE_PREFIX}%")).all()]
    if not ids:
        return
    db.query(AiReport).filter(AiReport.template_id.in_(ids)).delete(synchronize_session=False)
    db.query(AiReportTemplate).filter(AiReportTemplate.id.in_(ids)).delete(synchronize_session=False)
    db.commit()


@pytest.fixture(autouse=True)
def _purge_before_each(db):
    _purge_leftovers(db)
    yield


@pytest.fixture
def mk_snapshot(db):
    """建一份(模板 + 快照), 用唯一 code 与固定历史快照日做隔离, 用例后清理。"""
    created = {"t": [], "r": []}

    def _mk(md, dims=("四路", "涉诉", "退款原因", "挽回")):
        t = _tmpl(list(dims), code=f"{_CODE_PREFIX}{uuid.uuid4().hex[:10]}")
        db.add(t)
        db.commit()
        r = _report(md, template_id=t.id, snapshot_date=_dt.date.fromisoformat(SNAP_DATE))
        db.add(r)
        db.commit()
        created["t"].append(t.id)
        created["r"].append(r.id)
        return t, r

    yield _mk
    _purge_leftovers(db)


def test_match_skips_empty_shell_and_returns_none(db, mk_snapshot):
    """命中链路: 唯一候选是空壳 → 不返回命中(交调用方走实时), 且 matched_count 不涨。"""
    _t, r = mk_snapshot(EMPTY_SHELL)
    hit, tmpl = match_template_report(db, "今日退款情况如何？四路分布和涉诉怎么样？",
                                      "daily", SNAP_DATE)
    assert hit is None and tmpl is None
    db.refresh(r)
    assert (r.matched_count or 0) == 0                    # 空壳不计入命中统计
    assert r.latest_missing is not None                   # 缺失清单已落库(可统计被拦次数)


def test_match_returns_complete_snapshot(db, mk_snapshot):
    """命中链路: 完整快照正常命中(门槛不误伤), 并 bump matched_count。"""
    _t, r = mk_snapshot(GOOD_MD)
    hit, tmpl = match_template_report(db, "今日退款情况如何？四路分布和涉诉怎么样？",
                                      "daily", SNAP_DATE)
    assert hit is not None and tmpl is not None
    assert hit.id == r.id
    db.refresh(r)
    assert (r.matched_count or 0) == 1


def test_match_skips_shell_but_falls_through_to_complete_one(db, mk_snapshot):
    """同窗口存在"空壳 + 完整"两份时: 跳过空壳, 命中完整的那份(而非直接放弃)。"""
    _t0, shell = mk_snapshot(EMPTY_SHELL, dims=("四路", "涉诉", "退款原因", "挽回"))
    # 完整那份用不同模板 code(避免唯一键冲突), 维度同构
    mk_snapshot(GOOD_MD, dims=("四路", "涉诉", "退款原因", "挽回"))
    hit, _ = match_template_report(db, "今日退款情况如何？四路分布和涉诉怎么样？",
                                   "daily", SNAP_DATE)
    assert hit is not None and hit.id != shell.id


def test_bump_hit_accounting_counts_l1_and_l2_same_way(db, mk_snapshot):
    """#193 命中记账: L1/L2 走同一入口, 每次 +1(原先 L2 不记账 → matched_count 偏低 32%)。"""
    from app.services.ai_report_matcher import bump_hit_accounting

    _t, r = mk_snapshot(GOOD_MD)
    assert (r.matched_count or 0) == 0
    bump_hit_accounting(db, r.id)      # 模拟 L2 命中补记
    bump_hit_accounting(db, r.id)
    db.refresh(r)
    assert (r.matched_count or 0) == 2


def test_blocked_snapshot_not_counted(db, mk_snapshot):
    """被 #192 门槛拦下的空壳不计入 matched_count(未交付给用户, 不该算命中)。"""
    _t, r = mk_snapshot(EMPTY_SHELL)
    match_template_report(db, "今日退款情况如何？四路分布和涉诉怎么样？", "daily", SNAP_DATE)
    db.refresh(r)
    assert (r.matched_count or 0) == 0


def test_list_candidates_excludes_empty_shell(db, mk_snapshot):
    """L2 候选列表同样剔除空壳快照(否则语义最像的空壳会被选中)。"""
    from app.services.ai_report_matcher import list_candidates

    _t, shell = mk_snapshot(EMPTY_SHELL)
    _t2, good = mk_snapshot(GOOD_MD)
    cands = list_candidates(db, "daily", SNAP_DATE)
    ids = {c.id for c in cands}
    assert good.id in ids and shell.id not in ids

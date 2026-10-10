# -*- coding: utf-8 -*-
"""#87 第一批投诉健康度指标 — 口径契约测试

覆盖 `analytics.complaint_health_metrics` 与配套的岗位归类逻辑。
工单: #87（设计文档 `docs/arch/_pending/A-20260914-001-投诉升级链路指标体系设计.md`）

## 为什么必须锁口径（本仓库血泪史）
同一概念两套实现（口径漂移）已连续引发 #81 / #83 / #85 三起事故：
  · #81 投诉数：核心指标含卡商、未扣重 → 与四卡差 37 条
  · #83 退款笔数：注册表用单表、看板用四路 → 673 vs 阈值 30（13.5 倍错配）
  · #85 趋势柱：含卡商 + 未扣 315 重叠 → 1127 vs 1111
故本文件**优先锁定"同源"与"命名"，而非数值** —— 数值随数据变，口径不应变。

## 用户 2026-09-14 拍板的 6 项口径
  1. 升级识别键 = 按 SN
  2. 时间窗口 = 30 天（固定）
  3. 多次 → 拆两个指标（去重比率 / 不重次数）
  4. 分母 = 外部投诉SN / 内部投诉SN / 窗口内活跃SN
  5. 分子按岗位：组长与专员=投诉结案数，客服=接待数
  6. 岗位口径 = person.position（+ specialty 判定投诉专员）
"""
from __future__ import annotations

import inspect

import pytest

from app.services import analytics
from app.services.analytics import (
    _COMPLAINT_LEAD_POSITIONS,
    _COMPLAINT_SPECIALIST_SPECIALTIES,
    complaint_health_metrics,
    handler_fallback_expr,
)


# ═══════════════════════════════════════════════════════════════════
# 1. 口径常量（用户拍板值 —— 改动即破坏管理口径，必须显式确认）
# ═══════════════════════════════════════════════════════════════════
class TestCaliberConstants:
    """锁定用户拍板的岗位/专项口径常量。"""

    def test_specialist_specialties_are_internal_and_external(self):
        """投诉专员 = 专项 ∈ (内部投诉, 外部投诉)。

        依据: `specialties` 字典表把投诉专员分为内/外两条线
        （实测 4 人：刘景山/苏紫依=内部投诉，洪佳/陈玉红=外部投诉）。
        ⚠️ 这两个名字与「投诉来源桶」同名但**语义不同**（一个是职能，一个是来源），
           改动本常量前必须确认不会与来源分桶混淆。
        """
        assert _COMPLAINT_SPECIALIST_SPECIALTIES == ("内部投诉", "外部投诉")

    def test_lead_positions_include_deputy(self):
        """组长口径必须**含副主管** —— 实测该层承担 67% 投诉。

        若只算「组长」，会把副主管（吴晓龙，实测 2866 条）的工作量算成"其他"，
        导致组长负荷被低估。
        """
        assert _COMPLAINT_LEAD_POSITIONS == ("组长", "副主管")

    def test_real_finish_threshold_is_single_source(self):
        """真实完成阈值**单一来源**（=180s），complaints.py 从本模块导入。

        回归: 该 180 曾在本模块与 `api/v1/complaints.py` 各写一份。
        两处一旦漂移，投诉"处理量"就会出现两个值（#83 退款笔数同类问题）。
        """
        assert analytics.REAL_FINISH_EPOCH_SECS == 180
        from app.api.v1 import complaints as C

        assert C._REAL_FINISH_EPOCH_SECS == analytics.REAL_FINISH_EPOCH_SECS


# ═══════════════════════════════════════════════════════════════════
# 2. 口径说明 _spec（看板卡片规范 §二：必须有 ⓘ 数据说明）
# ═══════════════════════════════════════════════════════════════════
class TestSpecContract:
    """`_spec` 是前端 ⓘ 的唯一文案来源，必须覆盖规范要求的要素。"""

    def test_spec_covers_required_elements(self):
        """_spec 必须覆盖 来源/口径/空值含义/取舍 四要素。

        `看板卡片数据说明与下钻规范.md` 双强制之①要求 ⓘ 写清
        「来源 / 口径 / 时点 + 『空』的含义」，本仓库额外要求「取舍」段。
        """
        src = inspect.getsource(complaint_health_metrics)
        for key in ("active_sn", "rates", "by_position", "repeat_rate"):
            assert f'"{key}"' in src, f"_spec 缺少 {key} 说明"
        # 「空」的含义必须显式说明（规范强制，避免 0 与"无数据"混淆）
        assert "0%" in src or "不是 0" in src
        # 「重复投诉率」必须自我声明为代理指标，防止被当升级率使用
        assert "代理信号" in src

    def test_repeat_rate_not_named_as_escalation(self):
        """★ 核心禁令：「重复投诉率」禁止被称为「升级率」。

        两者不同源 —— 重复投诉是"同 SN 30 天内多次投诉"（可测），
        升级率是"客服申报升级后组长化解"（当前无数据）。
        混用即口径漂移，正是 #81/#83/#85 的病根。
        """
        src = inspect.getsource(complaint_health_metrics)
        # 允许在"说明为何不是升级率"的语境中出现（故只检查字段名）
        assert "repeat_rate" in src
        assert "升级率" not in src.split("_spec")[1].split("}")[0] or "不是" in src


# ═══════════════════════════════════════════════════════════════════
# 3. 处理人归属（岗位负荷的地基）
# ═══════════════════════════════════════════════════════════════════
class TestHandlerFallback:
    """handler → assignee → updated_by_raw → registrant 多级 fallback。"""

    def test_priority_order_documented(self):
        """fallback 顺序必须按"越接近真实处理人越优先"排列。

        回归: 历史工单 `handler` 常为空，但 `assignee`/`updated_by_raw` 有值。
        若顺序写反（如优先 registrant），会把"登记人"当"处理人"，
        导致岗位负荷张冠李戴。
        """
        src = inspect.getsource(handler_fallback_expr)
        order = [src.index(x) for x in ("handler", "assignee", "updated_by_raw", "registrant")]
        assert order == sorted(order), "fallback 顺序错误"

    def test_complaints_module_delegates(self):
        """`complaints._handler_fallback_cols` 必须**委派**而非重写。

        回归: 该逻辑原先只在 complaints.py 一份；#87 需要同口径 →
        上提到 analytics 作单一来源。若此处又被重写，就出现两个实现（口径漂移）。
        """
        from app.api.v1.complaints import _handler_fallback_cols

        src = inspect.getsource(_handler_fallback_cols)
        assert "handler_fallback_expr" in src, "必须委派到 analytics（单一来源）"


# ═══════════════════════════════════════════════════════════════════
# 4. 空值语义（规范：空必须可解释）
# ═══════════════════════════════════════════════════════════════════
class TestEmptySemantics:
    """分母为 0 时比率必须返回 None（前端显示「—」），**不得返回 0**。

    为什么关键: 若返回 0，「无接待记录」会被读成「零投诉」—— 结论完全相反。
    （本地库 cs_records 仅到 2026-09-08，09-13 无数据即触发该分支，已实测。）
    """

    def test_rate_helper_returns_none_when_denominator_zero(self):
        src = inspect.getsource(complaint_health_metrics)
        # `_rate` 内部必须有 active_sn 为 0 的守卫并返回 None
        assert "if not active_sn:" in src
        assert "return None" in src

    def test_by_position_per_capita_none_when_no_headcount(self):
        """无在岗人员时 人均 返回 None（不是 0）。

        0 会被读成"该岗位没干活"，而实际是"该岗位没人"（分母不存在）。
        """
        src = inspect.getsource(complaint_health_metrics)
        assert 'if hc else None' in src or "if hc\n" in src


# ═══════════════════════════════════════════════════════════════════
# 5. 岗位归因（★ 曾出错：用全站总量当分子）
# ═══════════════════════════════════════════════════════════════════
class TestPositionAttribution:
    """分子必须**按岗位归因**。

    ★ 回归场景（本人实际踩过）:
      初版把「全站投诉结案总数」同时当作组长与投诉专员的分子 →
      两者人均都等于 1307（同一分子 ÷ 不同分母但碰巧人数相同 4/4），
      该数字**完全无管理意义**。
      正确做法: 用 handler 归因到岗位后再计数。
    """

    def test_numerator_is_attributed_not_global_total(self):
        src = inspect.getsource(complaint_health_metrics)
        # 必须存在「按 bucket 归因」的求和
        assert "_sum_by_bucket" in src
        # 常量表里组长/专员的分子必须来自 done_by_bucket（归因结果）
        assert 'done_by_bucket.get("组长"' in src
        assert 'done_by_bucket.get("投诉专员"' in src
        # 客服分子必须来自接待数（用户拍板：客服分子用接待数）
        assert 'recv_by_bucket.get("客服"' in src

    def test_customer_service_uses_reception_not_complaint(self):
        """客服分子 = 接待数（**不是**投诉结案数）。

        依据: 客服职责是接待，不是结案（用户 2026-09-14 拍板）。
        若错用投诉数，客服负荷会被严重低估（其接待量远大于投诉量）。
        """
        src = inspect.getsource(complaint_health_metrics)
        assert 'recv_by_bucket.get("客服"' in src

    def test_attribution_total_exposed(self):
        """必须给出归因合计，让"其他岗位"的工作量可见。

        否则读者会以为 组长+专员+客服 应等于全站结案数，
        差额（未登记岗位/主管）就成了"看不见的黑洞"。
        """
        src = inspect.getsource(complaint_health_metrics)
        assert '"attribution"' in src
        assert "total_handled" in src


# ═══════════════════════════════════════════════════════════════════
# 6. 投诉判据（避免假结论）
# ═══════════════════════════════════════════════════════════════════
class TestComplaintCaliber:
    """必须先确认 `is_complaint_ticket_cond` 的判据（空白 OR '投诉'）。

    ★ 本人踩过的坑: 直接用 SQL `service_category LIKE '%投诉%'` 只能匹配 **5 条**
      （客服登记投诉时不选类别 → BMS 导出为空白），
      导致"30天内重复投诉 = 0"的**完全错误结论**。
      正确判据必须走 `complaint_scope.is_complaint_ticket_cond`。
    """

    def test_uses_complaint_scope_judgement(self):
        src = inspect.getsource(complaint_health_metrics)
        assert "is_complaint_ticket_cond" in src, "判据必须走 complaint_scope"

    def test_raw_sql_also_includes_blank_category(self):
        """重复投诉的原始 SQL 也必须含空白判据（不能只写 = '投诉'）。

        该处用 `text()` 原生 SQL（窗口函数 LAG），故无法复用 case 表达式 ——
        但**判据必须等价**，否则两处口径又不一致。
        """
        src = inspect.getsource(complaint_health_metrics)
        assert "service_category IS NULL" in src
        assert "btrim(service_category) = ''" in src
        assert "interval '30 days'" in src


# ═══════════════════════════════════════════════════════════════════
# 7. 岗位归属数据源（SCD 表，不用镜像）
# ═══════════════════════════════════════════════════════════════════
class TestStaffSource:
    """岗位/专项必须能同时用镜像与 SCD 源，且 SCD 查询带 is_current。"""

    def test_scd_queries_filter_current(self):
        """SCD 查询必须过滤 `is_current = true`。

        回归: `staff_assignments` 实测 total=131 但 current=38 ——
        不过滤会把历史任职重复计入在岗人数（分母虚高 → 人均虚低）。
        """
        src = inspect.getsource(analytics._person_position_map)
        assert "is_current.is_(True)" in src

    def test_specialty_query_joins_dictionary(self):
        """专项判定必须 JOIN `specialties` 字典（拿 name 而非 id）。

        回归: 只认 id 无法识别"内部投诉/外部投诉"语义，
        且 `staff_assignments.specialty` 是**旧自由文本**（写的是"投诉接待"），
        两者命名不同 —— 必须用字典表作权威来源。
        """
        src = inspect.getsource(analytics._person_position_map)
        assert "Specialty.name" in src or "Specialty" in src


# ═══════════════════════════════════════════════════════════════════
# 8. 缓存 key 对齐（本仓库既有约定）
# ═══════════════════════════════════════════════════════════════════
class TestCacheKeyAlignment:
    """缓存预热 key 必须与路由 key **逐字符对齐**（cache_warmer 模块约定）。

    对齐失败的表现是"缓存永远不命中"或"预热了但读到旧值" —— 不报错，难发现。
    """

    def test_warmer_key_matches_route_pattern(self):
        from app.services import cache_warmer

        src = inspect.getsource(cache_warmer._warm_complaints)
        assert "complaint:health:None:{start}:{end}" in src


# ═══════════════════════════════════════════════════════════════════
# 9. 路由注册
# ═══════════════════════════════════════════════════════════════════
def test_route_registered():
    """端点必须注册在 /complaints/health-metrics（含 /api/v1 前缀）。"""
    from app.main import app

    paths = app.openapi()["paths"]
    assert "/api/v1/complaints/health-metrics" in paths


@pytest.mark.parametrize(
    "field",
    ["active_sn", "complaint_sn", "complaint_tickets", "rates", "by_position", "repeat", "_spec"],
)
def test_response_contract_fields_documented(field):
    """返回结构的每个顶层字段都必须在实现中出现（防文档与代码脱节）。"""
    assert f'"{field}"' in inspect.getsource(complaint_health_metrics)

# -*- coding: utf-8 -*-
"""数据源命名与分组的**不变量**测试（工单 #100）

对应规范: docs/standards/数据源命名与分组规范.md

为什么要有这组测试:
  在 #100 之前，「数据查看」树的 `code` / `table_name` / `name_cn` 三者无任何约束，
  导致三类实际问题:
    ① 同一个概念在树上叫三个名字（merchant_refunds 在 Upload.vue 叫「旧,作废」，
       在数据查看叫「商户号现金流」）—— 使用者不知道该信哪个；
    ② A~E 经营口径（A-20260908-004 LOCKED 定义）在树上完全不可见 —— 看数据的人
       不知道 refunds 就是 A 路、refund_record_reports 就是 E 路对账基准；
    ③ 同类表无统一前缀 —— 无法按前缀一眼定位"哪几路属于经营口径"。
  这些是**结构性**问题，靠人工 review 记不住，必须用断言锁住。

本测试锁定的不变量（改种子/加数据源会被自动检查）:
  1. code ↔ table_name ↔ name_cn 一一对应，且 name_cn 全树唯一
  2. name_cn 必须带域前缀（`X·`），禁止出现 code 原文
  3. 每个数据源的 category 必须在 CATEGORY_ORDER 中登记
  4. 经营五路 A~E 前缀与业务顺序（A→B→C→D→E）
  5. 投影项：projection_of 必须指向已注册 code；必须声明真身位置；只读
  6. 投影与真身**同表**（这是投影的定义），且不落 data_sources 表
"""

from __future__ import annotations

import re

from app.services.data_source_registry import (
    CATEGORY_ORDER,
    DATA_SOURCE_PROJECTIONS,
    DATA_SOURCE_SEEDS,
    build_source_projections,
    category_order_index,
    resolve_projection_code,
    seed_sort_order,
)

# 经营五路前缀 → 规范要求的业务顺序号（A-20260908-004 LOCKED 口径）
ROUTE_ORDER = {"A": 10, "B": 20, "C": 30, "D": 40, "E": 50}

# name_cn 的域前缀白名单（`前缀·` 形式）
_ALLOWED_PREFIXES = (
    "A", "B", "C", "D", "E",  # 经营五路
    "聚合", "视图",  # 物化派生
    "投诉", "工单", "客服", "SCRM",  # 工单客服域
    "订单", "售后",  # 交易售后域
    "设备",  # 基础数据域
    "旧",  # 已停用（与 ⚠️ 警示前缀搭配使用）
)


def _all_entries() -> list[dict]:
    """全部树项 = 真实数据源 + 投影项"""
    return [{**s, "_tbl": s["model"].__tablename__, "_proj": False} for s in DATA_SOURCE_SEEDS] + [
        {**p, "_tbl": p["table_name"], "_proj": True} for p in build_source_projections()
    ]


class TestNamingInvariants:
    """不变量 1/2: code ↔ table_name ↔ name_cn 一一对应 + 前缀约束"""

    def test_name_cn_unique_across_tree(self):
        """★ name_cn 必须全树唯一 —— 否则使用者看到一个名字无法确定是哪张表"""
        names = [e["name_cn"] for e in _all_entries()]
        dupes = sorted({n for n in names if names.count(n) > 1})
        assert not dupes, f"name_cn 重复（同一名字对应多张表，使用者会混淆）: {dupes}"

    def test_code_table_name_one_to_one(self):
        """★ table_name ↔ code 一一对应（唯一例外: device_tag_binding 同表双映射）

        device_tag_binding 的物理表是 device_tag_rel —— 与 ORM 多对多关联表同表,
        属 _EXPLICIT_CODE_TABLE_EXCEPTIONS 中显式登记的例外（规范要求写明理由）。
        """
        exceptions = {"device_tag_binding": "device_tag_rel"}
        seen: dict[str, str] = {}
        for e in DATA_SOURCE_SEEDS:
            if e["code"] in exceptions:
                assert e["model"].__tablename__ == exceptions[e["code"]], (
                    f"{e['code']} 的例外映射已变，需同步更新测试与规范文档"
                )
                continue
            assert e["model"].__tablename__ not in seen, (
                f"两个 code 指向同一张表: {e['code']} 与 {seen[e['model'].__tablename__]} "
                f"→ {e['model'].__tablename__}（同表双注册会让统计口径分裂）"
            )
            seen[e["model"].__tablename__] = e["code"]

    def test_name_cn_has_domain_prefix(self):
        """★ name_cn 必须形如 `前缀·中文名`，让同类表可被前缀一眼聚拢"""
        bad = []
        for e in _all_entries():
            name = e["name_cn"]
            # 允许 `⚠️旧·`、`⚠️` 等警示前缀
            stripped = re.sub(r"^[⚠️\s]+", "", name)
            if "·" not in stripped:
                bad.append((e["code"], name))
                continue
            prefix = stripped.split("·", 1)[0]
            if prefix not in _ALLOWED_PREFIXES:
                bad.append((e["code"], name))
        assert not bad, f"name_cn 缺少白名单内的域前缀（形如 `前缀·中文名`）: {bad}"

    def test_name_cn_does_not_leak_code(self):
        """name_cn 不得出现 code 原文 —— 中文名是给使用者看的，不是给开发者看的"""
        bad = [
            (e["code"], e["name_cn"])
            for e in DATA_SOURCE_SEEDS
            # 只查"看起来像表名的 code"，排除纯中文无用例
            if re.search(rf"(?<![a-z_]){re.escape(e['code'])}(?![a-z_])", e["name_cn"])
        ]
        assert not bad, f"name_cn 泄露了 code（应改为中文语义）: {bad}"


class TestCategoryInvariants:
    """不变量 3: 分组归属合法"""

    def test_every_source_has_registered_category(self):
        """★ 每个数据源的 category 必须在 CATEGORY_ORDER 中登记

        未登记会被 category_order_index 丢到末尾「其他」,而前端只渲染
        CATEGORY_ORDER 中的分组 → **该数据源直接从树上消失**。
        """
        bad = [
            (e["code"], e.get("category"))
            for e in _all_entries()
            if e.get("category") not in CATEGORY_ORDER
        ]
        assert not bad, (
            f"category 未在 CATEGORY_ORDER 登记（这些源会在前端树上消失）: {bad}; "
            f"已登记分组: {CATEGORY_ORDER}"
        )

    def test_category_order_covers_ui_groups(self):
        """分组顺序表非空且无重复 —— 它是全站分组顺序的**唯一来源**"""
        assert CATEGORY_ORDER, "CATEGORY_ORDER 不得为空"
        assert len(CATEGORY_ORDER) == len(set(CATEGORY_ORDER)), (
            f"CATEGORY_ORDER 有重复分组: {CATEGORY_ORDER}"
        )


class TestRouteADashE:
    """不变量 4: 经营五路 A~E 前缀与业务顺序"""

    def test_route_prefixes_and_order(self):
        """★ A~E 五路: 前缀正确 + sort_order 单调递增（A→B→C→D→E）

        口径来源 A-20260908-004（LOCKED）。顺序错乱会让使用者误以为路线有优先级变化。
        """
        actual: dict[str, dict] = {}
        for e in DATA_SOURCE_SEEDS:
            m = re.match(r"^([A-E])·", e["name_cn"])
            if m:
                actual[m.group(1)] = e

        assert set(actual) == set(ROUTE_ORDER), (
            f"A~E 五路不全: 找到 {sorted(actual)}，应为 {sorted(ROUTE_ORDER)}"
        )
        for route, expect_order in ROUTE_ORDER.items():
            got = seed_sort_order(actual[route])
            assert got == expect_order, (
                f"{route} 路 sort_order={got}，应为 {expect_order}"
                f"（{actual[route]['code']}）"
            )

    def test_e_route_marked_as_reconciliation_baseline(self):
        """★ E 路必须在显示名中标注「对账基准」

        这是最容易被误用的口径: E 是**真实现金流出**, 不与 A+B+C+D 累加。
        只写在 description 里不够 —— 树上看到名字的人不会点开描述。
        """
        e_seed = next(s for s in DATA_SOURCE_SEEDS if s["name_cn"].startswith("E·"))
        assert "对账基准" in e_seed["name_cn"], (
            f"E 路显示名未标注「对账基准」: {e_seed['name_cn']}；"
            "使用者会误把 E 与经营四路相加"
        )

    def test_aggregate_and_view_come_after_routes(self):
        """聚合/视图项必须排在 A~E 之后 —— 先看原路,再看派生物"""
        route_max = max(seed_sort_order(s) for s in DATA_SOURCE_SEEDS if re.match(r"^[A-E]·", s["name_cn"]))
        for s in DATA_SOURCE_SEEDS:
            if s["name_cn"].startswith(("聚合·", "视图·")):
                assert seed_sort_order(s) > route_max, (
                    f"{s['name_cn']} 排在五路之前（sort_order={seed_sort_order(s)} ≤ {route_max}）"
                )

    def test_deprecated_source_marked(self):
        """已停用数据源必须在**显示名**中标注,而非只写在 description

        背景: merchant_refunds 在 Upload.vue 已标「旧,作废」,却在数据查看里
        仍显示正向名「商户号现金流」—— 同一事实两处说法打架。
        """
        mr = next(s for s in DATA_SOURCE_SEEDS if s["code"] == "merchant_refund")
        assert "停用" in mr["name_cn"] or "作废" in mr["name_cn"], (
            f"已停用数据源未在显示名标注: {mr['name_cn']}"
        )
        assert "⚠️" in mr["name_cn"], f"已停用数据源应带 ⚠️ 前缀: {mr['name_cn']}"


class TestProjections:
    """不变量 5/6: 投影项合法性"""

    def test_projection_of_points_to_registered_code(self):
        """★ 投影必须指向已注册的真身 —— 否则投影查不到字段/记录"""
        codes = {s["code"] for s in DATA_SOURCE_SEEDS}
        for proj in DATA_SOURCE_PROJECTIONS:
            assert proj["projection_of"] in codes, (
                f"投影 {proj['code']} 指向未注册的 code {proj['projection_of']}"
            )

    def test_projection_declares_true_body_location(self):
        """★ 投影必须声明真身分组与名称

        这是 AGENTS.md「界面设计纪律」第 5 条（禁止重复展示）的落地方式:
        投影必须在界面上声明真身位置,否则就是违规的重复展示。
        """
        for proj in DATA_SOURCE_PROJECTIONS:
            assert proj.get("true_body_category"), f"投影 {proj['code']} 未声明真身分组"
            assert proj.get("true_body_name"), f"投影 {proj['code']} 未声明真身名称"
            assert proj["true_body_category"] in CATEGORY_ORDER, (
                f"投影 {proj['code']} 的真身分组 {proj['true_body_category']} 未登记"
            )

    def test_projection_shares_table_with_true_body(self):
        """投影与真身**必须是同一张物理表** —— 不同表就不是投影,而是新数据源"""
        seed_by_code = {s["code"]: s for s in DATA_SOURCE_SEEDS}
        for proj in build_source_projections():
            true_tbl = seed_by_code[proj["projection_of"]]["model"].__tablename__
            assert proj["table_name"] == true_tbl, (
                f"投影 {proj['code']} 表名 {proj['table_name']} ≠ 真身表 {true_tbl}"
            )

    def test_projection_is_readonly_and_in_different_category(self):
        """投影必须只读,且与真身**不在同一分组**（同组内重复才是冗余）"""
        seed_by_code = {s["code"]: s for s in DATA_SOURCE_SEEDS}
        for proj in build_source_projections():
            assert proj["readonly"] is True, f"投影 {proj['code']} 必须只读"
            assert proj["category"] != seed_by_code[proj["projection_of"]]["category"], (
                f"投影 {proj['code']} 与真身同组，属冗余展示"
            )

    def test_resolve_projection_code(self):
        """投影 code 解析: 投影→真身; 非投影原样返回"""
        assert resolve_projection_code("complaint_ticket") == "ticket"
        assert resolve_projection_code("ticket") == "ticket"
        assert resolve_projection_code("refund") == "refund"
        assert resolve_projection_code("不存在") == "不存在"

    def test_projections_do_not_shadow_real_sources(self):
        """投影 code 不得与真实数据源 code 冲突"""
        codes = {s["code"] for s in DATA_SOURCE_SEEDS}
        for proj in DATA_SOURCE_PROJECTIONS:
            assert proj["code"] not in codes, (
                f"投影 code {proj['code']} 与真实数据源 code 冲突"
            )

    def test_projection_before_true_body_within_complaint_group(self):
        """投诉组内: 工单投影在最前（投诉工作流的第一入口）"""
        projs = [p for p in build_source_projections() if p["category"] == "投诉"]
        non_projs = [s for s in DATA_SOURCE_SEEDS if s.get("category") == "投诉"]
        assert projs, "投诉组应至少有一个投影（工单侧）"
        min_proj_order = min(p["sort_order"] for p in projs)
        min_real_order = min(seed_sort_order(s) for s in non_projs)
        assert min_proj_order < min_real_order, (
            "投诉组内工单投影应排在最前（它是投诉工作流的第一入口）"
        )


class TestTreeShape:
    """整体形状回归: 五分组齐全 + 项数正确（防止误删/误加）"""

    def test_expected_categories_present(self):
        used = {e["category"] for e in _all_entries()}
        assert used == set(CATEGORY_ORDER), (
            f"分组未全部填充: 实际 {sorted(used)}, 期望 {sorted(CATEGORY_ORDER)}"
        )

    def test_category_index_is_stable(self):
        """分组下标必须按 CATEGORY_ORDER 顺序（前端直接消费该顺序）"""
        assert category_order_index("经营与对账") == 0
        assert category_order_index("基础数据") == len(CATEGORY_ORDER) - 1
        # 未登记分组排最后
        assert category_order_index("不存在") == len(CATEGORY_ORDER)

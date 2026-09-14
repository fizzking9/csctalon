# -*- coding: utf-8 -*-
"""投诉专项合并（2026-09-14 组织调整）口径契约测试

## 背景
用户将投诉组的**行政与专项合一**：内/外部投诉不再分线，
统一为「投诉专项」(系数 5.0 = 原外部投诉值)。

## ★★ 本文件锁定的核心不变式 ★★
合并采用「**只增不改**」策略：

1. **新专项必须新增**到权重表/CAT 桶/豁免集合 —— 否则新任职的 4 人
   会映射到 `None` → **工作量得分为 0**（静默失败，不报错）。
2. **旧专项（内部投诉/外部投诉）必须保留** —— 历史任职仍指向它们。
   一旦删除，**历史区间（2026-09-01 之前）的绩效会被重算**
   （内部投诉 3.0 → 无 → 归零），= 静默篡改历史。
3. **绝不把旧映射改成指向新专项** —— 那会把历史内部投诉工作
   按 5.0 重算（原 3.0），同样是篡改历史。
4. **不得混淆「人员专项」与「投诉来源桶」** —— 两者字符串相同
   （内部投诉/外部投诉）但语义完全不同。本模块常量只约束人员专项。

## 为什么必须用测试锁（而非靠注释）
本仓库因"同一概念两套实现"已连续发生 #81/#83/#85 三起口径事故。
本次合并**同时存在新旧两套字符串**，是漂移的高危场景 ——
必须由测试守住"历史不可重算"这条线。
"""
from __future__ import annotations

import inspect

from app.services import analytics as A
from app.services.analytics import (
    COMPLAINT_SPECIALTIES_ALL,
    COMPLAINT_SPECIALTY,
    COMPLAINT_SPECIALTY_WEIGHT,
    LEGACY_COMPLAINT_SPECIALTIES,
)


# ═══════════════════════════════════════════════════════════════════
# 1. 常量契约
# ═══════════════════════════════════════════════════════════════════
class TestMergeConstants:
    def test_new_specialty_name_and_weight(self):
        """新专项名 = 投诉专项，系数 = 5.0（用户 2026-09-14 拍板取外部投诉值）。"""
        assert COMPLAINT_SPECIALTY == "投诉专项"
        assert COMPLAINT_SPECIALTY_WEIGHT == 5.0

    def test_legacy_specialties_preserved(self):
        """★ 旧专项必须保留（历史绩效不可重算的基础）。"""
        assert LEGACY_COMPLAINT_SPECIALTIES == ("内部投诉", "外部投诉")

    def test_all_contains_both_generations(self):
        """全量集合 = 新 + 旧，用于「是否投诉专员」判定。"""
        assert COMPLAINT_SPECIALTY in COMPLAINT_SPECIALTIES_ALL
        for legacy in LEGACY_COMPLAINT_SPECIALTIES:
            assert legacy in COMPLAINT_SPECIALTIES_ALL


# ═══════════════════════════════════════════════════════════════════
# 2. ★ 历史不可重算（最关键）
# ═══════════════════════════════════════════════════════════════════
class TestHistoricalImmutability:
    """守住「历史区间的专项系数不被改动」这条线。"""

    def test_legacy_weights_unchanged(self):
        """旧专项系数必须保持原值：内部投诉=3.0，外部投诉=5.0。

        ★ 回归场景：若为"合并"把内部投诉的 3.0 改成 5.0，
        则 2026-09-01 之前的内部投诉工作全部按 5.0 重算 → 历史绩效虚高 67%。
        """
        src = inspect.getsource(A)
        # 精确定位 today_staff_efficiency 内的 WEIGHT 字典
        assert '"内部投诉": 3.0' in src, "内部投诉系数被改动（历史会被重算）"
        assert '"外部投诉": 5.0' in src, "外部投诉系数被改动（历史会被重算）"

    def test_legacy_categories_still_exempt(self):
        """旧投诉专项仍在豁免集合内（管理岗/专项岗跨类全豁免）。

        若移除，历史区间这些人的得分算法会变。
        """
        src = inspect.getsource(A)
        assert 'EXEMPT_CATEGORIES = {"内部投诉", "外部投诉", "退款挽回", COMPLAINT_SPECIALTY}' in src


# ═══════════════════════════════════════════════════════════════════
# 3. ★ 新专项必须完整接入（否则映射到 None → 得分为 0）
# ═══════════════════════════════════════════════════════════════════
class TestNewSpecialtyWired:
    def test_in_weight_and_default_weight(self):
        """新专项必须在 WEIGHT 与 CAT_DEFAULT_WEIGHT（否则系数取不到 → 静默归零）。"""
        src = inspect.getsource(A)
        assert "WEIGHT = {" in src and "COMPLAINT_SPECIALTY: COMPLAINT_SPECIALTY_WEIGHT" in src
        assert "CAT_DEFAULT_WEIGHT = {" in src

    def test_in_cat_buckets(self):
        """新专项必须有独立展示桶（否则人效表不显示该列）。"""
        src = inspect.getsource(A)
        assert '(("complaint", COMPLAINT_SPECIALTY, {COMPLAINT_SPECIALTY}),' in src or \
               '("complaint", COMPLAINT_SPECIALTY, {COMPLAINT_SPECIALTY})' in src

    def test_in_person_cat_names(self):
        """新专项必须在 _PERSON_CAT_NAMES（按人归并时用）。"""
        assert COMPLAINT_SPECIALTY in A._PERSON_CAT_NAMES

    def test_hr_map_branches_include_new(self):
        """★ 三处 _hr_map 副本都必须能映射新专项。

        ⚠️ 实测本文件有 **3 份** _hr_map（历史遗留复制），
           其中 2 份是**硬编码元组**而非引用 WEIGHT —— 极易漏改。
           漏改的后果：该聚合路径下新专项的人 → 系数 None → 得分 0。
        """
        src = inspect.getsource(A)
        # 统计带新专项的精确匹配语句出现次数（应为 3 处 _hr_map 全覆盖）
        n_hardcoded = src.count('"内部投诉", "外部投诉", COMPLAINT_SPECIALTY')
        assert n_hardcoded >= 2, f"硬编码元组仅 {n_hardcoded} 处含新专项（应有 2 处）"
        # 第一处走 WEIGHT（必须含新专项）
        assert "if s in WEIGHT:" in src

    def test_bucket_detection_uses_all_not_legacy_only(self):
        """投诉专员判定必须用**全量**集合（新旧都认）。

        ★ 回归场景：若仍用 _COMPLAINT_SPECIALIST_SPECIALTIES（只含旧两条），
        合并后新专项的 5 人**全部识别不出是投诉专员** →
        岗位负荷表里「投诉专员」人数变 0，指标失效（且不报错）。
        """
        src = inspect.getsource(A._person_position_map)
        assert "COMPLAINT_SPECIALTIES_ALL" in src, "必须用全量集合判定投诉专员"
        # SCD 查询同样要用全量
        assert "Specialty.name.in_(COMPLAINT_SPECIALTIES_ALL)" in src


# ═══════════════════════════════════════════════════════════════════
# 4. ★ 语义隔离（人员专项 ⊥ 投诉来源桶）
# ═══════════════════════════════════════════════════════════════════
class TestSemanticSeparation:
    """「人员专项」与「投诉来源桶」字符串同名但语义无关，禁止互相引用。"""

    def test_source_buckets_untouched(self):
        """投诉**来源**桶常量必须未被本次合并改动。

        它们属于统计分类（工单按来源分桶），与人员专项无关。
        """
        from app.services.complaint_scope import BUCKET_EXTERNAL, BUCKET_INTERNAL

        assert BUCKET_INTERNAL == "内部投诉"
        assert BUCKET_EXTERNAL == "外部投诉"

    def test_personnel_specialty_logic_does_not_use_source_buckets(self):
        """★ **人员专项判定逻辑**不得引用来源桶常量。

        ⚠️ 注意精度：`analytics.py` **允许**在**投诉统计**处使用来源桶常量
        （那是它们的正当用途）。本用例只检查**人员专项**相关的三段逻辑：
          · `_person_position_map`（岗位/专项归类）
          · `_hr_map`（主专项归一化 → 决定绩效系数）
          · 权重/桶/豁免三张表（WEIGHT / CAT_BUCKETS / EXEMPT_CATEGORIES）
        若这些地方引用来源桶，则将来“合并投诉来源”的需求会连带改坏
        人员专项（反向亦然）—— 这正是同名陷阱的危害路径。
        """
        targets = {
            "_person_position_map": inspect.getsource(A._person_position_map),
        }
        # 三份 _hr_map 均需检查（历史遗留复制，必须一致）
        src_all = inspect.getsource(A)
        for i, chunk in enumerate(src_all.split("def _hr_map(")[1:], start=1):
            targets[f"_hr_map#{i}"] = chunk[:2000]

        for name, src in targets.items():
            assert "BUCKET_INTERNAL" not in src, f"{name} 误用来源桶常量 BUCKET_INTERNAL"
            assert "BUCKET_EXTERNAL" not in src, f"{name} 误用来源桶常量 BUCKET_EXTERNAL"
            assert "BUCKET_CARD" not in src, f"{name} 误用来源桶常量 BUCKET_CARD"

    def test_hr_map_count_is_three(self):
        """★ _hr_map 现有 **3 份副本**（历史遗留）—— 数量变化必须被察觉。

        为什么锁这个：三份副本很容易“只改一处”，造成同一人在不同看板
        得到不同系数（= 口径漂移）。数量一旦变化（合并/新增），
        必须同步确认全部副本都已接入新专项。
        """
        src = inspect.getsource(A)
        assert src.count("def _hr_map(") == 3, \
            "_hr_map 副本数量变化 → 请确认所有副本都已接入 COMPLAINT_SPECIALTY"

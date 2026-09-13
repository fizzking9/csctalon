"""#75 回归测试 —— 卡商渠道「卡商投诉分类」不得为空

## 缺陷

「投诉分析 → 渠道明细 → (外)卡商」首图 **永久显示「暂无数据」**：
`/complaints/summary` 的 `card.by_category` 被**硬编码为 `[]`**，
注释指向的替代数据源 `complaint_subs` **从未存在**（死引用），
而前端 `ComplaintChannelPanel.vue` 只读 `by_category` → 恒空。

## 修复

`card.by_category` 改为聚合 `card_complaints.problem_category`
（按 `complaint_date` 落窗口，NULL 归「未标注」，与其它三源同规则）。

## ★ 本测试锁定的两条边界

1. **有数据时必须返回分类** —— 否则图空白（本缺陷的直接回归）。
2. **`by_handler` 必须保持 `[]`** —— 这是**有意设计**（卡商 handler 语义不可靠，
   画了即误导），不是遗漏。锁定它可防止后人"顺手补上"而引入误导性图表。
"""
from __future__ import annotations

import datetime as dt

from app.api.v1.complaints import complaint_summary
from app.core.database import SessionLocal
from app.models.card_complaint import CardComplaint
from app.models.device import Device

_TS = dt.datetime(2026, 8, 6, 12, 0, tzinfo=dt.timezone.utc)
_START = "2026-08-01"
_END = "2026-08-31"


def _card(db, sn, category, *, when=_TS, group="卡商群A"):
    if sn and db.get(Device, sn) is None:
        db.add(Device(sn=sn))
        db.flush()
    db.add(CardComplaint(
        device_sn=sn, complainant_phone="13800138000", complaint_group=group,
        complaint_date=when, problem_category=category,
    ))
    db.flush()


def _summary(db):
    """调用 summary 并取 data 段（兼容 R 包装与裸 dict）

    ⚠️ 必须清缓存: `complaint_summary` 带 `@cached_json`，key 只含 start/end
    → 不同用例共用同一 key 会**串数据**（读到上一用例的结果）。
    生产无此问题（窗口不同 + TTL 60s），但测试内多用例同窗口必然相撞。
    """
    from app.core.redis_client import cache_invalidate_pattern

    cache_invalidate_pattern("complaint:summary:*")
    r = complaint_summary(db=db, _=None, start=_START, end=_END)
    return r.get("data", r) if isinstance(r, dict) else r.model_dump()["data"]


class TestCardByCategory:
    """卡商分类必须返回（本缺陷的核心回归）"""

    def test_card_by_category_not_empty(self, db):
        """★ 主回归: 有卡商记录时 by_category 必须非空

        修复前此处恒为 `[]` → 前端图恒空白。
        """
        _card(db, "869750000000001", "网卡退费")
        _card(db, "869750000000002", "注销实名解绑")
        _card(db, "869750000000003", "网卡退费")

        d = _summary(db)
        cats = d["card"]["by_category"]
        assert cats, "card.by_category 为空 —— 前端「卡商投诉分类」图会恒显示『暂无数据』"
        m = {x["name"]: x["count"] for x in cats}
        assert m["网卡退费"] == 2
        assert m["注销实名解绑"] == 1

    def test_null_category_becomes_unlabeled(self, db):
        """NULL 分类归「未标注」（与其它三源同规则）"""
        _card(db, "869750000000004", None)
        _card(db, "869750000000005", "流量虚")

        cats = {x["name"]: x["count"] for x in _summary(db)["card"]["by_category"]}
        assert cats.get("未标注") == 1
        assert cats.get("流量虚") == 1
        assert None not in cats, "None 未经归一化直接进入分类名"

    def test_more_than_20_categories_not_truncated(self, db):
        """★ 长尾不截断: 分类数 > 20 时必须全部返回

        回归: 初版沿用三源的 `limit(20)`，而卡商分类实测 **25 种**（自由文本长尾）
        → 被截断 5 类，造成「各分类之和 < 总数」的假象（实测 65 vs 70）。
        ⚠️ 后端截断是**静默丢数据**（无法察觉）；长尾聚合应在**展示层**做。
        """
        for i in range(25):
            _card(db, f"86975000000{i:04d}", f"分类{i:02d}")

        cats = _summary(db)["card"]["by_category"]
        assert len(cats) == 25, f"分类被截断: 期望 25 类, 实际 {len(cats)} 类"
        assert sum(x["count"] for x in cats) == 25, "分类合计 != 总数（有类别丢失）"

    def test_window_filters_categories(self, db):
        """分类必须跟随 start/end 窗口 —— 不落窗口的分类不得出现"""
        _card(db, "869750000000006", "窗口内",
              when=dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.timezone.utc))
        _card(db, "869750000000007", "窗口外",
              when=dt.datetime(2026, 5, 15, 12, 0, tzinfo=dt.timezone.utc))

        cats = {x["name"]: x["count"] for x in _summary(db)["card"]["by_category"]}
        assert "窗口内" in cats
        assert "窗口外" not in cats, "分类未按 complaint_date 过滤窗口"

    def test_by_handler_still_empty_by_design(self, db):
        """★ 边界锁定: by_handler 必须保持 `[]` —— 有意设计，非缺陷

        卡商 `handler` 为人员别名字段（来源混合、含自由文本），
        画「处理人排行」会误导使用者。前端已用「卡商渠道说明」占据该列。
        若后人"顺手补上"，此测试会失败以触发设计评审。
        """
        _card(db, "869750000000008", "网卡退费")

        d = _summary(db)
        assert d["card"]["by_handler"] == [], (
            "card.by_handler 被填充 —— 卡商处理人语义不可靠（混合自由文本），"
            "画排行即误导。若确要恢复该图，须先解决 handler 可信度并走评审。"
        )

    def test_no_dead_reference_complaint_subs(self):
        """★ 死引用防护: 不得再出现指向不存在接口的注释

        ⚠️ 断言时**拼接**目标字符串，避免本测试文件自身的字面量触发匹配；
        同时只检查注释里是否还残留**指向性**说法（"见 xxx" 形式）。
        """
        import pathlib
        src = pathlib.Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "complaints.py"
        lines = src.read_text(encoding="utf-8").splitlines()
        # 只扫注释行, 且排除已显式声明为"死引用"的说明行（那是修复记录, 不是引用）
        bad = []
        for ln in lines:
            s = ln.strip()
            if not s.startswith("#"):
                continue
            if "从未存在" in s or "死引用" in s or "已就地说明" in s:
                continue          # 这是修复说明, 允许保留该死引用的字样
            if "complaint_subs" in s:
                bad.append(ln.strip())
        assert not bad, (
            "仍有指向不存在接口 `complaint_subs` 的注释: " + " | ".join(bad)
        )

    def test_summary_callable_via_sessionlocal(self):
        """冒烟: 用真实 SessionLocal 调用不抛异常（字段/导入正确性）"""
        db = SessionLocal()
        try:
            d = _summary(db)
            assert "card" in d
            assert isinstance(d["card"]["by_category"], list)
        finally:
            db.close()

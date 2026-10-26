# -*- coding: utf-8 -*-
"""重复导入清理工具测试 (#42, 2026-09-10)

## 背景
2026-09-05 两批 workbuddy 推送的「投诉日期」被整批转置(库内年 = 2000+真值日;
库内日 = 真值年−2001; 月不变)。而 `dedup_hash = md5(日期|手机|SN|问题)` 上有
**唯一索引** → 日期错 ⇒ 指纹不同 ⇒ 唯一索引不冲突 ⇒ `ON CONFLICT` 不触发 ⇒
**INSERT 新行**。结果该批 2532 行中 2527 行(99.8%)与已有行重复。

## 工具处置
按"内容键(手机/SN/问题原文)"全局分组, 每组只留一条(优先: 有编号 > 入库早),
其余删除并在 `ingest_rejected_rows` 留痕(可追溯); 之后重算剩余行的指纹。

本测试锁定判重的**纯逻辑契约**(不触库):
  - `_content_key`: 空行返回 None(不参与判重)
  - `_rank`:       有编号优先, 其次入库早
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_SCRIPT = _BACKEND.parent / "scripts" / "dedup_complaint_dup_import.py"

if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# ⚠️ 2026-10-26 CI 迁至 GitHub Actions 后：检出**整个仓库**并设 working-directory: backend,
#    仓库根 scripts/ 随检出存在 → 本文件在 CI 中会**真正执行**(迁移前只挂载 backend/
#    时会被 skip, 覆盖率实际是缺的)。
#    下面的存在性判断仍然保留: 在只拿到 backend/ 的环境下防御性跳过,
#    避免收集期 exec_module 报 FileNotFoundError 中断整个会话。
if not _SCRIPT.exists():
    pytest.skip(
        f"去重脚本不在当前环境(仅拿到 backend/ 时): {_SCRIPT}",
        allow_module_level=True,
    )

_spec = importlib.util.spec_from_file_location("dedup_complaint_dup_import", _SCRIPT)
dd = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(dd)

_UTC = timezone.utc


class _Row:
    """轻量替身(工具只用到这几个属性)"""

    def __init__(self, id, phone=None, sn=None, problem=None, no=None, created=None):
        self.id = id
        self.complainant_phone = phone
        self.device_sn = sn
        self.problem = problem
        self.complaint_no = no
        self.created_at = created or datetime(2026, 8, 20, 6, 22, tzinfo=_UTC)


# ---------- _content_key ----------
class TestContentKey:
    def test_normal_key(self):
        r = _Row(1, phone="13800138000", sn="SN1", problem="流量虚标")
        assert dd._content_key(r) == ("13800138000", "SN1", "流量虚标")

    def test_all_empty_returns_none(self):
        """★空行(手机/SN/问题全空)必须返回 None —— 它们互相不是"同一投诉"。

        实证: id=363629(2005-02-25) 与 id=1672(2026-02-04) 全空, 用空键合并会误删。
        """
        assert dd._content_key(_Row(1)) is None
        assert dd._content_key(_Row(2, phone="  ", sn="", problem="   ")) is None

    def test_partial_kept(self):
        """只要有一项非空就是可判重的键(不应返回 None)"""
        assert dd._content_key(_Row(1, phone="138")) is not None
        assert dd._content_key(_Row(2, sn="SN1")) is not None
        assert dd._content_key(_Row(3, problem="x")) is not None

    def test_whitespace_normalized(self):
        """首尾空白必须归一, 否则同一投诉因空格差异漏判"""
        a = _Row(1, phone="13800138000 ", sn=" SN1", problem=" 流量虚标 ")
        b = _Row(2, phone="13800138000", sn="SN1", problem="流量虚标")
        assert dd._content_key(a) == dd._content_key(b)


# ---------- _rank(保留优先级) ----------
class TestRank:
    def test_numbered_beats_unnumbered(self):
        """★有编号优先: 编号是业务标识, 无编号行是旧版推送的缺失形态"""
        a = _Row(1, no="1440306002025030183693", created=datetime(2026, 9, 5, tzinfo=_UTC))
        b = _Row(2, no=None, created=datetime(2026, 8, 20, tzinfo=_UTC))
        # b 入库更早, 但 a 有编号 → a 应优先(rank 更小)
        assert dd._rank(a) < dd._rank(b)

    def test_earlier_created_wins_when_both_numbered(self):
        a = _Row(1, no="N1", created=datetime(2026, 8, 20, tzinfo=_UTC))
        b = _Row(2, no="N2", created=datetime(2026, 9, 5, tzinfo=_UTC))
        assert dd._rank(a) < dd._rank(b)

    def test_id_tiebreaker(self):
        """同编号状态 + 同入库时刻(批量插入同秒)时用 id 稳定排序"""
        t = datetime(2026, 9, 5, 10, 6, 34, tzinfo=_UTC)
        a = _Row(1, no="N1", created=t)
        b = _Row(2, no="N1", created=t)
        assert dd._rank(a) < dd._rank(b)

    def test_empty_string_no_treated_as_missing(self):
        """空字符串编号等同于无编号"""
        a = _Row(1, no="  ")
        b = _Row(2, no="N1")
        assert dd._rank(b) < dd._rank(a)


# ---------- 判重语义(用替身行模拟全局分组) ----------
class TestGroupingSemantics:
    """锁定"按内容键全局分组、每组留一条"的核心语义。

    为什么必须**全局**分组(不分批次内外): 曾按"批内/批外"分开处理, 导致
    批内多行时跳过跨批匹配, 批内"幸存者"从未与旧批孪生比较 → 二者共存
    (实证 id 362168 与 208 同时保留)。
    """

    @staticmethod
    def _dedupe(rows):
        groups: dict[tuple, list] = {}
        for r in rows:
            k = dd._content_key(r)
            if k is None:
                continue
            groups.setdefault(k, []).append(r)
        delete, keep = [], []
        for grp in groups.values():
            gs = sorted(grp, key=dd._rank)
            delete.extend(gs[1:])
            keep.append(gs[0])
        # 空键行全部保留
        keep.extend(r for r in rows if dd._content_key(r) is None)
        return keep, delete

    def test_cross_batch_duplicate_removed(self):
        """新批 ↔ 旧批 重复: 只留一条(优先有编号的)"""
        old = _Row(208, phone="189", problem="流量虚标", no="1440306002025021")
        new = _Row(362168, phone="189", problem="流量虚标", no="1440306002025021")
        keep, delete = self._dedupe([old, new])
        assert len(keep) == 1 and len(delete) == 1
        assert keep[0].id == 208   # 入库早

    def test_old_batch_internal_duplicate_also_merged(self):
        """★旧批内部重复也要收敛(实证 72121 与 157486 互为重复)。
        否则删除后仍残留重复 → 指纹仍冲突。"""
        r1 = _Row(72121, phone="158", problem="x", no="N1",
                  created=datetime(2026, 8, 23, tzinfo=_UTC))
        r2 = _Row(157486, phone="158", problem="x", no="N1",
                  created=datetime(2026, 8, 28, tzinfo=_UTC))
        keep, delete = self._dedupe([r1, r2])
        assert len(keep) == 1 and keep[0].id == 72121

    def test_new_batch_internal_duplicate_merged(self):
        t = datetime(2026, 9, 5, 10, 6, 34, tzinfo=_UTC)
        a = _Row(364577, phone="192", problem="p", no=None, created=t)
        b = _Row(361962, phone="192", problem="p", no="1440306002026", created=t)
        keep, delete = self._dedupe([a, b])
        assert len(keep) == 1
        assert keep[0].id == 361962   # 有编号者优先

    def test_empty_rows_all_kept(self):
        """空行不参与判重, 全部保留(实证: 5 行空行应保留)"""
        rows = [_Row(i) for i in range(1, 6)]
        keep, delete = self._dedupe(rows)
        assert len(keep) == 5 and not delete

    def test_unique_row_survives(self):
        rows = [_Row(1, phone="1", problem="a"), _Row(2, phone="2", problem="b")]
        keep, delete = self._dedupe(rows)
        assert len(keep) == 2 and not delete

    def test_third_duplicate_all_removed(self):
        """同键 3 行 → 删 2 留 1"""
        rows = [
            _Row(1, phone="1", problem="x", no="N"),
            _Row(2, phone="1", problem="x", no="N"),
            _Row(3, phone="1", problem="x", no="N"),
        ]
        keep, delete = self._dedupe(rows)
        assert len(keep) == 1 and len(delete) == 2


# ---------- 安全与接线 ----------
class TestSafety:
    def test_default_is_preview(self):
        """默认必须只预览(不写库) —— 防误操作"""
        import inspect

        src = inspect.getsource(dd.main)
        assert '"--apply"' in src or "'--apply'" in src
        assert "if not args.apply:" in src, "缺少预览保护"
        # 预览分支必须在执行之前 return
        prev = src.index("if not args.apply:")
        assert "return 0" in src[prev:prev + 200]

    def test_apply_requires_yes(self):
        """--apply 必须同时给 --yes"""
        import inspect

        src = inspect.getsource(dd.main)
        assert "args.apply and not args.yes" in src

    def test_rollback_on_selftest_failure(self):
        """自检不通过必须回滚(不能留下半成品)"""
        import inspect

        src = inspect.getsource(dd.main)
        assert "db.rollback()" in src
        assert "自检失败" in src

    def test_rejected_rows_kept_for_audit(self):
        """删除前必须落拒收表留痕(可追溯/可复原)"""
        import inspect

        src = inspect.getsource(dd.main)
        assert "IngestRejectedRow" in src
        assert "dup_import" in src

    def test_selftest_expires_orm_cache(self):
        """★自检前必须 expire —— 否则读到的是删除前的 ORM 缓存快照,
        会让自检恒报失败(实测踩过: 重算 5 行后仍报 5 行不一致)。"""
        import inspect

        src = inspect.getsource(dd.main)
        prev = src.index("# 4) 自检")
        assert "db.expire_all()" in src[prev:prev + 200], "自检未刷新 ORM 缓存"

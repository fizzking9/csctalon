# -*- coding: utf-8 -*-
"""AI 报告补算器测试 (工单 #152)。

覆盖 find_complete_snapshot / recompute 的:
- find_complete_snapshot: 同窗口无完整快照 → (None, None)
- find_complete_snapshot: 模板维度校验不通过 → 继续找下一份
- find_complete_snapshot: 模板 None(无维度配置)→ 直接复用最新快照
- find_complete_snapshot: 查库异常 → (None, None)(不阻塞主流程)
- recompute: 无完整快照 → needs_live(由 ai.py 走实时 27b)
- recompute: 命中完整快照 → snapshot_reuse + extract_section 秒回
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai_recompute_service import find_complete_snapshot, recompute


def _mk_row(rid: int, answer_md: str, template_id: int = 10) -> dict:
    """真实 dict 行(_row_to_report 按 setattr 建 ORM 对象; MagicMock 迭代为空 dict 不可用)。"""
    return {
        "id": rid,
        "template_id": template_id,
        "answer_md": answer_md,
        "llm_model": "pregen:test",
    }


def _mk_tmpl(dims):
    t = MagicMock()
    t.id = 10
    t.code = "test_template"
    t.required_dimensions = dims
    return t


_WIN = {"period": "daily", "date": "2026-09-18", "start": None, "end": None}


def _db_returning(rows):
    db = MagicMock()
    db.execute.return_value.mappings.return_value.all.return_value = rows
    return db


# ── find_complete_snapshot ──────────────────────────────
def test_find_none_when_no_complete_snapshot():
    """全部快照维度缺失 → (None, None)(交由实时路径)。"""
    db = _db_returning([
        _mk_row(1, "## 退款分析\n四路: 未提供", template_id=10),
        _mk_row(2, "## 退款分析\n四路: 未提供", template_id=10),
    ])
    tmpl = _mk_tmpl(["四路"])
    with patch("app.services.ai_recompute_service._get_template",
               return_value=tmpl):
        report, t = find_complete_snapshot(db, _WIN, tmpl)
    assert report is None
    assert t is None


def test_find_skips_incomplete_then_hits_complete():
    """第一份缺维度 → 跳过; 第二份完整 → 返回(report, template)。"""
    db = _db_returning([
        _mk_row(1, "## 退款分析\n四路: 未提供", template_id=10),
        _mk_row(2, "## 退款分析\n四路: A 线上直退 23 笔", template_id=10),
    ])
    tmpl = _mk_tmpl(["四路"])
    with patch("app.services.ai_recompute_service._get_template",
               return_value=tmpl):
        report, t = find_complete_snapshot(db, _WIN, tmpl)
    assert report is not None
    assert report.id == 2
    assert t is tmpl


def test_find_returns_latest_when_no_template():
    """模板 None(无维度配置)→ 不做校验, 直接复用最新快照。"""
    db = _db_returning([_mk_row(7, "任何内容", template_id=None)])
    report, t = find_complete_snapshot(db, _WIN, None)
    assert report is not None
    assert report.id == 7
    assert t is None


def test_find_db_error_safe():
    """查库异常 → (None, None)(不阻塞主流程)。"""
    db = MagicMock()
    db.execute.side_effect = Exception("db down")
    report, t = find_complete_snapshot(db, _WIN, _mk_tmpl(["四路"]))
    assert report is None
    assert t is None


# ── recompute(内部调用 extract_section, 是 async) ────────
def _run(coro):
    return asyncio.run(coro)


def test_recompute_needs_live_when_no_snapshot():
    """无完整快照 → source=needs_live(由 ai.py 走 _runner 实时 27b)。"""
    db = _db_returning([])
    out = _run(recompute(db, "四路明细", _WIN, _mk_tmpl(["四路"])))
    assert out["source"] == "needs_live"
    assert out["result_md"] == ""
    assert out["report_id"] is None


def test_recompute_snapshot_reuse():
    """命中完整快照 → snapshot_reuse + extract_section 结果。"""
    db = _db_returning([
        _mk_row(2, "## 退款分析\n四路: A 线上直退 23 笔", template_id=10),
    ])
    tmpl = _mk_tmpl(["四路"])
    with patch("app.services.ai_recompute_service._get_template",
               return_value=tmpl), \
         patch("app.services.ai_recompute_service.extract_section",
               AsyncMock(return_value={"result_md": "四路: A 线上直退 23 笔",
                                       "confidence": 0.9})):
        out = _run(recompute(db, "四路明细", _WIN, tmpl))
    assert out["source"] == "snapshot_reuse"
    assert out["report_id"] == 2
    assert "四路" in out["result_md"]

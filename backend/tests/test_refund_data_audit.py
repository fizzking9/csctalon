"""#196 退款数据审计验收测试：冻结语料上钉死计数。

数据来自 2026-09-25 冻结快照：
- workdata/refund_xlsx_rows.pkl（金山文档导出解析缓存）
- workdata/frozen-manual-refunds-20260925.csv（manual_refunds 全表）

计数口径由 workdata/refund_evidence.py 独立计算锁死。
"""
import csv
import pickle
from pathlib import Path

import pytest

from app.services.refund_data_audit import (
    audit_refund_data,
    norm_sn,
    parse_amount,
    parse_doc_datetime,
)

WD = Path(__file__).resolve().parents[2] / "workdata"
XLSX_PKL = WD / "refund_xlsx_rows.pkl"
DB_CSV = WD / "frozen-manual-refunds-20260925.csv"

pytestmark = pytest.mark.skipif(
    not (XLSX_PKL.exists() and DB_CSV.exists()),
    reason="冻结语料不在本机（仅治理环境可跑）",
)


@pytest.fixture(scope="module")
def report():
    with XLSX_PKL.open("rb") as f:
        doc_rows = pickle.load(f)
    with DB_CSV.open(newline="", encoding="utf-8") as f:
        db_rows = list(csv.DictReader(f))
    return audit_refund_data(doc_rows, db_rows)


# ── 单元级 ────────────────────────────────────────────────────────────────

def test_norm_sn():
    assert norm_sn("328810760.0") == "328810760"
    assert norm_sn("'70032617036773") == "70032617036773"
    assert norm_sn(" 67492609647576 ") == "67492609647576"
    assert norm_sn("123.0456") == "123.0456"  # 非 .0 尾巴不动
    assert norm_sn("") == ""


def test_parse_amount():
    assert parse_amount("27.90") == 27.90
    assert parse_amount("￥1,102.20") == 1102.20
    assert parse_amount("-5") == -5.0
    assert parse_amount("") is None
    assert parse_amount("abc") is None


def test_parse_doc_datetime():
    assert parse_doc_datetime("2026/9/25 14:22") is not None
    assert parse_doc_datetime("2026-09-25") is not None
    assert parse_doc_datetime("2026年9月25日") is not None
    assert parse_doc_datetime("") is None
    assert parse_doc_datetime("26/0920") is None  # 不支持形态 → None


# ── 冻结语料验收（与 refund_evidence.json 锁死值一致）──────────────────────

def test_doc_side_counts(report):
    doc = report["doc"]
    assert doc["total_rows"] == 5031
    assert doc["empty_rows"] == 18
    assert doc["date_unparseable"] == 29
    assert doc["negative_amount_rows"] == 0
    assert doc["date_jumps_gt7d"] == 5
    # 4 处成对 = 行序颠倒；剩 1 处为表头首行 07-01→07-17 真跳变（留给人工看样例）
    assert doc["date_jump_row_swaps"] == 4


def test_db_side_counts(report):
    db = report["db"]
    assert db["total_rows"] == 9589
    assert db["known_governed_rows"] == 5304  # 全表打标行（其中 5281 行落在 db_only）
    assert db["weird_year_rows"] == 2058
    assert db["negative_amount_rows"] == 0


def test_reconcile_counts(report):
    r = report["reconcile"]
    assert r["matched"] == 4213
    assert r["doc_only_rows"] == 800
    assert r["db_only_rows"] == 5376


def test_doc_internal_duplicates(report):
    doc = report["doc"]
    # 文档内同键重复 768 份，其中 757 份是两个明细 sheet 重叠（库去重键正确去重）
    assert doc["internal_duplicate_extra"] == 768
    assert doc["cross_sheet_duplicate_extra"] == 757


def test_true_drift(report):
    r = report["reconcile"]
    # 真丢行：文档有而库里 (订单号,SN) 完全没有 —— 仅 5 行（含空日期/空 SN）
    assert r["truly_missing_in_db"] == 5
    # 库侧无法解释的多余（未打标且文档无此单）：25 行，含 TEST 行/脏行/新增行
    assert r["untagged_extra_in_db"] == 25


def test_verdict(report):
    v = report["verdict"]
    assert v["negative_amount"] == "pass"
    assert v["date_jump"] == "warn"  # 1 处非成对跳变（表头首行），人工复核样例
    assert v["collection_drift"] == "warn"  # 存在键漂移与治理残留

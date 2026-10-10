# -*- coding: utf-8 -*-
"""complaint_date 回填工具测试 (#42, 2026-09-10)

覆盖 scripts/backfill_complaint_date.py 的**纯函数**(编号日期提取 / 真值合理性 /
时区比较口径), 不触碰数据库。

背景: 2026-09-05 两批 workbuddy 推送落库时「投诉日期」被转置
(库内年 = 2000+真值日, 库内日 = 真值年−2001, 月不变), 共 2532 行整批可疑。
成因不在本仓库代码(_to_datetime 对源表 663 个真实日期解析 661 个正确),
本工具做数据修复, 真值取 complaint_no 内嵌日期(主键) + 源表(手机+问题) 反查(次键)。
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_SCRIPT = _BACKEND.parent / "scripts" / "backfill_complaint_date.py"

if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# ⚠️ CI 只挂载 backend/ 目录(见 .gitea/workflows/test.yml 的 `-v .../backend:/app`),
#    仓库根的 scripts/ 在容器内不存在 → 不能在收集期直接 exec_module(会 FileNotFoundError
#    导致整个测试会话中断)。故改为"可选加载": 脚本不在时跳过本文件的用例。
if not _SCRIPT.exists():
    pytest.skip(
        f"回填脚本不在当前环境(CI 仅挂载 backend/): {_SCRIPT}",
        allow_module_level=True,
    )

_spec = importlib.util.spec_from_file_location("backfill_complaint_date", _SCRIPT)
bf = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(bf)

_TZ_CN = timezone(timedelta(hours=8))
_UTC = timezone.utc


# ---- 编号内嵌日期提取 ----
class TestExtractNoDate:
    def test_real_sample_from_db(self):
        """真实样本: 库内 2001-03-24 的行, 编号内嵌真值为 2025-03-01

        (源表交叉验证: 该行手机号 19165958605 的源表投诉日期 = 2025-03-01)
        """
        assert bf._extract_no_date("1440306002025030183693297") == "2025-03-01"

    def test_strips_newline_prefix(self):
        """编号可能带 \\n 前缀(上游导出常见), 必须清洗后再匹配"""
        assert bf._extract_no_date("\\n1440306002025030183693297") == "2025-03-01"

    def test_other_real_samples(self):
        for no, exp in (
            ("1440306002025050342362132", "2025-05-03"),
            ("HJ202505231348560001", "2025-05-23"),
            ("1440306002026082794666450", "2026-08-27"),
        ):
            assert bf._extract_no_date(no) == exp, no

    def test_no_date_returns_none(self):
        for bad in (None, "", "abc", "1440306002025130183693297"):  # 13 月非法
            assert bf._extract_no_date(bad) is None, bad

    def test_implausible_year_rejected(self):
        """窗口外(未来/过古)的编号日期不可信, 应弃用而非回填"""
        assert bf._extract_no_date("1440306002099030183693297") is None   # 2099 未来
        assert bf._extract_no_date("1440306002015030183693297") is None   # 2015 过古

    def test_uses_last_match(self):
        """前缀含干扰数字时取最后一个匹配(编号主体在尾部)"""
        # 前缀 20240101 是干扰, 真正的日期在末尾
        assert bf._extract_no_date("202401011440306002025030183693297") == "2025-03-01"


# ---- 真值合理性窗口 ----
class TestPlausibleTruth:
    def test_source_range_ok(self):
        for d in ("2023-09-01", "2024-06-15", "2025-03-01", "2026-08-27"):
            assert bf._is_plausible_truth(d), d

    def test_year_typo_rejected(self):
        """★源表本身含年份笔误(2225-03-28), 不得直接采信写回库内

        这类值应由 upsert._sanitize_date 做 2xxx→20xx 纠错, 不在回填层处理 ——
        否则会把库内写成 2225(我首版预览就出现过 3 行 2225)。
        """
        assert not bf._is_plausible_truth("2225-03-28")
        assert not bf._is_plausible_truth("2225-04-12")

    def test_too_old_rejected(self):
        for d in ("2001-03-24", "2019-12-31", "2022-12-31"):
            assert not bf._is_plausible_truth(d), d

    def test_future_rejected(self):
        future = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        assert not bf._is_plausible_truth(future)


# ---- 时区比较口径 ----
class TestLocalDateCompare:
    def test_utc_stored_reads_as_next_cn_day(self):
        """★关键口径: 库内按 UTC 存, 必须转北京时间再比日期

        naive 北京时间 2025-03-01 00:00 入库后是 UTC 2025-02-28 16:00,
        若直接用 UTC 取日期会得到 2025-02-28 → 误判"差一天"。
        """
        stored = datetime(2025, 2, 28, 16, 0, tzinfo=_UTC)
        assert bf._to_local_date(stored) == "2025-03-01"

    def test_matches_source_date(self):
        """与源表真值比对必须一致(端到端口径)"""
        for cn_date in ("2023-09-03", "2025-03-01", "2026-08-27"):
            dt = datetime.strptime(cn_date, "%Y-%m-%d").replace(tzinfo=_TZ_CN)
            assert bf._to_local_date(dt.astimezone(_UTC)) == cn_date

    def test_none_passthrough(self):
        assert bf._to_local_date(None) is None

    def test_naive_treated_as_utc(self):
        """naive 值按 UTC 处理(库中 timestamptz 读出一般为 aware, 此处为防御)"""
        assert bf._to_local_date(datetime(2025, 2, 28, 16, 0)) == "2025-03-01"

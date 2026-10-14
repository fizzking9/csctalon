#!/usr/bin/env python3
"""C2 闸门: analytics 层禁止读 ods 源表 (#319 契约 C2, P2 落地)

背景: #319 将 21 张信源表移入 `ods` schema, 并加了连接级 search_path=public,ods
兜底裸 SQL。C2 要求 `services/analytics/**` 不得依赖该兜底直读 ods 源表, 须改读
public 干净层(宽表/字典)。P2 按 refund→complaint→reception 逐域切换。

本检查器用「棘轮(ratchet)」模式落地门禁:
  - 扫描 analytics 目录, 检出三类违规:
      ods_prefix  裸 SQL/文本出现 `ods.` 前缀表名
      sql_table   裸 SQL 以 FROM/JOIN/UPDATE/... 直读 21 张 ods 表之一(非限定名)
      orm_model   import 了 schema=ods 的 ORM 模型类
  - 与基线文件 `scripts/analytics_ods_isolation_baseline.txt` 比对:
      新增违规 → 退出码 1(阻断 CI); 减少违规 → 提示可收敛基线(不阻断)
  - 基线随 P2 逐域切换逐步收敛, 最终清空即 C2 完全生效。

用法:
    python3 scripts/check_analytics_ods_isolation.py            # 校验(CI 用)
    python3 scripts/check_analytics_ods_isolation.py --update-baseline  # 收敛后重生成基线
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "backend" / "app" / "models"
ANALYTICS_DIR = ROOT / "backend" / "app" / "services" / "analytics"
BASELINE_PATH = Path(__file__).resolve().parent / "analytics_ods_isolation_baseline.txt"

# 21 张信源表(权威来源: alembic e7f8a9b0c1d2_issue319_move_source_tables_to_ods.py)
ODS_TABLES: tuple[str, ...] = (
    "bms_orders", "device_combos", "refunds", "manual_refunds", "merchant_refunds",
    "shanmao_refunds", "refund_record_reports", "card_complaints", "external_complaints",
    "cs_records", "scrm_sessions", "scrm_session_transfers", "scrm_operators",
    "scrm_staff_profiles", "wallet_withdrawals", "device_flow_usage", "return_trackings",
    "wechat_registrations", "wechat_accounts", "dept_info_syncs", "attendance",
)

_SQL_READ = re.compile(
    r"(?i)\b(?:FROM|JOIN|UPDATE|INSERT\s+INTO|DELETE\s+FROM|TABLE)\s+(?:ods\.)?("
    + "|".join(re.escape(t) for t in ODS_TABLES)
    + r")\b"
)
_ODS_PREFIX = re.compile(r"\bods\.[a-z_]+")


def load_ods_model_classes() -> dict[str, str]:
    """解析 models/*.py, 返回 {类名: 表名} —— 仅含 schema=ods 的类。

    逐行顺序解析: `class X` 开启新类, `__tablename__=...` 与 `"schema": "ods"`
    归属当前类(模型的列定义在类体内、下一个 class 之前)。
    """
    classes: dict[str, str] = {}
    tablenames: dict[str, str] = {}
    for f in sorted(MODELS_DIR.glob("*.py")):
        cur: str | None = None
        for line in f.read_text(encoding="utf-8").splitlines():
            mc = re.match(r"\s*class\s+(\w+)\s*[\(:]", line)
            if mc:
                cur = mc.group(1)
            mt = re.search(r"__tablename__\s*=\s*[\"']([^\"']+)[\"']", line)
            if mt and cur:
                tablenames[cur] = mt.group(1)
            if re.search(r"[\"']schema[\"']\s*:\s*[\"']ods[\"']", line) and cur:
                classes[cur] = tablenames.get(cur, cur)
    return classes


def scan() -> set[str]:
    """返回违规签名集合, 形如 `<relpath>|<kind>|<token>`(不含行号, 稳于编辑)。"""
    ods_classes = load_ods_model_classes()
    violations: set[str] = set()

    for f in sorted(ANALYTICS_DIR.rglob("*.py")):
        rel = f.relative_to(ROOT).as_posix()
        text = f.read_text(encoding="utf-8")

        # 1) 显式 ods. 前缀
        for m in _ODS_PREFIX.finditer(text):
            violations.add(f"{rel}|ods_prefix|{m.group(0)}")

        # 2) 裸 SQL 直读 ods 表
        for m in _SQL_READ.finditer(text):
            violations.add(f"{rel}|sql_table|{m.group(1).lower()}")

        # 3) import / 使用 schema=ods 的 ORM 模型类
        for cn in ods_classes:
            if re.search(rf"\b{re.escape(cn)}\b", text):
                violations.add(f"{rel}|orm_model|{cn}")

    return violations


def read_baseline() -> set[str]:
    if not BASELINE_PATH.exists():
        return set()
    return {
        ln.strip()
        for ln in BASELINE_PATH.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update-baseline", action="store_true", help="按当前扫描结果重写基线")
    args = ap.parse_args()

    current = scan()

    if args.update_baseline:
        BASELINE_PATH.write_text(
            "# C2 棘轮基线: analytics 读 ods 源表的存量违规 (#319 C2 / P2 逐域收敛)\n"
            "# 每行一签: <文件>|<类型>|<token>；P2 每切换一个域, 该域条目应减少。\n"
            + "\n".join(sorted(current)) + "\n",
            encoding="utf-8",
        )
        print(f"基线已更新: {len(current)} 条 → {BASELINE_PATH.relative_to(ROOT)}")
        return 0

    baseline = read_baseline()
    new = sorted(current - baseline)
    gone = sorted(baseline - current)

    print(f"C2 analytics↔ods 隔离: 当前违规 {len(current)} · 基线 {len(baseline)}")
    if gone:
        print(f"✅ 已收敛 {len(gone)} 条(可运行 --update-baseline 收缩基线):")
        for g in gone:
            print(f"   - {g}")
    if new:
        print(f"❌ 新增 {len(new)} 条违规(analytics 不得直读 ods 源表, 见 #319 C2):")
        for n in new:
            print(f"   + {n}")
        print("   修复: 改读 public 干净层, 或若属既有面请并入基线(须在 PR 中说明)。")
        return 1
    print("✅ 无新增违规, 门禁通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

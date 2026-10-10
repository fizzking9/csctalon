#!/usr/bin/env python3
"""#64 历史脏 SN 清洗（一次性脚本，补齐「代码已修但历史数据仍在」的缺口）。

## 背景

工单 #64 发现 `_clean_sn()` 漏处理两种源文档脏 SN 形态：

| 形态 | 示例 | 根因 |
|---|---|---|
| **前导引号** | `'70032611013040` | Excel 把长数字当文本时自动加 `'`，导出后残留 |
| **一格两 SN** | `58812351022558/63272421032891` | 原分隔符正则漏 `/` 与 `|` |

**代码已修**（`complaint_sync._clean_sn` 已补 `/`、`|` 分隔符 + 引号剥离），
但**历史数据未清洗** → 这些行仍以脏 SN 存库，**永不匹配工单**
→ 产生虚假的「文档有、工单无」空悬。

本脚本用**同一个 `_clean_sn()`**（单一事实来源）清洗历史行，保证与入库侧口径一致。

## 安全性设计

1. **默认只预览**（`--dry-run`），需显式 `--apply` 才写库。
2. **只清洗确定的脏值**：正则 `^['"]|/` 命中的行（不扫描全表，避免误伤）。
3. **跳过已打标行**：`quality_status='dup_sn_backfill'`（残影）不动 ——
   其 SN 本就应缺（#62 要求），回填会把它「洗白」。
4. **幂等**：清洗后再跑，命中 0 行。
5. **保留原值到 `quality_note`**，可溯源回滚。
6. **写库前二次确认库名**（防连错库，参照 conftest 的守卫思路）。

## 用法

    # 本地/生产容器内（推荐 docker exec -w /app）
    python scripts/fix64_dirty_sn.py                 # 预览
    python scripts/fix64_dirty_sn.py --apply         # 写库

⚠️ 本脚本**只改 external_complaints.device_sn**，不碰工单表（BMS 侧只读）。
"""

from __future__ import annotations

import argparse
import re
import sys

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.external_complaint import ExternalComplaint
from app.services.complaint_sync import _clean_sn

# 命中「确定的脏 SN」：前导引号 或 含斜杠（一格多 SN）
DIRTY_RE = re.compile(r"^['\"]|/")

# 残影行不动（#62：残影 SN 本就应缺，回填会破坏其语义）
SKIP_QSTATUS = ("dup_sn_backfill",)


def _scan(db: Session) -> list[tuple[int, str, str]]:
    """返回 [(id, 原SN, 清洗后SN)]，仅含「确定脏」且清洗后确有变化且可用的行。"""
    rows = db.execute(
        select(ExternalComplaint.id, ExternalComplaint.device_sn, ExternalComplaint.quality_status)
        .where(ExternalComplaint.device_sn.isnot(None))
        .where(ExternalComplaint.device_sn != "")
        .where(text("device_sn ~ :pat").bindparams(pat=r"^['\"]|/"))
    ).all()

    out: list[tuple[int, str, str]] = []
    for rid, raw, qs in rows:
        if qs in SKIP_QSTATUS:
            continue  # 残影：保持空缺（#62 语义）
        fixed = _clean_sn(raw)
        if fixed and fixed != raw:
            out.append((int(rid), str(raw), fixed))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写库（默认仅预览）")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        # ★ 库名守卫（参照 tests/conftest 思路）：打印实际连接的库，供人工确认
        dbname = db.execute(text("SELECT current_database()")).scalar()
        print(f"连接库: {dbname}")

        targets = _scan(db)
        print(f"\n=== #64 历史脏 SN 清洗 [{'写库' if args.apply else '预览'}] ===")
        print(f"命中 {len(targets)} 行\n")
        for rid, raw, fixed in targets:
            print(f"  id={rid:<8} {raw!r:<38} → {fixed!r}")

        if not targets:
            print("  (无待清洗行 —— 幂等确认)")
            return 0

        # 顺带报告：清洗后能否匹配到工单（判断这些行是否真的被漏匹配）
        print("\n--- 清洗后与工单的匹配情况 ---")
        for rid, raw, fixed in targets:
            n = db.execute(
                text("SELECT count(*) FROM service_tickets WHERE device_sn = :s"),
                {"s": fixed},
            ).scalar()
            print(f"  id={rid:<8} {fixed:<20} → 工单命中 {n}")

        if not args.apply:
            print("\n→ 确认无误后加 --apply 执行")
            return 0

        for rid, raw, fixed in targets:
            obj = db.get(ExternalComplaint, rid)
            if obj is None:
                continue
            note = (obj.quality_note or "")
            obj.quality_note = f"{note} | #64 SN清洗: {raw!r} → {fixed!r}".strip(" |")
            obj.device_sn = fixed
        db.commit()
        print(f"\n✔ 已清洗 {len(targets)} 行（原值记入 quality_note，可溯源）")

        # 幂等自检
        remain = _scan(db)
        print(f"✔ 幂等自检: 剩余待清洗 {len(remain)} 行")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

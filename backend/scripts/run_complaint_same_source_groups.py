#!/usr/bin/env python3
"""外部投诉同源判重 v5 离线打标任务（工单 #208）

规则见 backend/app/services/complaint_same_source.py（同SN + 相似≥0.7 + ≤14天，
内嵌前文降 needs_review）。打标不删 external_complaints 源数据。

用法：
  # 预览（默认，不写库）
  python backend/scripts/run_complaint_same_source_groups.py --mode dev
  # 落库（dev/test）
  python backend/scripts/run_complaint_same_source_groups.py --mode dev --apply
  # 生产（标准发布流程内，显式授权）
  python backend/scripts/run_complaint_same_source_groups.py --mode prod --apply --allow-real-db

幂等：confirmed/rejected 人工组保留；auto/needs_review 解散后全量重建。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 必须先 bootstrap（在 import app 之前完成模式↔库名校验与 sys.path 设置）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._bootstrap import bootstrap, real_db_requested  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="外部投诉同源判重 v5 打标（#208）")
    ap.add_argument("--mode", choices=["dev", "test", "prod"], default="dev")
    ap.add_argument("--apply", action="store_true", help="落库（默认 dry-run 只预览）")
    args = ap.parse_args()

    db = bootstrap(mode=args.mode, allow_real_db=real_db_requested())
    try:
        # 延迟导入：bootstrap 设好环境后 app 才可安全导入
        from app.services.complaint_same_source import rebuild_groups

        persist = bool(args.apply)
        stats = rebuild_groups(db, persist=persist)
        if persist:
            db.commit()
        mode_label = "APPLY 已落库" if persist else "DRY-RUN 未落库"
        print(f"[{mode_label}] 规则版本={stats['rule_version']}")
        print(
            f"扫描行={stats['scanned']} 同SN多行组={stats['sn_multi_groups']} "
            f"命中对={stats['pairs']}"
        )
        print(
            f"同源组={stats['groups_total']} "
            f"(auto={stats['groups_auto']} needs_review={stats['groups_needs_review']}) "
            f"人工组锁定成员={stats['locked_members_kept']}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

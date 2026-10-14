# -*- coding: utf-8 -*-
"""#321: 把 dim_device_combo 当前切片投影回 devices.current_combo_name

用法:
    python -m scripts.backfill_current_combo_from_dim --mode dev          # 开发库(只补空)
    python -m scripts.backfill_current_combo_from_dim --mode prod --allow-real-db
    python -m scripts.backfill_current_combo_from_dim --mode prod --allow-real-db --overwrite

默认**只补空**(仅填 current 为空/空串的设备, 保留 BMS 现值);
`--overwrite` 则以 dim 当前切片覆盖(仅更新确有差异的行)。

属于批量 UPDATE, 真实库须显式 --allow-real-db(经 scripts._bootstrap 护栏)。
"""

from __future__ import annotations

import sys

from scripts._bootstrap import bootstrap


def main() -> None:
    args = sys.argv[1:]
    mode = "dev"
    if "--mode" in args:
        mode = args[args.index("--mode") + 1]
    allow_real_db = "--allow-real-db" in args
    overwrite = "--overwrite" in args

    db = bootstrap(mode=mode, allow_real_db=allow_real_db)
    try:
        from app.services.dim_device_combo_service import refresh_current_combo_from_dim
        res = refresh_current_combo_from_dim(db, overwrite=overwrite)
        print(f"✅ current_combo_name 投影回写完成(overwrite={res['overwrite']}): {res['updated']} 行")
    finally:
        db.close()


if __name__ == "__main__":
    main()

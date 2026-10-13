# -*- coding: utf-8 -*-
"""#320 P1: 全量重建 dim_device_combo 干净维度 (SCD Type 2)

用法:
    python -m scripts.backfill_dim_device_combo --mode dev        # 开发库
    python -m scripts.backfill_dim_device_combo --mode prod --allow-real-db  # 生产库

幂等: 每次 TRUNCATE 后全量重建。
"""

from __future__ import annotations

import sys

from scripts._bootstrap import bootstrap


def main() -> None:
    args = sys.argv[1:]
    mode = "dev"
    allow_real_db = False
    if "--mode" in args:
        mode = args[args.index("--mode") + 1]
    allow_real_db = "--allow-real-db" in args

    db = bootstrap(mode=mode, allow_real_db=allow_real_db)
    try:
        from app.services.dim_device_combo_service import rebuild_dim_device_combo
        result = rebuild_dim_device_combo(db)
        print(f"✅ dim_device_combo 重建完成: {result['rows']} 行 / {result['devices']} 台设备")
    finally:
        db.close()


if __name__ == "__main__":
    main()

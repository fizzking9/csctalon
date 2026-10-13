# -*- coding: utf-8 -*-
"""#320 对账脚本: dim_device_combo 时点套餐 vs 现口径, 验证零差异(或可解释差异)

对退款/投诉两侧分别抽样, 对比:
  - 退款侧: 新口径(dim 时点套餐 → refunds.package_name → 当前套餐) vs 旧口径(refunds.package_name → 当前套餐)
  - 投诉侧: 新口径(dim 时点套餐 → 当前套餐) vs 旧口径(当前套餐)

用法:
    python -m scripts.diff_dim_combo --mode prod --allow-real-db
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from scripts._bootstrap import bootstrap

_REFUND_DIFF_SQL = text("""
WITH refund_rows AS (
    SELECT r.device_sn AS sn,
           COALESCE(r.actual_refund_time, r.updated_at_raw, r.created_at_raw) AS ev,
           COALESCE(r.package_name, d.current_combo_name) AS old_pkg
    FROM refunds r
    LEFT JOIN devices d ON d.sn = r.device_sn
    WHERE r.refund_method = '线上' AND r.status = '已退款'
      AND COALESCE(r.actual_refund_time, r.updated_at_raw, r.created_at_raw) >= NOW() - INTERVAL '90 days'
    LIMIT 5000
),
new_pkg AS (
    SELECT sn, ev, (
        SELECT package_name FROM dim_device_combo dc
        WHERE dc.device_sn = rr.sn AND dc.valid_from <= rr.ev
          AND (dc.valid_to IS NULL OR dc.valid_to > rr.ev)
        LIMIT 1
    ) AS dim_pkg
    FROM refund_rows rr
)
SELECT
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE COALESCE(np.dim_pkg, rr.old_pkg) = rr.old_pkg) AS match_cnt,
    COUNT(*) FILTER (WHERE COALESCE(np.dim_pkg, rr.old_pkg) IS DISTINCT FROM rr.old_pkg) AS diff_cnt
FROM refund_rows rr
LEFT JOIN new_pkg np ON np.sn = rr.sn AND np.ev = rr.ev
""")

_COMPLAINT_DIFF_SQL = text("""
WITH complaint_rows AS (
    SELECT t.device_sn AS sn, t.register_time AS ev,
           d.current_combo_name AS old_pkg
    FROM service_tickets t
    LEFT JOIN devices d ON d.sn = t.device_sn
    WHERE t.service_category IN ('投诉', '') IS NOT FALSE
      AND t.register_time >= NOW() - INTERVAL '90 days'
    LIMIT 5000
),
new_pkg AS (
    SELECT sn, ev, (
        SELECT package_name FROM dim_device_combo dc
        WHERE dc.device_sn = cr.sn AND dc.valid_from <= cr.ev
          AND (dc.valid_to IS NULL OR dc.valid_to > cr.ev)
        LIMIT 1
    ) AS dim_pkg
    FROM complaint_rows cr
)
SELECT
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE COALESCE(np.dim_pkg, cr.old_pkg) IS NOT DISTINCT FROM cr.old_pkg) AS match_cnt,
    COUNT(*) FILTER (WHERE COALESCE(np.dim_pkg, cr.old_pkg) IS DISTINCT FROM cr.old_pkg) AS diff_cnt
FROM complaint_rows cr
LEFT JOIN new_pkg np ON np.sn = cr.sn AND np.ev = cr.ev
""")


def main() -> None:
    args = sys.argv[1:]
    mode = "dev"
    allow_real_db = False
    if "--mode" in args:
        mode = args[args.index("--mode") + 1]
    allow_real_db = "--allow-real-db" in args

    db = bootstrap(mode=mode, allow_real_db=allow_real_db)
    try:
        db.execute(text("SET search_path TO public, ods"))
        r = db.execute(_REFUND_DIFF_SQL).fetchone()
        print(f"[退款侧] 抽样 {r.total}: 一致 {r.match_cnt}, 差异 {r.diff_cnt}")
        c = db.execute(_COMPLAINT_DIFF_SQL).fetchone()
        print(f"[投诉侧] 抽样 {c.total}: 一致 {c.match_cnt}, 差异 {c.diff_cnt}")
        if (r.diff_cnt or 0) + (c.diff_cnt or 0) == 0:
            print("✅ 两侧零差异, 可切换")
        else:
            print("⚠️ 存在差异, 需人工核查是否为套餐变更导致的合理时点差异")
    finally:
        db.close()


if __name__ == "__main__":
    main()

"""dim_device_combo 干净维度构建服务 (#320 P1)

单一真身: 把 bms_orders + device_combos 两路套餐事件合并为 SCD Type 2 区间,
供退款/投诉两侧按事件时点 JOIN 取当时套餐。

构建逻辑(纯 SQL, 一次 INSERT ... SELECT):
  1. 合并两路事件, 过滤「工厂质检」套餐;
  2. 按设备+时间排序, LAG 检测套餐变化, 打 group 标记;
  3. 每组 MIN(time)=valid_from, LEAD(valid_from)=valid_to(末行 NULL);
  4. is_current = (valid_to IS NULL)。

幂等: 先 TRUNCATE 再全量重建(维度表无外键依赖, 秒级重建)。
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# #320: 工厂质检专用套餐过滤(与 _device_dim_attrs 同源口径, 退款/投诉两侧一致)
# 注: 此处为普通字符串(非 f-string), 单 % 即为 SQL LIKE 通配符。

_REBUILD_SQL = text("""
WITH events AS (
    SELECT device_sn, combo_name AS package_name, create_date AS evt_time,
           'bms_order'::text AS source, order_no
    FROM ods.bms_orders
    WHERE device_sn IS NOT NULL AND combo_name IS NOT NULL
      AND combo_name NOT LIKE '%工厂质检%'
    UNION ALL
    SELECT device_sn, package_name, created_at_raw AS evt_time,
           'device_combo'::text AS source, recharge_order_no AS order_no
    FROM ods.device_combos
    WHERE device_sn IS NOT NULL AND package_name IS NOT NULL
      AND package_name NOT LIKE '%工厂质检%'
),
ordered AS (
    SELECT device_sn, package_name, source, order_no, evt_time,
           LAG(package_name) OVER (PARTITION BY device_sn ORDER BY evt_time, source) AS prev_pkg
    FROM events
),
grouped AS (
    SELECT device_sn, package_name, source, order_no, evt_time,
           SUM(CASE WHEN prev_pkg IS DISTINCT FROM package_name THEN 1 ELSE 0 END)
               OVER (PARTITION BY device_sn ORDER BY evt_time, source
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS grp
    FROM ordered
),
intervals AS (
    SELECT device_sn,
           package_name,
           MIN(evt_time) AS valid_from,
           (array_agg(source ORDER BY evt_time, source))[1] AS source,
           (array_agg(order_no ORDER BY evt_time, source))[1] AS order_no
    FROM grouped
    GROUP BY device_sn, package_name, grp
),
with_next AS (
    SELECT device_sn, package_name, source, order_no, valid_from,
           LEAD(valid_from) OVER (PARTITION BY device_sn ORDER BY valid_from) AS valid_to
    FROM intervals
)
INSERT INTO dim_device_combo (device_sn, package_name, source, order_no, valid_from, valid_to, is_current)
SELECT device_sn, package_name, source, order_no, valid_from, valid_to,
       (valid_to IS NULL) AS is_current
FROM with_next
""")


def rebuild_dim_device_combo(db: Session) -> dict:
    """全量重建 dim_device_combo(TRUNCATE + INSERT)。

    返回: {"rows": 插入行数, "devices": 覆盖设备数}
    """
    # 维度表无外键依赖, TRUNCATE 安全且快
    db.execute(text("TRUNCATE TABLE dim_device_combo RESTART IDENTITY"))
    result = db.execute(_REBUILD_SQL)
    db.commit()
    inserted = result.rowcount
    # 统计覆盖设备数
    dev_cnt = db.execute(text("SELECT COUNT(DISTINCT device_sn) FROM dim_device_combo")).scalar() or 0
    logger.info(f"[dim_device_combo] 重建完成: {inserted} 行 / {dev_cnt} 台设备")
    return {"rows": inserted, "devices": dev_cnt}

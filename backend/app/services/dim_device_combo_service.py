"""dim_device_combo 干净维度构建服务 (#320 P1)

单一真身: 把 bms_orders + device_combos 两路套餐事件合并为 SCD Type 2 区间,
供退款/投诉两侧按事件时点 JOIN 取当时套餐。

#338: 源数据改读 std_bms_order_all / std_device_combo_all 镜像视图
(与 ods 同构透传, 业务/派生作业零直读 ods)。

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
    FROM std_bms_order_all
    WHERE device_sn IS NOT NULL AND combo_name IS NOT NULL
      AND create_date IS NOT NULL
      AND combo_name NOT LIKE '%工厂质检%'
    UNION ALL
    SELECT device_sn, package_name, created_at_raw AS evt_time,
           'device_combo'::text AS source, recharge_order_no AS order_no
    FROM std_device_combo_all
    WHERE device_sn IS NOT NULL AND package_name IS NOT NULL
      AND created_at_raw IS NOT NULL
      AND package_name NOT LIKE '%工厂质检%'
),
events_dedup AS (
    -- 同一设备同一时间点两路事件可能同时存在(bms_order + device_combo 同 timestamp),
    -- 需去重到唯一 package, 否则 intervals 会产生相同 (device_sn, valid_from) 撞唯一键.
    -- 优先级: bms_order(订单记录, 更权威) > device_combo.
    SELECT device_sn, package_name, source, order_no, evt_time
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (PARTITION BY device_sn, evt_time
                                  ORDER BY CASE source WHEN 'bms_order' THEN 0 ELSE 1 END) AS rn
        FROM events
    ) t
    WHERE rn = 1
),
ordered AS (
    SELECT device_sn, package_name, source, order_no, evt_time,
           LAG(package_name) OVER (PARTITION BY device_sn ORDER BY evt_time, source) AS prev_pkg
    FROM events_dedup
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


# ── #321: 把 dim 当前切片投影回 devices.current_combo_name ────────────────
# 背景: devices.current_combo_name 现有三个写入方(CSV 导入覆盖 / 探针只填 / 业务反填只填),
#       语义不一致导致与订单源 37% 对不上。目标: 让 dim 成为"当前套餐"的单一真身之一。
# 生产实测(2026-10-14): dim 当前切片覆盖 1,512,301 台; 有 current 的设备约 174.8 万台,
#   其中 dim 缺 445,415 台(25.5%)、可补空 209,897 台、不一致 198,924 台。
#   —— dim 并不能完整权威"current", 故**默认只补空**(不覆盖), 避免丢失 BMS 独有套餐。

_DIM_CURRENT_SUBQ = """
    SELECT DISTINCT ON (device_sn) device_sn, package_name
    FROM dim_device_combo
    WHERE is_current
    ORDER BY device_sn
"""

# 只补空: 仅当 current 为空/空串时填入 dim 值(不改动已有值, 无回归)
_SQL_FILL_CURRENT = text("""
UPDATE devices d
SET current_combo_name = x.package_name, updated_at = now()
FROM (""" + _DIM_CURRENT_SUBQ + """) x
WHERE d.sn = x.device_sn
  AND (d.current_combo_name IS NULL OR d.current_combo_name = '')
""")

# 覆盖: 以 dim 当前切片覆盖(仅更新确有差异的行, 幂等)
_SQL_OVERWRITE_CURRENT = text("""
UPDATE devices d
SET current_combo_name = x.package_name, updated_at = now()
FROM (""" + _DIM_CURRENT_SUBQ + """) x
WHERE d.sn = x.device_sn
  AND d.current_combo_name IS DISTINCT FROM x.package_name
""")


def refresh_current_combo_from_dim(db: Session, *, overwrite: bool = False) -> dict:
    """把 dim_device_combo 当前切片投影回 devices.current_combo_name (#321)。

    overwrite=False(默认, 推荐): **只补空** —— 保留 BMS 现值, 仅填 dim 有值且 current 为空
        的设备(生产实测约 209,897 台), 无回归。
    overwrite=True: 以 dim 覆盖 —— 生产实测约 198,924 台改值; dim 缺失者(44.5 万台)不触及,
        保持原值。用于"分析口径与订单源对齐"目标。

    幂等(覆盖模式仅更新确有差异的行); 属批量 UPDATE, 调用方须已过 db_guard 守卫。
    返回: {"updated": 影响行数, "overwrite": bool}
    """
    sql = _SQL_OVERWRITE_CURRENT if overwrite else _SQL_FILL_CURRENT
    result = db.execute(sql)
    db.commit()
    updated = result.rowcount
    logger.info(f"[dim_device_combo] current_combo_name 投影回写(overwrite={overwrite}): {updated} 行")
    return {"updated": updated, "overwrite": overwrite}

# -*- coding: utf-8 -*-
"""E 路企业商户维表 dim_device_merchant 全量重建作业 (#332 T5)

权威口径: docs/standards/退款五路数据标准与采纳规则.md

构建逻辑(纯 SQL 一次 INSERT…SELECT, 范式同 dim_device_combo_service):
  1. base: E 路 std_refund_record_all 镜像(#338, 与 ods.refund_record_reports
     同构透传)取 (device_sn, merchant_id,
     merchant_name, refund_time) 去重; 过滤空 SN / 空 refund_time / 假 SN
     (全重复位正则 ^(\\d)\\1+$, 如 00000000000000 —— 普查 2026-10-16 确认);
  2. enriched: LEFT JOIN dim_merchant_group 取集团名, 无人工映射 = merchant_name;
  3. events: 同 (device_sn, refund_time) 多商户事件去重(取 merchant_id 最小者,
     防 UQ(device_sn, valid_from) 撞键; 该冲突仍由下方 overlap 检测捕获);
  4. LAG 检测商户变化分段: valid_from=段首退款时间, valid_to=下段 valid_from
     (末段 NULL) —— 分段按构造互不重迭且连续覆盖 [首事件, ∞);
  5. overlap 标记(普查口径): 同 SN 任意两商户的 [min(t), max(t)] 时间跨度
     **严格相交**(s1.t0 < s2.t1 AND s2.t0 < s1.t1) → 两商户全部分段标
     conflict_flag='overlap'; 仅时间相接不算;
  6. 仲裁(tasks.md T5 二选一决策: 选维表加可仲裁标记列): 对 overlap SN,
     只读 JOIN devices(sn, merchant_name), 档案名精确命中某商户名优先,
     其次命中其集团名, 每 SN 至多 1 家 → 该商户全部分段 arbiter_pick=TRUE;
     无法仲裁则全 FALSE, 消费侧按冲突兜底展示。

幂等: TRUNCATE + 全量重建(无外键依赖), 重跑内容一致。

消费侧 as-of JOIN(按事件时间 t 取当时企业商户)::

    LEFT JOIN LATERAL (
        SELECT dm.merchant_group, dm.merchant_name, dm.merchant_id, dm.conflict_flag
        FROM dim_device_merchant dm
        WHERE dm.device_sn = <sn列>
          AND dm.valid_from <= <t列>
          AND (dm.valid_to IS NULL OR <t列> < dm.valid_to)
        ORDER BY dm.arbiter_pick DESC, dm.valid_from DESC
        LIMIT 1
    ) dm ON TRUE

  分段互不重叠时至多命中 1 行(arbiter_pick 排序为防御性, 对未来真重叠设计兼容);
  t 早于首段则无命中 → 消费侧对 NULL 企业维度兜底展示(维表已知边界)。
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 假 SN 判定(普查确认): 全重复位, 如 00000000000000; 不产出分段, 仅计 stats
_FAKE_SN_REGEX = r"^(\d)\1+$"

# 重建主 SQL(text 常量, 无外部输入参数)
_REBUILD_SQL = text(r"""
WITH base AS (
    SELECT DISTINCT r.device_sn, r.merchant_id, r.merchant_name, r.refund_time
    FROM std_refund_record_all r
    WHERE r.device_sn IS NOT NULL AND btrim(r.device_sn) <> ''
      AND r.refund_time IS NOT NULL
      AND r.device_sn !~ '^(\d)\1+$'
),
enriched AS (
    SELECT b.device_sn, b.merchant_id, b.merchant_name, b.refund_time,
           COALESCE(g.group_name, b.merchant_name) AS merchant_group
    FROM base b
    LEFT JOIN dim_merchant_group g ON g.merchant_id = b.merchant_id
),
events AS (
    -- 同 SN 同时间点多商户去重(防 (device_sn, valid_from) 撞唯一键)
    SELECT device_sn, merchant_id, merchant_name, merchant_group, refund_time
    FROM (
        SELECT e.*,
               ROW_NUMBER() OVER (PARTITION BY device_sn, refund_time
                                  ORDER BY merchant_id NULLS LAST) AS rn
        FROM enriched e
    ) t
    WHERE rn = 1
),
ordered AS (
    SELECT e.*,
           LAG(merchant_id) OVER (PARTITION BY device_sn
                                  ORDER BY refund_time, merchant_id) AS prev_mid
    FROM events e
),
segmented AS (
    SELECT o.*,
           SUM(CASE WHEN prev_mid IS DISTINCT FROM merchant_id THEN 1 ELSE 0 END)
               OVER (PARTITION BY device_sn ORDER BY refund_time, merchant_id
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS seg
    FROM ordered o
),
intervals AS (
    SELECT device_sn, merchant_id, merchant_name, merchant_group,
           MIN(refund_time) AS valid_from
    FROM segmented
    GROUP BY device_sn, merchant_id, merchant_name, merchant_group, seg
),
with_next AS (
    SELECT i.*,
           LEAD(valid_from) OVER (PARTITION BY device_sn ORDER BY valid_from) AS valid_to
    FROM intervals i
),
-- 普查 overlap 口径: 同 SN 两商户时间跨度严格相交(用全量 enriched, 非去重后 events)
spans AS (
    SELECT device_sn, merchant_id,
           MIN(refund_time) AS t0, MAX(refund_time) AS t1
    FROM enriched
    GROUP BY device_sn, merchant_id
),
overlap_ms AS (
    SELECT DISTINCT s1.device_sn, s1.merchant_id
    FROM spans s1
    JOIN spans s2
      ON s2.device_sn = s1.device_sn
     AND s1.merchant_id IS DISTINCT FROM s2.merchant_id
     AND s1.t0 < s2.t1 AND s2.t0 < s1.t1
),
arbiter AS (
    -- 设备档案 merchant_name 仲裁(每 SN 至多 1 家): 精确命中商户名 > 命中集团名
    SELECT device_sn, merchant_id
    FROM (
        SELECT w.device_sn, w.merchant_id,
               ROW_NUMBER() OVER (
                   PARTITION BY w.device_sn
                   ORDER BY CASE WHEN d.merchant_name = w.merchant_name THEN 0 ELSE 1 END,
                            w.merchant_id
               ) AS rn
        FROM (SELECT DISTINCT device_sn, merchant_id, merchant_name, merchant_group
              FROM enriched) w
        JOIN (SELECT DISTINCT device_sn FROM overlap_ms) o ON o.device_sn = w.device_sn
        JOIN devices d ON d.sn = w.device_sn
        WHERE d.merchant_name IS NOT NULL
          AND (d.merchant_name = w.merchant_name OR d.merchant_name = w.merchant_group)
    ) picks
    WHERE rn = 1
)
INSERT INTO dim_device_merchant
    (device_sn, merchant_id, merchant_name, merchant_group,
     valid_from, valid_to, conflict_flag, arbiter_pick)
SELECT w.device_sn, w.merchant_id, w.merchant_name, w.merchant_group,
       w.valid_from, w.valid_to,
       CASE WHEN om.merchant_id IS NOT NULL THEN 'overlap' ELSE 'none' END,
       (a.merchant_id IS NOT NULL)
FROM with_next w
LEFT JOIN overlap_ms om
  ON om.device_sn = w.device_sn AND om.merchant_id IS NOT DISTINCT FROM w.merchant_id
LEFT JOIN arbiter a
  ON a.device_sn = w.device_sn AND a.merchant_id IS NOT DISTINCT FROM w.merchant_id
""")


def rebuild_dim_device_merchant(db: Session, *, commit: bool = True) -> dict:
    """全量重建 dim_device_merchant(TRUNCATE + INSERT…SELECT)。

    commit=True(调度/API 入口): 作业末尾统一提交;
    commit=False(单测): 仅 flush, 由外层 fixture 事务回滚隔离。

    返回: {segments, devices, overlap_devices, arbiter_picks, fake_sns, inserted}
    """
    db.execute(text("TRUNCATE TABLE dim_device_merchant RESTART IDENTITY"))
    result = db.execute(_REBUILD_SQL)
    inserted = result.rowcount

    stats = db.execute(text("""
        SELECT count(*) AS segments,
               count(DISTINCT device_sn) AS devices,
               count(DISTINCT device_sn) FILTER (WHERE conflict_flag = 'overlap') AS overlap_devices,
               count(*) FILTER (WHERE arbiter_pick) AS arbiter_picks
        FROM dim_device_merchant
    """)).one()
    fake = db.execute(text(
        "SELECT count(DISTINCT device_sn) FROM std_refund_record_all "
        "WHERE device_sn ~ :re"
    ), {"re": _FAKE_SN_REGEX}).scalar() or 0
    if commit:
        db.commit()
    else:
        db.flush()

    out = {
        "segments": int(stats.segments),
        "devices": int(stats.devices),
        "overlap_devices": int(stats.overlap_devices),
        "arbiter_picks": int(stats.arbiter_picks),
        "fake_sns": int(fake),
        "inserted": int(inserted),
    }
    logger.info(
        "[dim_device_merchant] 重建完成: %d 分段 / %d 设备; overlap 设备 %d, 仲裁命中 %d 段, 假SN %d",
        out["segments"], out["devices"], out["overlap_devices"],
        out["arbiter_picks"], out["fake_sns"],
    )
    return out

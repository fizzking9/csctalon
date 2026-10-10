"""fix_metric_thresholds_and_add_backlog_net

#83: 指标阈值严重错配治理 + 新增「积压净增」指标

## 问题（2026-09-14 生产实测，近90天分布）
3 个 lower_better 指标的阈值比真实常态低 6.6~13.5 倍 → **每天恒报 critical**，
告警机制对它们实际已失效（使用者会学会忽略告警 → 比没有告警更糟）：

| 指标 | 近90天 p50 | 旧阈值 warn/crit | 倍数 |
|---|---|---|---|
| refund_count（旧口径 refunds 单表） | 673 | 30 / 50 | 13.5x |
| internal_complaint_daily | 135 | 8 / 15 | 9.0x |
| ticket_backlog | 330 | 20 / 50 | 6.6x |

## 本迁移做三件事

### 1. 重设 3 个错配阈值 → 异常检测语义（用户 2026-09-14 拍板）
**预警 = p90，告警 = p95** —— 即「显著高于历史常态」才报。

| 指标 | 新 warn / crit | 依据（近90天分布） |
|---|---|---|
| `refund_count` | 400 / 460 | 四路合计 p90=403 / p95=451 |
| `internal_complaint_daily` | 220 / 290 | p90=216 / p95=282 |
| `ticket_backlog` | 420 / 450 | p90=419 / p95=431（当前 585 超历史峰值，报 critical 正确） |

⚠️ `recovery_rate`(30/20/优秀40) 与 `external_complaint_7d`(50/70) **不动** ——
   其阈值经复核合理（前者是业务目标语义，后者与当前值 27 相符）。

### 2. 新增 `ticket_backlog_net`（积压净增，滚动7天）
用户决策：「存量指标看『是否在恶化』比看『绝对水位』有意义」。
阈值 warn=149 / crit=180（依据 p90=149.1 / p95=180.1；当前值 32 → green）。

### 3. 历史快照标注不可比
`refund_count` 口径由「refunds 单表」改为「四路经营合计」（代码层，见
`metric_registry_service._eval_refund_count`），**旧快照数值不可与新值比较**。
本迁移在旧快照 detail 上追加标记，避免回溯分析误读。

Revision ID: b8c9d0e1f2a3
Revises: a1c2e3f4d5b6
Create Date: 2026-09-14 10:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, None] = 'a1c2e3f4d5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NET_CODE = "ticket_backlog_net"

# (metric_code, 新 warn, 新 crit) —— 按 p90/p95 重设
THRESHOLD_FIXES = [
    ("refund_count", 400.0, 460.0),
    ("internal_complaint_daily", 220.0, 290.0),
    ("ticket_backlog", 420.0, 450.0),
]
# 旧阈值（供 downgrade 还原）
THRESHOLD_OLD = {
    "refund_count": (30.0, 50.0),
    "internal_complaint_daily": (8.0, 15.0),
    "ticket_backlog": (20.0, 50.0),
}


def _has_metric(conn, code: str) -> bool:
    return conn.execute(
        sa.text("SELECT count(*) FROM metric_registry WHERE code = :c"), {"c": code}
    ).scalar() > 0


def _has_rule(conn, code: str) -> bool:
    return conn.execute(
        sa.text("SELECT count(*) FROM alert_rules WHERE metric_code = :c"), {"c": code}
    ).scalar() > 0


def _rethreshold(conn, code: str, warn: float, crit: float) -> None:
    """关闭当前生效阈值(留痕 valid_to) + 插入新版本 —— 与
    `PUT /metrics/{code}/thresholds` 的行为一致（不删历史，可回溯调参）"""
    conn.execute(
        sa.text(
            "UPDATE metric_thresholds SET valid_to = now(), updated_at = now() "
            "WHERE metric_code = :c AND valid_to IS NULL"
        ),
        {"c": code},
    )
    conn.execute(
        sa.text(
            "INSERT INTO metric_thresholds "
            "(metric_code, excellent_value, warning_value, critical_value, "
            " valid_from, created_at, updated_at) "
            "VALUES (:c, NULL, :w, :k, now(), now(), now())"
        ),
        {"c": code, "w": warn, "k": crit},
    )


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. 重设 3 个错配阈值 ──
    for code, warn, crit in THRESHOLD_FIXES:
        if _has_metric(conn, code):
            _rethreshold(conn, code, warn, crit)

    # ── 2. 新增「积压净增」指标 + 阈值 + 规则 ──
    if not _has_metric(conn, NET_CODE):
        conn.execute(
            sa.text(
                """
                INSERT INTO metric_registry
                (code, name_cn, direction, period, unit, data_sources, description, enabled, created_at, updated_at)
                VALUES (:c, '投诉积压净增(7天)', 'lower_better', 'day', '个',
                        '["service_tickets"]',
                        '滚动 7 天 新增投诉工单 − 真实结案(finish−register≥3min)。为负=在消化(好); 为正=在累积(坏)。新增于 #83: 存量指标(ticket_backlog)只增不减, 用历史分位定阈值无意义, 故改看增量方向。',
                        true, now(), now())
                """
            ),
            {"c": NET_CODE},
        )
    # 阈值: p90/p95（近90天实测 149.1 / 180.1）
    if not conn.execute(
        sa.text(
            "SELECT count(*) FROM metric_thresholds WHERE metric_code = :c AND valid_to IS NULL"
        ),
        {"c": NET_CODE},
    ).scalar():
        conn.execute(
            sa.text(
                "INSERT INTO metric_thresholds "
                "(metric_code, excellent_value, warning_value, critical_value, "
                " valid_from, created_at, updated_at) "
                "VALUES (:c, -50.0, 149.0, 180.0, now(), now(), now())"
            ),
            {"c": NET_CODE},
        )
    if not _has_rule(conn, NET_CODE):
        conn.execute(
            sa.text(
                """
                INSERT INTO alert_rules
                (name, type, condition, threshold, enabled, metric_code,
                 excellent_value, warning_value, critical_value,
                 channels, recipient_roles, silence_window, created_at, updated_at)
                VALUES ('业务-投诉积压净增(7天)', 'metric', NULL, NULL, true, :c,
                        -50.0, 149.0, 180.0,
                        '["inapp"]', '["admin","supervisor"]', NULL, now(), now())
                """
            ),
            {"c": NET_CODE},
        )

    # ── 3. 同步 alert_rules 的阈值副本（scan 时以 metric_thresholds 为准，
    #        但规则上的副本会展示在预警中心，不同步会显示旧数字）──
    for code, warn, crit in THRESHOLD_FIXES:
        conn.execute(
            sa.text(
                "UPDATE alert_rules SET warning_value = :w, critical_value = :k, updated_at = now() "
                "WHERE metric_code = :c"
            ),
            {"c": code, "w": warn, "k": crit},
        )

    # ── 4. 旧 refund_count 快照标注「不可比」（口径已改，防回溯误读）──
    conn.execute(
        sa.text(
            "UPDATE metric_daily_snapshots "
            "SET detail = COALESCE(detail, '') || ' [⚠️#83 口径已由 refunds 单表改为四路合计, 此值不可与新值比较]' "
            "WHERE metric_code = 'refund_count' AND period_date < :d"
        ),
        {"d": "2026-09-14"},
    )


def downgrade() -> None:
    conn = op.get_bind()
    # 还原 3 个阈值
    for code, warn, crit in THRESHOLD_FIXES:
        old = THRESHOLD_OLD.get(code)
        if old and _has_metric(conn, code):
            _rethreshold(conn, code, old[0], old[1])
            conn.execute(
                sa.text(
                    "UPDATE alert_rules SET warning_value = :w, critical_value = :k, updated_at = now() "
                    "WHERE metric_code = :c"
                ),
                {"c": code, "w": old[0], "k": old[1]},
            )
    # 删除新增指标（含阈值/规则）
    if _has_rule(conn, NET_CODE):
        conn.execute(sa.text("DELETE FROM alert_rules WHERE metric_code = :c"), {"c": NET_CODE})
    conn.execute(sa.text("DELETE FROM metric_thresholds WHERE metric_code = :c"), {"c": NET_CODE})
    conn.execute(sa.text("DELETE FROM metric_daily_snapshots WHERE metric_code = :c"), {"c": NET_CODE})
    conn.execute(sa.text("DELETE FROM metric_registry WHERE code = :c"), {"c": NET_CODE})
    # 撤销快照标注
    conn.execute(
        sa.text(
            "UPDATE metric_daily_snapshots SET detail = REPLACE(detail, "
            "' [⚠️#83 口径已由 refunds 单表改为四路合计, 此值不可与新值比较]', '') "
            "WHERE metric_code = 'refund_count'"
        )
    )

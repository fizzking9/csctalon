"""add_data_arrival_health_metrics

采集健康告警 v1.2 (工单 #6 / A-20260904-001 v1.2 LOCKED):
- 弃用 ingest_volume_cs_record(入表量比例, 接待量波动大易误报) → 停用指标与规则
- 注册表驱动生成 data_arrival_<data_type> 指标 + 规则:
  主动抓取源 = sync_source_configs WHERE enabled AND default_sync
  被动推送源 = data_sources WHERE sync_source IN ('workbuddy','tencent_doc')
              + manual_refund(手动退款推送)
  判定 = 该源当日是否有任一渠道到达(主动抓取 success / 被动推送 / 手动导入 imported)

Revision ID: c2d3e4f5a6b7
Revises: b1e2f3a4c5d6
Create Date: 2026-09-04 16:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'c2d3e4f5a6b7'
down_revision: Union[str, None] = 'b1e2f3a4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_CODE = "ingest_volume_cs_record"


def _active_fetch_sources(conn) -> list[str]:
    """主动抓取源: sync_source_configs enabled + default_sync 且有抓取方式(fetch_mode 非空或默认).
    取 default_sync=true 且 enabled=true 的 data_type(排除纯推送/手动域)。
    """
    rows = conn.execute(sa.text(
        "SELECT data_type FROM sync_source_configs WHERE enabled = true AND default_sync = true ORDER BY sort_order"
    )).fetchall()
    # 排除"仅推送/手动导入"的源(它们不在 sync_source_configs 的抓取语义内; manual_refund 等不在此表)
    skip = {"device_combo"}  # 设备套餐 default_sync=false, 实际不在; 保留防御
    return [r[0] for r in rows if r[0] not in skip]


def _passive_sources(conn) -> list[str]:
    """被动推送源: data_sources.sync_source ∈ (workbuddy/tencent_doc) + manual_refund(手动退款推送)."""
    rows = conn.execute(sa.text(
        "SELECT code FROM data_sources WHERE sync_source IN ('workbuddy','tencent_doc') AND status='active'"
    )).fetchall()
    out = [r[0] for r in rows]
    # manual_refund 由 workbuddy 推送(manual_refund_sync_logs), 若 data_sources 未标 workbuddy 则补
    if "manual_refund" not in out:
        exists = conn.execute(sa.text(
            "SELECT count(*) FROM data_sources WHERE code='manual_refund' AND status='active'"
        )).scalar()
        if exists:
            out.append("manual_refund")
    return out


def _metric_exists(conn, code: str) -> bool:
    return conn.execute(sa.text("SELECT count(*) FROM metric_registry WHERE code=:c"), {"c": code}).scalar() > 0


def _rule_exists(conn, code: str) -> bool:
    return conn.execute(sa.text("SELECT count(*) FROM alert_rules WHERE metric_code=:c"), {"c": code}).scalar() > 0


def upgrade() -> None:
    conn = op.get_bind()

    # 1. 停用旧 ingest_volume_cs_record(防误报; 保留历史数据)
    conn.execute(sa.text("UPDATE metric_registry SET enabled=false WHERE code=:c"), {"c": OLD_CODE})
    conn.execute(sa.text("UPDATE alert_rules SET enabled=false WHERE metric_code=:c"), {"c": OLD_CODE})

    # 2. 动态生成 data_arrival_<type> 指标(主动抓取 + 被动推送源)
    sources = sorted(set(_active_fetch_sources(conn)) | set(_passive_sources(conn)))
    for dt in sources:
        code = f"data_arrival_{dt}"
        if not _metric_exists(conn, code):
            conn.execute(sa.text(
                """
                INSERT INTO metric_registry (code, name_cn, direction, period, unit, data_sources, description, enabled, created_at, updated_at)
                VALUES (:c, :name, 'higher_better', 'day', '',
                        '["bms_sync_logs","upload_records","complaint_sync_logs","manual_refund_sync_logs"]',
                        '采集健康(v1.2 注册表驱动): 该源当日是否有任一渠道(success抓取/被动推送/手动导入)到达记录; 不看业务量',
                        true, now(), now())
                """
            ), {"c": code, "name": f"{dt} 数据到达健康"})
        if not _rule_exists(conn, code):
            conn.execute(sa.text(
                """
                INSERT INTO alert_rules (name, type, condition, threshold, enabled,
                                         metric_code, excellent_value, warning_value, critical_value,
                                         channels, recipient_roles, silence_window, created_at, updated_at)
                VALUES (:name, 'metric', NULL, NULL, true,
                        :c, NULL, NULL, NULL,
                        '["inapp"]', '["admin","supervisor"]', NULL, now(), now())
                """
            ), {"name": f"数据到达-{dt}", "c": code})


def downgrade() -> None:
    conn = op.get_bind()
    # 重新启用旧指标/规则
    conn.execute(sa.text("UPDATE metric_registry SET enabled=true WHERE code=:c"), {"c": OLD_CODE})
    conn.execute(sa.text("UPDATE alert_rules SET enabled=true WHERE metric_code=:c"), {"c": OLD_CODE})
    # 删除 data_arrival_* 指标与规则
    rows = conn.execute(sa.text(
        "SELECT code FROM metric_registry WHERE code LIKE 'data_arrival_%'"
    )).fetchall()
    for (code,) in rows:
        conn.execute(sa.text("DELETE FROM alert_rules WHERE metric_code=:c"), {"c": code})
        conn.execute(sa.text("DELETE FROM metric_thresholds WHERE metric_code=:c"), {"c": code})
        conn.execute(sa.text("DELETE FROM metric_daily_snapshots WHERE metric_code=:c"), {"c": code})
        conn.execute(sa.text("DELETE FROM metric_registry WHERE code=:c"), {"c": code})

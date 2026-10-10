"""add_ingest_health_metric

采集数据健康告警指标种子 (工单 #5 / A-20260904-001 LOCKED):
- metric_registry 新增 ingest_volume_cs_record(采集健康类指标, 依赖 cs_record)
- metric_thresholds 新增默认阈值(相对近7日均值: 预警 70% / 告警 40%)
- alert_rules 新增启用规则(metric_code 指向新指标, 供 scan_alerts 每小时扫描)
- 修复历史缺陷: 生产 alert_rules 为空 → 指标引擎空转无告警; 本次随新指标一并补建规则

Revision ID: b1e2f3a4c5d6
Revises: a9e8d7c6b5a4
Create Date: 2026-09-04 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'b1e2f3a4c5d6'
down_revision: Union[str, None] = 'a9e8d7c6b5a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CODE = "ingest_volume_cs_record"


def _metric_registry_exists(conn) -> bool:
    return conn.execute(sa.text(
        "SELECT count(*) FROM metric_registry WHERE code = :c"
    ), {"c": CODE}).scalar() > 0


def _threshold_exists(conn) -> bool:
    return conn.execute(sa.text(
        "SELECT count(*) FROM metric_thresholds WHERE metric_code = :c AND valid_to IS NULL"
    ), {"c": CODE}).scalar() > 0


def _rule_exists(conn) -> bool:
    return conn.execute(sa.text(
        "SELECT count(*) FROM alert_rules WHERE metric_code = :c"
    ), {"c": CODE}).scalar() > 0


def upgrade() -> None:
    conn = op.get_bind()

    # 1. 指标注册(幂等)
    if not _metric_registry_exists(conn):
        conn.execute(sa.text(
            """
            INSERT INTO metric_registry (code, name_cn, direction, period, unit, data_sources, description, enabled, created_at, updated_at)
            VALUES (:c, '智能助手记录每日入表量', 'higher_better', 'day', '%',
                    '["cs_record","bms_sync_logs"]',
                    '采集健康指标: cs_record 当日入表量相对近7日均值比例(采集断流/骤降告警, 非业务指标); kind=ingest_health',
                    true, now(), now())
            """
        ), {"c": CODE})

    # 2. 默认阈值(相对近7日均值 %): 预警 <70% / 告警 <40%(可配置)
    if not _threshold_exists(conn):
        conn.execute(sa.text(
            """
            INSERT INTO metric_thresholds (metric_code, excellent_value, warning_value, critical_value, created_at, updated_at)
            VALUES (:c, 100.0, 70.0, 40.0, now(), now())
            """
        ), {"c": CODE})

    # 3. 启用规则(scan_alerts 遍历 alert_rules; 缺规则则引擎空转)
    #    同时为 5 个既有业务指标补建规则(修复历史缺陷: 生产 alert_rules 为空)
    existing_rules = set(r[0] for r in conn.execute(sa.text(
        "SELECT metric_code FROM alert_rules WHERE metric_code IS NOT NULL"
    )))
    rule_seeds = [
        # (name, metric_code, warning, critical)
        ("采集健康-接待入表量", CODE, 70.0, 40.0),
        ("业务-退款挽回率", "recovery_rate", 30.0, 20.0),
        ("业务-当日退款笔数", "refund_count", 30.0, 50.0),
        ("业务-投诉工单积压", "ticket_backlog", 20.0, 50.0),
        ("业务-近7天外部投诉量", "external_complaint_7d", 50.0, 70.0),
        ("业务-当日内部投诉量", "internal_complaint_daily", 8.0, 15.0),
    ]
    for name, mcode, warn, crit in rule_seeds:
        if mcode in existing_rules:
            continue
        conn.execute(sa.text(
            """
            INSERT INTO alert_rules (name, type, condition, threshold, enabled,
                                     metric_code, excellent_value, warning_value, critical_value,
                                     channels, recipient_roles, silence_window, created_at, updated_at)
            VALUES (:name, 'metric', NULL, NULL, true,
                    :mcode, NULL, :warn, :crit,
                    '["inapp"]', '["admin","supervisor"]', NULL, now(), now())
            """
        ), {"name": name, "mcode": mcode, "warn": warn, "crit": crit})


def downgrade() -> None:
    conn = op.get_bind()
    # 删除规则(含本迁移新建的) + 指标阈值 + 指标注册(幂等容忍不存在)
    conn.execute(sa.text("DELETE FROM alert_rules WHERE metric_code = :c"), {"c": CODE})
    conn.execute(sa.text("DELETE FROM metric_thresholds WHERE metric_code = :c"), {"c": CODE})
    conn.execute(sa.text("DELETE FROM metric_registry WHERE code = :c"), {"c": CODE})
    # 顺带清理迁移中为历史业务指标补建的规则
    for mcode in ("recovery_rate", "refund_count", "ticket_backlog", "external_complaint_7d", "internal_complaint_daily"):
        conn.execute(sa.text("DELETE FROM alert_rules WHERE metric_code = :c"), {"c": mcode})

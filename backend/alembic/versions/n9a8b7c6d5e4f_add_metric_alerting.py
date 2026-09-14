"""add metric registry & alerting extension

运营指标阈值化: 指标注册表 + 多级阈值(优秀/预警/告警) + 通知通道 + 告警投递记录;
扩展 alert_rules(指标模式字段) 与 alerts(指标值/恢复时间字段)。

Revision ID: n9a8b7c6d5e4f
Revises: x4a5b6c7d8e9f
Create Date: 2026-08-21 16:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'n9a8b7c6d5e4f'
down_revision: Union[str, None] = 'x4a5b6c7d8e9f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=:t)"
    ), {"t": table_name})
    return bool(result.scalar())


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t AND column_name=:c)"
    ), {"t": table, "c": column})
    return bool(result.scalar())


def upgrade() -> None:
    # ---------- 1. 指标注册表 ----------
    if not _table_exists('metric_registry'):
        op.create_table('metric_registry',
            sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('code', sa.String(length=64), nullable=False, comment='指标code'),
            sa.Column('name_cn', sa.String(length=128), nullable=False, comment='指标名'),
            sa.Column('direction', sa.String(length=16), nullable=False, comment='方向:higher_better/lower_better'),
            sa.Column('period', sa.String(length=8), nullable=False, comment='周期:day/week/month'),
            sa.Column('unit', sa.String(length=16), nullable=True, comment='单位'),
            sa.Column('data_sources', postgresql.JSONB(), nullable=True, comment='依赖数据源'),
            sa.Column('description', sa.Text(), nullable=True, comment='口径说明'),
            sa.Column('enabled', sa.Boolean(), nullable=False, comment='是否参与预警'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('code'),
        )
        op.create_index(op.f('ix_metric_registry_code'), 'metric_registry', ['code'])

    # ---------- 2. 阈值参数 ----------
    if not _table_exists('metric_thresholds'):
        op.create_table('metric_thresholds',
            sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('metric_code', sa.String(length=64), nullable=False, comment='指标code'),
            sa.Column('excellent_value', sa.Float(), nullable=True, comment='优秀线(可选)'),
            sa.Column('warning_value', sa.Float(), nullable=False, comment='预警线'),
            sa.Column('critical_value', sa.Float(), nullable=False, comment='告警线'),
            sa.Column('valid_from', sa.DateTime(timezone=True), nullable=True, comment='生效起始'),
            sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True, comment='生效结束'),
            sa.Column('updated_by', sa.Integer(), nullable=True, comment='修改人'),
            sa.ForeignKeyConstraint(['metric_code'], ['metric_registry.code'], ),
            sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_metric_thresholds_metric_code'), 'metric_thresholds', ['metric_code'])

    # ---------- 3. 每日快照 ----------
    if not _table_exists('metric_daily_snapshots'):
        op.create_table('metric_daily_snapshots',
            sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('metric_code', sa.String(length=64), nullable=False, comment='指标code'),
            sa.Column('value', sa.Float(), nullable=True, comment='指标值'),
            sa.Column('period_date', sa.String(length=16), nullable=False, comment='统计周期(北京时间)'),
            sa.Column('level', sa.String(length=16), nullable=True, comment='级别'),
            sa.Column('data_ok', sa.Boolean(), nullable=False, comment='数据是否完整'),
            sa.Column('detail', sa.Text(), nullable=True, comment='求值明细'),
            sa.ForeignKeyConstraint(['metric_code'], ['metric_registry.code'], ),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('metric_code', 'period_date', name='uq_snapshot_metric_period'),
        )
        op.create_index(op.f('ix_metric_daily_snapshots_metric_code'), 'metric_daily_snapshots', ['metric_code'])

    # ---------- 4. 通知通道 ----------
    if not _table_exists('notification_channels'):
        op.create_table('notification_channels',
            sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('code', sa.String(length=32), nullable=False, comment='通道code'),
            sa.Column('name_cn', sa.String(length=64), nullable=False, comment='通道名'),
            sa.Column('channel_type', sa.String(length=16), nullable=False, comment='类型:inapp/webhook/email'),
            sa.Column('config', postgresql.JSONB(), nullable=True, comment='配置'),
            sa.Column('enabled', sa.Boolean(), nullable=False, comment='是否启用'),
            sa.Column('description', sa.Text(), nullable=True, comment='说明'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('code'),
        )
        op.create_index(op.f('ix_notification_channels_code'), 'notification_channels', ['code'])

    # ---------- 5. 告警投递记录 ----------
    if not _table_exists('alert_deliveries'):
        op.create_table('alert_deliveries',
            sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('alert_id', sa.BigInteger(), nullable=True, comment='告警记录'),
            sa.Column('channel', sa.String(length=32), nullable=False, comment='通道code'),
            sa.Column('status', sa.String(length=16), nullable=False, comment='success/failed/degraded'),
            sa.Column('detail', sa.Text(), nullable=True, comment='投递明细'),
            sa.ForeignKeyConstraint(['alert_id'], ['alerts.id'], ),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_alert_deliveries_alert_id'), 'alert_deliveries', ['alert_id'])

    # ---------- 6. 扩展 alert_rules (指标模式) ----------
    if not _column_exists('alert_rules', 'metric_code'):
        op.add_column('alert_rules', sa.Column('metric_code', sa.String(length=64), nullable=True, comment='引用指标注册表code'))
        op.create_index(op.f('ix_alert_rules_metric_code'), 'alert_rules', ['metric_code'])
    if not _column_exists('alert_rules', 'excellent_value'):
        op.add_column('alert_rules', sa.Column('excellent_value', sa.Float(), nullable=True, comment='优秀线'))
    if not _column_exists('alert_rules', 'warning_value'):
        op.add_column('alert_rules', sa.Column('warning_value', sa.Float(), nullable=True, comment='预警线'))
    if not _column_exists('alert_rules', 'critical_value'):
        op.add_column('alert_rules', sa.Column('critical_value', sa.Float(), nullable=True, comment='告警线'))
    if not _column_exists('alert_rules', 'channels'):
        op.add_column('alert_rules', sa.Column('channels', postgresql.JSONB(), nullable=True, comment='通知通道列表'))
    if not _column_exists('alert_rules', 'recipient_roles'):
        op.add_column('alert_rules', sa.Column('recipient_roles', postgresql.JSONB(), nullable=True, comment='通知角色列表'))
    if not _column_exists('alert_rules', 'silence_window'):
        op.add_column('alert_rules', sa.Column('silence_window', postgresql.JSONB(), nullable=True, comment='静默时段'))

    # ---------- 7. 扩展 alerts (指标值/恢复时间) ----------
    if not _column_exists('alerts', 'metric_code'):
        op.add_column('alerts', sa.Column('metric_code', sa.String(length=64), nullable=True, comment='指标code'))
        op.create_index(op.f('ix_alerts_metric_code'), 'alerts', ['metric_code'])
    if not _column_exists('alerts', 'metric_value'):
        op.add_column('alerts', sa.Column('metric_value', sa.Float(), nullable=True, comment='触发时指标值'))
    if not _column_exists('alerts', 'threshold_value'):
        op.add_column('alerts', sa.Column('threshold_value', sa.Float(), nullable=True, comment='触发阈值'))
    if not _column_exists('alerts', 'period_start'):
        op.add_column('alerts', sa.Column('period_start', sa.DateTime(timezone=True), nullable=True, comment='统计周期起'))
    if not _column_exists('alerts', 'period_end'):
        op.add_column('alerts', sa.Column('period_end', sa.DateTime(timezone=True), nullable=True, comment='统计周期止'))
    if not _column_exists('alerts', 'data_ok'):
        op.add_column('alerts', sa.Column('data_ok', sa.Boolean(), nullable=False, server_default=sa.text('true'), comment='数据是否完整'))
    if not _column_exists('alerts', 'recovered_at'):
        op.add_column('alerts', sa.Column('recovered_at', sa.DateTime(timezone=True), nullable=True, comment='恢复时间'))

    # ---------- 8. 种子数据 ----------
    conn = op.get_bind()

    # 站内信通道(必存在)
    if conn.execute(sa.text("SELECT count(*) FROM notification_channels WHERE code='inapp'")).scalar() == 0:
        conn.execute(sa.text("""
            INSERT INTO notification_channels (code, name_cn, channel_type, config, enabled, description, created_at, updated_at)
            VALUES ('inapp', '站内信', 'inapp', '{}', true, '系统内消息通知', now(), now())
        """))

    # 首批指标注册(ON CONFLICT 幂等)
    conn.execute(sa.text("""
        INSERT INTO metric_registry (code, name_cn, direction, period, unit, data_sources, description, enabled, created_at, updated_at)
        VALUES
          ('recovery_rate', '退款挽回率', 'higher_better', 'day', '%', '["scrm_session","cs_record","refund"]', '当日退款挽回成功率(与今日看板退款挽回专项同口径)', true, now(), now()),
          ('refund_count', '当日退款笔数', 'lower_better', 'day', '笔', '["refund"]', '当日退款操作笔数(时间=创建时间, 北京时间)', true, now(), now()),
          ('ticket_backlog', '投诉工单积压', 'lower_better', 'day', '个', '["service_tickets"]', '当前未完成投诉工单总数(含历史遗留, 与投诉专项积压同口径)', true, now(), now()),
          ('external_complaint_7d', '近7天外部投诉量', 'lower_better', 'day', '条', '["external_complaints"]', '近7天315/监督局外部投诉登记量', true, now(), now()),
          ('internal_complaint_daily', '当日内部投诉量', 'lower_better', 'day', '条', '["service_tickets"]', '当日内部投诉工单数(商品/服务投诉, 北京时间)', true, now(), now())
        ON CONFLICT (code) DO NOTHING
    """))

    # 首批默认阈值(可调整; ON CONFLICT 用 metric_code+valid_to IS NULL 保护, 先查再插)
    existing_thr = set(r[0] for r in conn.execute(sa.text(
        "SELECT DISTINCT metric_code FROM metric_thresholds WHERE valid_to IS NULL"
    )))
    defaults = [
        ("recovery_rate", 85.0, 60.0, 40.0),
        ("refund_count", None, 30.0, 50.0),
        ("ticket_backlog", None, 20.0, 50.0),
        ("external_complaint_7d", None, 10.0, 20.0),
        ("internal_complaint_daily", None, 8.0, 15.0),
    ]
    for code, excellent, warning, critical in defaults:
        if code not in existing_thr:
            conn.execute(sa.text(
                "INSERT INTO metric_thresholds (metric_code, excellent_value, warning_value, critical_value, created_at, updated_at) "
                "VALUES (:c, :e, :w, :cr, now(), now())"
            ), {"c": code, "e": excellent, "w": warning, "cr": critical})


def downgrade() -> None:
    # 按依赖逆序删除
    op.drop_index(op.f('ix_alert_deliveries_alert_id'), table_name='alert_deliveries')
    op.drop_table('alert_deliveries')
    op.drop_index(op.f('ix_notification_channels_code'), table_name='notification_channels')
    op.drop_table('notification_channels')
    op.drop_index(op.f('ix_metric_daily_snapshots_metric_code'), table_name='metric_daily_snapshots')
    op.drop_table('metric_daily_snapshots')
    op.drop_index(op.f('ix_metric_thresholds_metric_code'), table_name='metric_thresholds')
    op.drop_table('metric_thresholds')
    op.drop_index(op.f('ix_metric_registry_code'), table_name='metric_registry')
    op.drop_table('metric_registry')

    # 扩展列回退
    for table, cols in (
        ("alert_rules", ["metric_code", "excellent_value", "warning_value", "critical_value", "channels", "recipient_roles", "silence_window"]),
        ("alerts", ["metric_code", "metric_value", "threshold_value", "period_start", "period_end", "data_ok", "recovered_at"]),
    ):
        for c in cols:
            try:
                op.drop_column(table, c)
            except Exception:  # noqa: BLE001 列不存在则跳过
                pass

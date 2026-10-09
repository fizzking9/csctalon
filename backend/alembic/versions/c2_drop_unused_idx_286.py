"""#286 删除 8 个未用索引 (约 72MB, #274 审计 C2)

Revision ID: c2_drop_unused_idx_286
Revises: o5_risk_alert_settings
Create Date: 2026-10-09

依据: pg_stat_user_indexes 统计自集群初始化从未 reset, 8 个索引 idx_scan=0
且 >1MB; 已逐个复核列引用与去重键:
- ticket_action_logs 3 索引: 访问均以 ticket_no(FK 索引) 为入口;
- devices.start_iccid / service_tickets.synced_at / cash_flow_wide.biz_no:
  只写不作为过滤条件;
- merchant_refunds.merchant_order_no: 去重走 (trade_time, merchant_order_no)
  部分唯一复合索引, 与本单列索引无关;
- data_match_relations.src_table: 100% 行均为 'refund', 过滤无选择性。
"""
from alembic import op

# revision identifiers
revision = "c2_drop_unused_idx_286"
down_revision = "o5_risk_alert_settings"
branch_labels = None
depends_on = None

# (索引名, 表名, 列) — downgrade 按此重建
_INDEXES = [
    ("ix_ticket_action_logs_operator_action", "ticket_action_logs", ["operator"]),
    ("ix_ticket_action_logs_action_at", "ticket_action_logs", ["action_at"]),
    ("ix_ticket_action_logs_action_type", "ticket_action_logs", ["action_type"]),
    ("ix_devices_start_iccid", "devices", ["start_iccid"]),
    ("ix_service_tickets_synced_at", "service_tickets", ["synced_at"]),
    ("ix_cash_flow_wide_biz_no", "cash_flow_wide", ["biz_no"]),
    ("ix_merchant_refunds_merchant_order_no", "merchant_refunds", ["merchant_order_no"]),
    ("ix_data_match_relations_src_table", "data_match_relations", ["src_table"]),
]


def upgrade() -> None:
    for idx, table, _cols in _INDEXES:
        op.drop_index(idx, table_name=table)


def downgrade() -> None:
    for idx, table, cols in _INDEXES:
        op.create_index(idx, table, cols, unique=False)

"""add_bms_5source_tables

BMS 五源采集扩容新增 4 张表(A-20260905-001 PENDING / 工单 P1+P3):
  - device_flow_usage     设备月流量使用(审计核心, 唯一 device_sn+stat_month)
  - refund_record_reports 退款记录报表(线上退款水单, E 路对账)
  - bms_orders            订单管理台账
  - return_trackings      退货跟踪
全部为采集新增表, 不影响既有表。金额 Numeric(12,2) 元; 时间 timestamptz。

Revision ID: f6a7b8c9d0e1
Revises: f1a2b3c4d5e6
Create Date: 2026-09-05 18:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. device_flow_usage 设备月流量使用 ───────────────────────────
    op.create_table(
        'device_flow_usage',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('device_sn', sa.String(length=64), nullable=True, comment='设备SN'),
        sa.Column('merchant_id', sa.String(length=64), nullable=True, comment='商户ID(businessId)'),
        sa.Column('merchant_name', sa.String(length=128), nullable=True, comment='商户名称'),
        sa.Column('device_type_name', sa.String(length=64), nullable=True, comment='设备类型'),
        sa.Column('device_group_name', sa.String(length=64), nullable=True, comment='设备组'),
        sa.Column('device_combo_name', sa.String(length=128), nullable=True, comment='当前套餐'),
        sa.Column('stat_month', sa.String(length=16), nullable=True, comment='统计月份 YYYY-MM'),
        sa.Column('flow_gb', sa.Numeric(precision=12, scale=4), nullable=True, comment='该月流量(GB)'),
        sa.Column('month_complete', sa.Boolean(), nullable=True, comment='是否完整月'),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True, comment='本次抓取时间'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='记录创建时间'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='记录更新时间'),
        sa.ForeignKeyConstraint(['device_sn'], ['devices.sn'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_sn', 'stat_month', name='uq_device_flow_usage_sn_month'),
        comment='设备月流量使用(单SN×月汇总, 设备审计)',
    )
    op.create_index('ix_device_flow_usage_device_sn', 'device_flow_usage', ['device_sn'])
    op.create_index('ix_device_flow_usage_stat_month', 'device_flow_usage', ['stat_month'])
    op.create_index('ix_device_flow_usage_merchant_id', 'device_flow_usage', ['merchant_id'])
    op.create_index('ix_device_flow_usage_merchant_name', 'device_flow_usage', ['merchant_name'])

    # ── 2. refund_record_reports 退款记录报表 ─────────────────────────
    op.create_table(
        'refund_record_reports',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('platform_order_no', sa.String(length=128), nullable=True, comment='平台订单号'),
        sa.Column('wx_order_no', sa.String(length=128), nullable=True, comment='微信订单号'),
        sa.Column('merchant_id', sa.String(length=64), nullable=True, comment='商户号(对账锚点)'),
        sa.Column('merchant_name', sa.String(length=128), nullable=True, comment='商户名'),
        sa.Column('device_sn', sa.String(length=64), nullable=True, comment='设备号(对账锚点)'),
        sa.Column('product_name', sa.String(length=128), nullable=True, comment='商品名称'),
        sa.Column('refund_time', sa.DateTime(timezone=True), nullable=True, comment='退款时间(精确到秒)'),
        sa.Column('apply_refund_amount', sa.Numeric(precision=12, scale=2), nullable=True, comment='申请退款金额(元)'),
        sa.Column('refund_amount', sa.Numeric(precision=12, scale=2), nullable=True, comment='退款金额(元)'),
        sa.Column('refund_status', sa.String(length=32), nullable=True, comment='退款状态'),
        sa.Column('raw_extra', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='原始数据冗余'),
        sa.Column('synced_at', sa.DateTime(timezone=True), nullable=True, comment='同步采集时间'),
        sa.Column('backfill_op_time', sa.DateTime(timezone=True), nullable=True, comment='回填到D路op_time的时间'),
        sa.Column('backfill_manual_refund_id', sa.String(length=64), nullable=True, comment='已回填manual_refunds.id'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='记录创建时间'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='记录更新时间'),
        sa.ForeignKeyConstraint(['device_sn'], ['devices.sn'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('platform_order_no', 'refund_time', 'refund_amount', name='uq_refund_record_reports_dedup'),
        comment='退款记录报表(BMS线上退款水单, E路对账)',
    )
    op.create_index('ix_refund_record_reports_platform_order_no', 'refund_record_reports', ['platform_order_no'])
    op.create_index('ix_refund_record_reports_wx_order_no', 'refund_record_reports', ['wx_order_no'])
    op.create_index('ix_refund_record_reports_merchant_id', 'refund_record_reports', ['merchant_id'])
    op.create_index('ix_refund_record_reports_merchant_name', 'refund_record_reports', ['merchant_name'])
    op.create_index('ix_refund_record_reports_device_sn', 'refund_record_reports', ['device_sn'])
    op.create_index('ix_refund_record_reports_refund_time', 'refund_record_reports', ['refund_time'])
    op.create_index('ix_refund_record_reports_refund_status', 'refund_record_reports', ['refund_status'])
    op.create_index('ix_refund_record_reports_backfill_manual_refund_id', 'refund_record_reports', ['backfill_manual_refund_id'])

    # ── 3. bms_orders 订单管理 ────────────────────────────────────────
    op.create_table(
        'bms_orders',
        sa.Column('order_no', sa.String(length=64), nullable=False, comment='订单号'),
        sa.Column('order_type', sa.String(length=32), nullable=True, comment='订单类型'),
        sa.Column('device_sn', sa.String(length=64), nullable=True, comment='设备SN'),
        sa.Column('combo_name', sa.String(length=128), nullable=True, comment='套餐名称'),
        sa.Column('combo_price', sa.Numeric(precision=12, scale=2), nullable=True, comment='套餐价格(元)'),
        sa.Column('payable_price', sa.Numeric(precision=12, scale=2), nullable=True, comment='应付价格(元)'),
        sa.Column('actual_pay_price', sa.Numeric(precision=12, scale=2), nullable=True, comment='实际支付价格(元)'),
        sa.Column('balance_pay_price', sa.Numeric(precision=12, scale=2), nullable=True, comment='余额支付(元)'),
        sa.Column('wx_order_no', sa.String(length=128), nullable=True, comment='微信订单号'),
        sa.Column('pay_date', sa.DateTime(timezone=True), nullable=True, comment='支付时间'),
        sa.Column('pay_type', sa.String(length=32), nullable=True, comment='支付类型'),
        sa.Column('order_status', sa.String(length=32), nullable=True, comment='订单状态'),
        sa.Column('order_result', sa.Text(), nullable=True, comment='订单结果'),
        sa.Column('order_status_raw', sa.Integer(), nullable=True, comment='订单状态原始码'),
        sa.Column('create_user', sa.String(length=64), nullable=True, comment='创建人'),
        sa.Column('create_date', sa.DateTime(timezone=True), nullable=True, comment='创建时间(同步锚点)'),
        sa.Column('business_name', sa.String(length=128), nullable=True, comment='商户名称'),
        sa.Column('business_id', sa.String(length=64), nullable=True, comment='内部商户ID'),
        sa.Column('device_type_name', sa.String(length=64), nullable=True, comment='设备类型'),
        sa.Column('refund_status', sa.String(length=32), nullable=True, comment='退款状态'),
        sa.Column('active_date', sa.DateTime(timezone=True), nullable=True, comment='激活时间'),
        sa.Column('last_stored_time', sa.DateTime(timezone=True), nullable=True, comment='最后储值时间'),
        sa.Column('mch_id', sa.String(length=64), nullable=True, comment='微信商户Id'),
        sa.Column('merchant_name', sa.String(length=128), nullable=True, comment='微信商户名称'),
        sa.Column('before_serve_valid_date', sa.DateTime(timezone=True), nullable=True, comment='续费前服务有效期'),
        sa.Column('after_serve_valid_date', sa.DateTime(timezone=True), nullable=True, comment='续费后服务有效期'),
        sa.Column('days', sa.Integer(), nullable=True, comment='有效期天数'),
        sa.Column('raw_extra', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='原始数据冗余'),
        sa.Column('synced_at', sa.DateTime(timezone=True), nullable=True, comment='同步采集时间'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='记录创建时间'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='记录更新时间'),
        sa.ForeignKeyConstraint(['device_sn'], ['devices.sn'], ),
        sa.PrimaryKeyConstraint('order_no'),
        comment='订单管理台账(BMS支付管理订单)',
    )
    op.create_index('ix_bms_orders_device_sn', 'bms_orders', ['device_sn'])
    op.create_index('ix_bms_orders_create_date', 'bms_orders', ['create_date'])
    op.create_index('ix_bms_orders_order_type', 'bms_orders', ['order_type'])
    op.create_index('ix_bms_orders_order_status', 'bms_orders', ['order_status'])
    op.create_index('ix_bms_orders_business_name', 'bms_orders', ['business_name'])
    op.create_index('ix_bms_orders_business_id', 'bms_orders', ['business_id'])
    op.create_index('ix_bms_orders_mch_id', 'bms_orders', ['mch_id'])
    op.create_index('ix_bms_orders_wx_order_no', 'bms_orders', ['wx_order_no'])
    op.create_index('ix_bms_orders_pay_date', 'bms_orders', ['pay_date'])

    # ── 4. return_trackings 退货跟踪 ──────────────────────────────────
    op.create_table(
        'return_trackings',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('device_sn', sa.String(length=64), nullable=True, comment='设备SN'),
        sa.Column('device_type', sa.String(length=64), nullable=True, comment='设备类型'),
        sa.Column('shop', sa.String(length=128), nullable=True, comment='店铺'),
        sa.Column('merchant', sa.String(length=128), nullable=True, comment='商户'),
        sa.Column('paid_amount', sa.Numeric(precision=12, scale=2), nullable=True, comment='实付金额(元)'),
        sa.Column('return_type', sa.String(length=32), nullable=True, comment='退货类型'),
        sa.Column('refund_amount', sa.Numeric(precision=12, scale=2), nullable=True, comment='退款金额(元)'),
        sa.Column('return_sign_status', sa.String(length=32), nullable=True, comment='退货签收状态'),
        sa.Column('return_express_no', sa.String(length=64), nullable=True, comment='退货快递单号'),
        sa.Column('return_reason', sa.String(length=128), nullable=True, comment='退货原因'),
        sa.Column('order_no', sa.String(length=128), nullable=True, comment='销售订单号'),
        sa.Column('order_time', sa.DateTime(timezone=True), nullable=True, comment='下单时间'),
        sa.Column('return_time', sa.DateTime(timezone=True), nullable=True, comment='退货时间'),
        sa.Column('sign_time', sa.DateTime(timezone=True), nullable=True, comment='签收时间'),
        sa.Column('creator', sa.String(length=64), nullable=True, comment='创建人'),
        sa.Column('updater', sa.String(length=64), nullable=True, comment='更新人'),
        sa.Column('created_at_raw', sa.DateTime(timezone=True), nullable=True, comment='原始创建时间(同步锚点)'),
        sa.Column('updated_at_raw', sa.DateTime(timezone=True), nullable=True, comment='原始更新时间'),
        sa.Column('remark', sa.Text(), nullable=True, comment='退货备注'),
        sa.Column('raw_extra', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='原始数据冗余'),
        sa.Column('synced_at', sa.DateTime(timezone=True), nullable=True, comment='同步采集时间'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='记录创建时间'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='记录更新时间'),
        sa.ForeignKeyConstraint(['device_sn'], ['devices.sn'], ),
        sa.PrimaryKeyConstraint('id'),
        comment='退货跟踪(BMS售后退货/换货全流程)',
    )
    op.create_index('ix_return_trackings_device_sn', 'return_trackings', ['device_sn'])
    op.create_index('ix_return_trackings_shop', 'return_trackings', ['shop'])
    op.create_index('ix_return_trackings_merchant', 'return_trackings', ['merchant'])
    op.create_index('ix_return_trackings_return_type', 'return_trackings', ['return_type'])
    op.create_index('ix_return_trackings_return_express_no', 'return_trackings', ['return_express_no'])
    op.create_index('ix_return_trackings_order_no', 'return_trackings', ['order_no'])
    op.create_index('ix_return_trackings_created_at_raw', 'return_trackings', ['created_at_raw'])


def downgrade() -> None:
    op.drop_table('return_trackings')
    op.drop_table('bms_orders')
    op.drop_table('refund_record_reports')
    op.drop_table('device_flow_usage')

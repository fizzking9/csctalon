"""#339 大表物理精简: cs_records + bms_orders (Phase ①)

Revision ID: f1a2b3c4d5e6
Revises: e9d4f5c6b1a2
Create Date: 2026-10-21

背景:
  cs_records 54 万行, bms_orders 67 万行, 业务侧宽扫描 (TOPN/GROUP BY) 经
  EXPLAIN ANALYZE 验证走 Parallel Index Scan + 全排序, 90 天窗口约 150ms,
  后续数据增长或叠 JOIN 会进入百 ms→秒级瓶颈。

设计 (Phase ①, 增量):
  std_cs_record / std_bms_order 两张**精简物理表**(新增):
  - 仅保留业务实际消费列 (audit_timeline / device_profile / schedules /
    incidents / complaint_risk_service / person_extractor / dim_device_combo_service /
    admin_bms_backfill 及 analytics 宽扫描 GROUP BY 引用列)
  - cs_records: 16 源列 → 12 保留, 削 25% (删 send_content / problem_create_time /
    last_consult_time / updated_at)
  - bms_orders: 31 源列 → 19 保留, 削 39% (删 raw_extra JSONB / synced_at / 12 业务
    零消费列: payable_price / wx_order_no / order_result / order_status_raw /
    last_stored_time / mch_id / business_id / before_serve_valid_date /
    after_serve_valid_date / days)
  - ⚠️ order_type / combo_price / balance_pay_price / pay_type / pay_date 虽 analytics
    宽扫描不用, 但 audit_timeline._build_bms_items 渲染时序 UI 详情依赖, 保留
  - 重建作业幂等 TRUNCATE + INSERT…SELECT (见 app/services/governance/std_big_rebuild.py)
  - std_cs_record_all / std_bms_order_all 全量镜像视图保持不变
  - cs_record_clean / bms_order_clean **暂不重定向** (Phase ② 待 EXPLAIN ANALYZE
    验证收益后再做, 避免 ORM trim + 测试矩阵爆炸)

索引设计 (按业务查询模式):
  std_cs_record:
    ix_std_cs_record_op_time (operation_time)
    ix_std_cs_record_sn (device_sn)
    ix_std_cs_record_cat_op (problem_category, operation_time DESC) -- TOPN GROUP BY
  std_bms_order:
    ix_std_bms_order_sn (device_sn)
    ix_std_bms_order_create_date (create_date)
    ix_std_bms_order_active_date (active_date)
"""
from typing import Sequence, Union

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e9d4f5c6b1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ── cs_records: 16 → 12 列 ──
_CS_COLS = """\
    id,
    problem_category, problem_type, device_sn, solution,
    operation_time, operator, description, execution_result, user_accepted,
    consult_time,
    created_at
"""

# ── bms_orders: 31 → 19 列 ──
_BO_COLS = """\
    order_no, order_type, device_sn, combo_name, combo_price,
    actual_pay_price, balance_pay_price, pay_date, pay_type,
    order_status, refund_status,
    create_user, create_date, business_name, merchant_name,
    active_date, device_type_name,
    created_at, updated_at
"""


def upgrade() -> None:
    # 1) cs_records 精简物理表
    op.execute(f"""
        CREATE TABLE std_cs_record AS
        SELECT {_CS_COLS}
        FROM ods.cs_records
        WITH DATA
    """)
    op.create_index("ix_std_cs_record_op_time", "std_cs_record", ["operation_time"])
    op.create_index("ix_std_cs_record_sn", "std_cs_record", ["device_sn"])
    op.create_index(
        "ix_std_cs_record_cat_op", "std_cs_record",
        ["problem_category", "operation_time DESC"],
    )
    op.execute("ALTER TABLE std_cs_record ADD PRIMARY KEY (id)")

    # 2) bms_orders 精简物理表
    op.execute(f"""
        CREATE TABLE std_bms_order AS
        SELECT {_BO_COLS}
        FROM ods.bms_orders
        WITH DATA
    """)
    op.create_index("ix_std_bms_order_sn", "std_bms_order", ["device_sn"])
    op.create_index("ix_std_bms_order_create_date", "std_bms_order", ["create_date"])
    op.create_index("ix_std_bms_order_active_date", "std_bms_order", ["active_date"])
    op.execute("ALTER TABLE std_bms_order ADD PRIMARY KEY (order_no)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS std_cs_record")
    op.execute("DROP TABLE IF EXISTS std_bms_order")

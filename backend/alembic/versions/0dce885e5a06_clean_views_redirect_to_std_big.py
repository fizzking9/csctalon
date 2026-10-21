"""#339 Phase ③: cs_record_clean / bms_order_clean 重定向到 std 精简物理表

Revision ID: 0dce885e5a06
Revises: d9e1f0c2b4a6
Create Date: 2026-10-21

背景:
  Phase ① (d9e1f0c2b4a6) 建立精简物理表 std_cs_record (16→12 列, -25%) /
  std_bms_order (31→19 列, -39%), EXPLAIN 实测 cs_records 90d TOPN 1.87x (83ms→44ms)。
  Phase ② 消费侧 table() 路径已切换。本迁移把 analytics 读的干净层视图
  cs_record_clean (24 处消费, 主要在 analytics/refund.py) / bms_order_clean
  一并重定向, 让宽扫描全链路吃到精简表红利。

列审计 (Phase ③ 前置, 2026-10-21):
  - cs_record_clean 原暴露 16 列, 删 send_content / problem_create_time /
    last_consult_time / updated_at → grep 证实 analytics 零消费 (仅采集解析
    data_parser / 采集写入 upsert/_tickets / 元数据 data_source_registry /
    质量探针规则名 date_anomaly_rules 引用, 均作用于 ods 源表, 不受影响)
  - bms_order_clean 原暴露 29 列, 删 payable_price / wx_order_no / order_result /
    order_status_raw / last_stored_time / mch_id / business_id /
    before_serve_valid_date / after_serve_valid_date / days → analytics 零消费
    (refund.py:2100 的 wx_order_no 属 refund_record_report_clean; 各处 `.days`
    均为 timedelta 运算非列引用)
  - ORM 同步 trim: CsRecordClean / BmsOrderClean (app/models/ods_clean.py),
    CLEAN_VIEWS 源改指 std 表 (conftest 测试库: std 视图 over ods → clean 视图
    over std 视图, fixture 插 ods 数据仍自动可见)

⚠️ 视图重建须 DROP 后 CREATE (PG 的 CREATE OR REPLACE 不允许删列);
   依赖本视图的下游对象无 (analytics 仅裸 SELECT)。
⚠️ std_cs_record / std_bms_order 每日 03:40 由 batch job rebuild_std_big 全量
   重建 (TRUNCATE+INSERT 同事务原子换装, 读侧 MVCC 无感知)。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0dce885e5a06"
down_revision: Union[str, None] = "d9e1f0c2b4a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ── 重定向后列集 = std 精简表全列 (单一来源: std_big_rebuild._CS_COLS/_BO_COLS
#    与 migration d9e1f0c2b4a6 逐字一致) ──
_CS_FROM_STD = """\
    id, problem_category, problem_type, device_sn, solution,
    operation_time, operator, description, execution_result, user_accepted,
    consult_time, created_at
"""
_BO_FROM_STD = """\
    order_no, order_type, device_sn, combo_name, combo_price,
    actual_pay_price, balance_pay_price, pay_date, pay_type,
    order_status, refund_status,
    create_user, create_date, business_name, merchant_name,
    active_date, device_type_name,
    created_at, updated_at
"""

# ── downgrade 恢复列集 = Phase ③ 前的 clean 视图原列集 (over ods, 与
#    migration c3d4e5f6a7b8 / ods_clean.py 模型逐字一致) ──
_CS_FROM_ODS = """\
    id, problem_category, problem_type, device_sn, solution,
    operation_time, operator, send_content, description, execution_result,
    user_accepted, consult_time, problem_create_time, last_consult_time,
    created_at, updated_at
"""
_BO_FROM_ODS = """\
    order_no, order_type, device_sn, combo_name, combo_price, payable_price,
    actual_pay_price, balance_pay_price, wx_order_no, pay_date, pay_type,
    order_status, order_result, order_status_raw, create_user, create_date,
    business_name, business_id, device_type_name, refund_status, active_date,
    last_stored_time, mch_id, merchant_name, before_serve_valid_date,
    after_serve_valid_date, days, created_at, updated_at
"""


def upgrade() -> None:
    # 1) cs_record_clean: ods.cs_records(宽) → std_cs_record(精简)
    op.execute("DROP VIEW IF EXISTS cs_record_clean")
    op.execute(f"CREATE VIEW cs_record_clean AS SELECT {_CS_FROM_STD} FROM std_cs_record")
    # 2) bms_order_clean: ods.bms_orders(宽) → std_bms_order(精简)
    op.execute("DROP VIEW IF EXISTS bms_order_clean")
    op.execute(f"CREATE VIEW bms_order_clean AS SELECT {_BO_FROM_STD} FROM std_bms_order")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS cs_record_clean")
    op.execute(f"CREATE VIEW cs_record_clean AS SELECT {_CS_FROM_ODS} FROM ods.cs_records")
    op.execute("DROP VIEW IF EXISTS bms_order_clean")
    op.execute(f"CREATE VIEW bms_order_clean AS SELECT {_BO_FROM_ODS} FROM ods.bms_orders")

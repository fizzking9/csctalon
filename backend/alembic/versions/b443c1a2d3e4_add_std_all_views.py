"""add std_*_all full-mirror views over ods refund tables

Revision ID: b443c1a2d3e4
Revises: a332b05d6e7f
Create Date: 2026-10-19

#334: 退款域业务代码零读 ods —— 全状态语义场景统一迁 std 层全量镜像视图。

五个视图（public schema, SELECT * 透传 ods, 含 T1 已加的采纳四列）：
  std_refund_online_all   → ods.refunds              (A 路全量)
  std_wallet_flow_all     → ods.wallet_withdrawals   (B 路全量)
  std_refund_shanmao_all  → ods.shanmao_refunds      (C 路全量)
  std_refund_manual_all   → ods.manual_refunds       (D 路全量)
  std_refund_record_all   → ods.refund_record_reports (E 路全量)

定位：
  - 经营统计口径仍读 std_* 采纳集物理表（不动）；
  - 全状态语义（挽回漏斗/状态分布/工作量/导出明细/链路台账/设备档案/对账勾稽）
    一律读本视图，业务代码不再直读 ods；
  - ods 回归三本职：采集写入、采纳作业读取、质量治理标记。

视图零存储、随 ods 实时一致，采纳作业无需改动。
"""
from typing import Sequence, Union

from alembic import op
from app.models.refund_standard import STD_ALL_VIEWS

revision: str = "b443c1a2d3e4"
down_revision: Union[str, None] = "a332b05d6e7f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 视图名 → ods 源表: 单一来源 STD_ALL_VIEWS(代码常量白名单, 与 conftest 逐字一致)
_VIEWS = STD_ALL_VIEWS


def upgrade() -> None:
    for view, src in _VIEWS.items():
        # CREATE OR REPLACE: 幂等, 重跑安全; 显式 ods 限定不受 search_path 影响
        op.execute(f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM {src}")


def downgrade() -> None:
    for view in _VIEWS:
        op.execute(f"DROP VIEW IF EXISTS {view}")

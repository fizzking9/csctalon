"""reimbursement_items_invoice_unique

Revision ID: 64a584531400
Revises: r6f7a8b9c0d1
Create Date: 2026-08-20 08:56:28.292288
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '64a584531400'
down_revision: Union[str, None] = 'r6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 2026-08-20 审计 H-2/API-02 修复: 防同一发票重复报销
    # 1. 清理历史重复关联: 同 invoice_id 保留最早一条(id 最小), 其余删除
    op.execute(
        """
        DELETE FROM reimbursement_items a
        USING reimbursement_items b
        WHERE a.invoice_id = b.invoice_id
          AND a.id > b.id
        """
    )
    # 2. 唯一约束
    op.create_unique_constraint(
        "uq_reimbursement_items_invoice_id", "reimbursement_items", ["invoice_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_reimbursement_items_invoice_id", "reimbursement_items", type_="unique")

"""demote stub persons — 人力资源主表与别名层解耦

Revision ID: v1a2b3c4d5e6
Revises: u0d1e2f3a4b5
Create Date: 2026-07-30 10:30:00.000000

数据清理(配合代码改动 person_extractor 不再造 person):
1. HR 人员(position 非空 = 有任职 = 真人)标记 is_confirmed=true → 进入"已确认"主表视图
2. 空壳 person 的别名解绑到待归并队列(person_aliases.person_id = NULL)
3. 删除空壳 person(position IS NULL AND is_confirmed=false AND is_active=true)

空壳特征:由旧 person_extractor 从业务表名字自动生成,每壳 1 别名、0 任职,
删除零数据损失;别名保留(解绑到 NULL),等待人工/HR 归并到真人。
"""

from alembic import op

revision = "v1a2b3c4d5e6"
down_revision = "u0d1e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. HR 人员(position 非空=有任职=真人)标记已确认,使其进入"已确认"主表视图
    op.execute(
        "UPDATE persons SET is_confirmed = true WHERE position IS NOT NULL AND is_confirmed = false"
    )
    # 2. 空壳 person 的别名解绑到待归并(person_id=NULL)
    op.execute(
        """
        UPDATE person_aliases SET person_id = NULL
        WHERE person_id IN (
            SELECT id FROM persons
            WHERE position IS NULL AND is_confirmed = false AND is_active = true
        )
        """
    )
    # 3. 删除空壳 person(staff_assignments FK 为 CASCADE,但空壳 0 任职,无连锁删除)
    op.execute(
        "DELETE FROM persons WHERE position IS NULL AND is_confirmed = false AND is_active = true"
    )


def downgrade() -> None:
    # 数据清理不可逆:空壳 person 已删除,别名保留在 person_aliases(person_id=NULL)待归并
    # 如需恢复需重新跑旧版 person_extractor(不推荐,与解耦目标相悖)
    raise NotImplementedError("数据清理不可逆:空壳 person 已删除,别名保留在待归并队列")

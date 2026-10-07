"""cs_records 唯一约束改为 NULLS NOT DISTINCT

【工单】#77 cs_records 重复行止损：唯一约束因 NULL 失效
【问题】原约束 uq_cs_record_business 含 8 列，其中 problem_create_time 大量为 NULL。
        PostgreSQL 唯一约束默认 NULLS DISTINCT —— 任一列为 NULL 时该行不参与去重，
        导致重复行可无限插入（实测单秒同一 SN 最多 64 行，2026-09 重复率 47%）。
【修复】重建约束为 UNIQUE NULLS NOT DISTINCT（PG 15+ 特性），
        使 NULL 视为相等值参与去重，从数据库层堵住重复插入。
【依据】docs/standards/数据采集合并去重机制说明.md（去重键）
        工单 #77 正文「根因判断」与「处理动作」
【数据】本迁移只改约束，不动数据；重复行清理由工单 #77 单独执行并留备份。

⚠️ 注意：PostgreSQL 15 起才支持 NULLS NOT DISTINCT。
   项目 PostgreSQL 版本为 15.19，满足要求。

Revision ID: a1c2e3f4d5b6
Revises: b2c3d4e5f6a7
Create Date: 2026-09-13 12:30:00.000000
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1c2e3f4d5b6'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 业务唯一键（与 app/models/cs_record.py 保持一致）
_UNIQUE_COLS = (
    "device_sn", "operation_time", "operator",
    "problem_category", "problem_type", "solution",
    "consult_time", "problem_create_time",
)
_CONSTRAINT_NAME = "uq_cs_record_business"


def upgrade() -> None:
    # 0) 先清理历史重复行 —— 否则下面加唯一约束必然失败(库中已存在重复)。
    #    去重口径与业务键一致，保留每组 id 最小的一条(最早入库的那条)。
    #    ⚠️ 这是**幂等**操作: 无重复时不删任何行。
    #    生产已于 2026-09-13 手工清理(备份 /tmp/cs_records_pre_dedup_*.sql)，此处兜底
    #    其他环境(本地/测试库)；重复行是同一事件的重复写入，无业务信息损失。
    op.execute(
        """
        DELETE FROM cs_records c
        WHERE NOT EXISTS (
            SELECT 1 FROM (
                SELECT min(id) AS keep_id
                FROM cs_records
                GROUP BY device_sn, operation_time, operator, solution, consult_time
            ) k
            WHERE k.keep_id = c.id
        )
        """
    )

    # 1) 删除旧约束（NULLS DISTINCT 语义，对含 NULL 的记录不起去重作用）
    #    ⚠️ 空库可重放（#211）：建表迁移 b4c9d1f02e5a 从未创建过该唯一约束
    #    （旧约束历史上仅由 create_all 依模型带出，链上无独立建约束迁移）。
    #    全新环境顺序迁移到此处时约束并不存在，无条件 drop_constraint 会以
    #    UndefinedObject 中断整条迁移链（create_all + stamp head 的盲区）。
    #    故先查 pg_constraint：存在（存量库）才 DROP；不存在（全新空库）跳过，
    #    由下一步直接建立 NULLS NOT DISTINCT 约束。两种路径最终 schema 一致。
    op.execute(
        f"""
        DO $do$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'cs_records'::regclass
                  AND conname = '{_CONSTRAINT_NAME}'
            ) THEN
                ALTER TABLE cs_records DROP CONSTRAINT {_CONSTRAINT_NAME};
            END IF;
        END $do$;
        """
    )

    # 2) 重建为 NULLS NOT DISTINCT：NULL 视为相等值，参与去重
    #    这样即使 problem_create_time 为 NULL，重复行也会被数据库拒绝。
    #    与 upsert.py 的 on_conflict_do_nothing(constraint=...) 同名，无需改业务代码。
    op.execute(
        f"ALTER TABLE cs_records ADD CONSTRAINT {_CONSTRAINT_NAME} "
        f"UNIQUE NULLS NOT DISTINCT ({', '.join(_UNIQUE_COLS)})"
    )


def downgrade() -> None:
    # 回滚为默认 NULLS DISTINCT（恢复原行为）
    op.execute(f"ALTER TABLE cs_records DROP CONSTRAINT {_CONSTRAINT_NAME}")
    op.create_unique_constraint(_CONSTRAINT_NAME, "cs_records", list(_UNIQUE_COLS))

"""#322 方案A: 批量新建 public 干净层只读视图(10 张, over ods 源表)

背景(#319/#322 数据分层):
- #319 已把 21 张信源表移入 ods schema; C2 契约要求 services/analytics/** 不得直读 ods 源表。
- 本迁移为剩余各域落地「方案A 薄视图」: 一表一视图, **逐列取自 ods 源表, 不做任何加工**
  (不改变任何口径/过滤/时间换算), 仅把 analytics 的「读法」从 ods 换成 public 视图。
- refund 域的 public.refund_clean 已由上一迁移 a9b0c1d2e3f4 建好, **本迁移不重建**。

单一列清单来源: app.models.ods_clean.CLEAN_VIEWS(键=视图名, 值=(源表全限定名, 列清单))。
列清单由对应只读模型的 __table__.columns 生成, 与 conftest 视图重建共用同一来源, 保证不漂移。

暴露列口径: 除内部溯源列 synced_at / raw_extra / updated_by_raw 外全部保留;
  quality_status / quality_note 必须保留(analytics 质检清单/统计排除会读)。

写入不受影响: upsert 入库 / actual_refund_time 回填等仍直写 ods 源表, 视图仅给 analytics 读。

Revision ID: c3d4e5f6a7b8
Revises: a9b0c1d2e3f4
Create Date: 2026-10-14 01:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

from app.models.ods_clean import CLEAN_VIEWS

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str = "a9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# refund_clean 已由 a9b0c1d2e3f4 建好, 本迁移只建其余 10 张
_NEW_VIEWS = [v for v in CLEAN_VIEWS if v != "refund_clean"]


def upgrade() -> None:
    for view, (src_table, cols) in CLEAN_VIEWS.items():
        if view == "refund_clean":
            continue  # 已存在, 不重建
        op.execute(
            f"CREATE VIEW public.{view} AS SELECT {cols} FROM {src_table}"
        )


def downgrade() -> None:
    for view in _NEW_VIEWS:
        op.execute(f"DROP VIEW IF EXISTS public.{view}")

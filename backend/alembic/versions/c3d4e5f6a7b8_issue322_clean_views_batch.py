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

# ⚠️ 源表钉回 #322 当时的 ods 源表 (2026-10-26 空库重放修复)
#
# 背景: 本迁移 `from app.models.ods_clean import CLEAN_VIEWS` —— 而 CLEAN_VIEWS 是
#   **可变的应用常量**。#339 Phase③(58850d3) 把它其中两个键改挂到 std 精简物理表:
#       bms_order_clean -> std_bms_order / cs_record_clean -> std_cs_record
#   于是本历史迁移在**空库重放**时被一起改写了语义: 它会在 std_* 尚未创建之前
#   (std_* 由后续 d9e1f0c2b4a6 建) 就去 `FROM std_bms_order`, 报
#       relation "std_bms_order" does not exist  -> 整条迁移链回滚, 空库无法起飞。
#   生产不受影响(早已越过本版本, 不会重放), 但 CI #211 空库门禁与本地新环境必然失败。
#
# 修法(最小且忠实): 只把被改写过的两个源表钉回 ods —— 与 58850d3 之前的历史行为逐字一致。
#   列集差异无需处理: 这两张视图随后由 0dce885e5a06 DROP+CREATE 用精简列集整表重建,
#   中间态的列数不影响最终结构。
#
# 通用教训: **历史迁移不得依赖可变的应用常量** —— 常量一改, 历史迁移的语义就跟着漂移,
#   且只在"重放"时才暴露。新增迁移请把源表/列清单就地冻结。
_PINNED_ODS_SRC = {
    "bms_order_clean": "ods.bms_orders",
    "cs_record_clean": "ods.cs_records",
}


def upgrade() -> None:
    for view, (src_table, cols) in CLEAN_VIEWS.items():
        if view == "refund_clean":
            continue  # 已存在, 不重建
        src = _PINNED_ODS_SRC.get(view, src_table)
        op.execute(
            f"CREATE VIEW public.{view} AS SELECT {cols} FROM {src}"
        )


def downgrade() -> None:
    for view in _NEW_VIEWS:
        op.execute(f"DROP VIEW IF EXISTS public.{view}")

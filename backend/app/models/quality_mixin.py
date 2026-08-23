"""数据质量字段混入 - 支持通用数据质量审核(L2 自动校验 / L3 人工审核)

任何需要质量审核的业务表继承本 Mixin 即获得:
  quality_status   None=正常(未标记) / ok=已人工确认 / auto_fixed=已自动纠错 / pending=待人工确认
  quality_note     质量说明(自动纠错或待确认原因, 含原始值)

由 services/quality_check.py 的通用校验器在导入时写入, 并在数据质量看板
(web DataQuality.vue) 统一审核。manual_refunds 表内联了同名字段, 不继承本 Mixin。
"""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column


class QualityMixin:
    """数据质量状态字段混入"""

    quality_status: Mapped[str | None] = mapped_column(
        String(16), index=True, nullable=True,
        comment="数据质量状态: None=正常 / ok=已人工确认 / auto_fixed=已自动纠错 / pending=待人工确认",
    )
    quality_note: Mapped[str | None] = mapped_column(
        String(512), nullable=True,
        comment="质量说明(自动纠错或待确认原因, 含原始值)",
    )

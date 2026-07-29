"""报告看板模板 - 可自定义编辑的看板框架定义

设计原则:
- 模板定义 section 列表(key/title/section_type/view_ids/owner_id), 实例化时复制到 WeeklyReport/Report.framework
- 每种 report_type 有一个默认模板(is_default), 一键生成时用
- 预设模板(is_preset)由种子数据维护, 不可删除, 可调优更新
- 实例 framework 是模板的副本, 编辑实例不影响模板

模板 framework section 结构(无 data/summary/related_report_ids, 这些是实例数据):
  {key, title, section_type, view_ids[], owner_id, summary_placeholder}
实例化后(instantiate_framework)补齐为:
  {key, title, section_type, view_ids[], owner_id, summary, data, related_report_ids[]}
"""

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class ReportTemplate(IDMixin, TimestampMixin, Base):
    """报告看板模板(周报/专项报告/审计报告通用)"""

    __tablename__ = "report_templates"

    code: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True, comment="唯一标识(预设用,如 preset_template_weekly)"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="模板名称")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="说明")
    report_type: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True, comment="类型:weekly/special_audit/custom"
    )
    framework: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        comment="section定义 [{key,title,section_type,view_ids[],owner_id,summary_placeholder}]",
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="是否该类型默认模板(一键生成用)"
    )
    is_preset: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="是否预设(不可删)")
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, comment="active/archived")
    visibility: Mapped[str] = mapped_column(
        String(16), default="manager", nullable=False, comment="可见性:public/manager/admin"
    )
    owner_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, comment="所有者")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="排序")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人")

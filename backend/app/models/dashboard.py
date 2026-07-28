"""看板模型 - 决策消费层

Dashboard 把多个 AnalysisView 聚合到一个可配置页面,按工作流/管理目标/权限分组,
面向决策消费。items JSONB 承载布局(数组顺序即展示顺序)。

看板层不重新实现聚合,前端按 items 逐个调 /analysis-views/execute(view_id 模式),
并发执行,单视图失败隔离。

权限双轨:visibility 字段(public/manager/admin)做 API 行级过滤;
路由 meta.manager 做页面级过滤;菜单 v-if=isManager。
"""

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class Dashboard(IDMixin, TimestampMixin, Base):
    """看板 - 一行 = 一个可配置的决策看板

    items 配置示例:
    [
      {"view_id": 12, "view_code": "preset_refund_trend", "title": null, "span": 16, "visible": true},
      {"view_id": 15, "view_code": "preset_refund_status", "title": null, "span": 8, "visible": true}
    ]
    """

    __tablename__ = "dashboards"

    code: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True,
        comment="预设看板代码(preset 才有,如 preset_dashboard_refund)",
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="看板名称")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True,
        comment="看板分类:退款/投诉/接待/绩效/专项/自定义",
    )
    items: Mapped[list] = mapped_column(
        JSONB, nullable=False, comment="看板项数组 [{view_id,view_code,title,span,visible}]",
    )
    time_range_default: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment='默认时间范围 {"days":30}',
    )
    is_preset: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="预设看板(4个常规预设)",
    )
    status: Mapped[str] = mapped_column(
        String(16), default="active", nullable=False, comment="active/archived",
    )
    visibility: Mapped[str] = mapped_column(
        String(16), default="manager", nullable=False,
        comment="可见性:public=全员 / manager=管理层 / admin=仅管理员",
    )
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

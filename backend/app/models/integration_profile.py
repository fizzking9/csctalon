"""接入配置包模板 - 管理员可维护的对外接入「提示词 + 规范」模板

背景 (#44, A-20260910-002 LOCKED):
  调用方(WorkBuddy 等)接入时需要「提示词 + 字段规范 + 接口说明」一整套物料。
  此前这些文本散落在代码常量与 Markdown 文档里, 改一次要发版。
  本表把模板 DB 化, 管理员可在「接入中心」直接维护。

设计:
- 每行 = 一个接入场景的 Markdown 模板(content_md), 支持 {{变量}} 占位。
- variables 声明模板用到哪些变量(label/required), 供前端渲染表单。
- 变量替换**只允许预定义白名单**(见 services/integration_profile_service.py),
  非白名单占位符原样保留(便于在模板里写文档示例), 不做任意表达式求值。
- seed 提供 3 个默认场景; 已存在则跳过不覆盖用户改动(遵循审计项 C-P2-2)。
"""

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class IntegrationProfile(IDMixin, TimestampMixin, Base):
    """接入配置包模板 - 每行一个接入场景"""

    __tablename__ = "integration_profiles"

    code: Mapped[str] = mapped_column(
        String(48), unique=True, index=True, nullable=False,
        comment="唯一标识(如 workbuddy-complaint-push)",
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="显示名")
    scene: Mapped[str] = mapped_column(
        String(24), index=True, nullable=False,
        comment="场景: push=外部推送 / pull=外部拉取 / manual_refund=手动退款推送",
    )
    content_md: Mapped[str] = mapped_column(Text, nullable=False, comment="Markdown 模板(含 {{变量}})")
    variables: Mapped[list | None] = mapped_column(
        JSONB, nullable=True,
        comment='变量声明: [{"key":"doc_url","label":"共享文档地址","required":true}]',
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="停用后不在列表出现")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="排序(小在前)")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人")
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="最后更新人")

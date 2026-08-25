"""数据源分组模型 - 同一 Web 平台(域)下的多个数据源共用登录凭证

设计(2026-08-25):
  - 每个「同步域」一组(如 BMS 主平台 support.cloud-cat.cn、闪猫、SCRM 等)
  - 组级可配置账号密码(加密), 组内单个数据源可覆盖(单源凭证优先)
  - 运行时凭证解析: 单源凭证 > 组凭证 > 种子/.env
  - sync_source_configs.group_key 关联本表
"""

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class SourceGroup(IDMixin, TimestampMixin, Base):
    """数据源分组"""

    __tablename__ = "source_groups"

    group_key: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False,
        comment="组标识: 如 bms-support / bms-wifi / shanmao / scrm / aiacc",
    )
    group_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="组显示名: 如 BMS 主平台 / 闪猫 / SCRM")
    base_url: Mapped[str | None] = mapped_column(String(256), nullable=True, comment="组域名/基础地址")
    username: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="组级登录账号(Fernet加密); 组内单源未配置时继承"
    )
    password: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="组级登录密码(Fernet加密)"
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="展示顺序")

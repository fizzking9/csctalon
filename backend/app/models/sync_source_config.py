"""BMS 同步源运行配置模型 - URL/凭证/开关

与 data_sources(分析元数据)分离:这里只存"如何去 BMS 抓"的运行参数。
账号密码用 Fernet 加密存储(见 app/core/crypto.py)。
"""

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class SyncSourceConfig(IDMixin, TimestampMixin, Base):
    """BMS 同步源运行配置 - 每行一个可同步源(refund/ticket/wallet/device_combo...)"""

    __tablename__ = "sync_source_configs"

    data_type: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, nullable=False, comment="refund/ticket/wallet/device_combo"
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="显示名")
    base_url: Mapped[str] = mapped_column(String(256), nullable=False, comment="BMS 基础地址")
    page_path: Mapped[str] = mapped_column(String(256), nullable=False, comment="页面路径")
    time_labels: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, comment="时间筛选标签(页面上有多个时间框时按标签精确填,如 ticket)"
    )
    username: Mapped[str | None] = mapped_column(Text, nullable=True, comment="BMS 账号(Fernet 加密)")
    password: Mapped[str | None] = mapped_column(Text, nullable=True, comment="BMS 密码(Fernet 加密)")
    default_sync: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, comment="是否纳入定时同步/手动同步默认勾选"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="总开关")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

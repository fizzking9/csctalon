"""投诉数据采集方式开关模型 - 开启哪些接入模式

mode 取值:
  - monitor : 主动监测(腾讯文档等开放平台 API 轮询)
  - push    : 被动推送(WorkBuddy/外部工具 HTTP 推送)
  - import  : 文件导入(手动上传, 无开关意义, 恒可用)

背景: 三种方式都汇入同一投诉台账(指纹去重保证不产生脏数据),
开关用于避免"冗余采集/日志混淆", 管理员可按实际情况只保留一种。
"""

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class IngestModeConfig(IDMixin, TimestampMixin, Base):
    """采集方式开关 - 每行一个模式"""

    __tablename__ = "ingest_mode_configs"

    mode: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, nullable=False, comment="monitor=主动监测 / push=被动推送 / import=文件导入"
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="显示名")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否开启")

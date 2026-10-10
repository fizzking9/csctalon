"""共享文档同步配置模型 - 腾讯文档/金山文档 OAuth 授权与同步参数

与 sync_source_configs(BMS/SCRM)分离:这里存"如何读取云端共享文档"的配置:
  - 开放平台应用凭证(app_id/app_secret, Fernet 加密)
  - 用户 OAuth 授权 token(access/refresh, Fernet 加密)
  - 共享文档链接 + 各业务 Sheet 名(315/卡商投诉)
  - 同步开关 / 轮询间隔 / 最近同步状态(成功水位 last_sync_at + 尝试心跳 last_attempt_at;
    R-P1-16 #241 起分离, 失败只刷 attempt)
每个平台(tencent/wps)一行,通过 platform 唯一区分。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class DocSyncConfig(IDMixin, TimestampMixin, Base):
    """共享文档同步配置 - 每行一个平台(腾讯文档/金山文档)"""

    __tablename__ = "doc_sync_configs"

    platform: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, nullable=False, comment="平台: tencent=腾讯文档 / wps=金山文档"
    )
    name: Mapped[str] = mapped_column(String(64), default="", nullable=False, comment="显示名(平台名)")
    # ── 开放平台应用凭证(Fernet 加密) ──
    app_id: Mapped[str | None] = mapped_column(Text, nullable=True, comment="开放平台应用 ClientID(Fernet加密)")
    app_secret: Mapped[str | None] = mapped_column(Text, nullable=True, comment="开放平台应用 ClientSecret(Fernet加密)")
    redirect_uri: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="OAuth 回调地址")
    # ── 用户 OAuth 授权 token(Fernet 加密) ──
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True, comment="用户访问令牌(Fernet加密)")
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True, comment="用户刷新令牌(Fernet加密,1年有效)")
    open_id: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="授权用户 OpenID")
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="access_token 过期时间"
    )
    # ── 共享文档配置 ──
    doc_url: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="共享表格链接(https://docs.qq.com/sheet/... )")
    file_id: Mapped[str | None] = mapped_column(String(256), nullable=True, comment="解析/转换后的文档 fileID(缓存)")
    external_sheet_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="315外部投诉 Sheet 名(缺省自动找首表)"
    )
    card_sheet_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="卡商群投诉 Sheet 名(缺省自动找首表)"
    )
    # ── 同步控制 ──
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否定时同步")
    sync_interval_minutes: Mapped[int] = mapped_column(Integer, default=20, nullable=False, comment="轮询间隔(分钟)")
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="最近一次尝试时间(无论成败=调度节流心跳; R-P1-16 #241)",
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="最近一次成功同步时间(success/partial/skipped 才刷; failed 保留旧值; R-P1-16)",
    )
    last_sync_status: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="最近同步状态 success/partial/failed")
    last_sync_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="最近同步统计(315/卡商行数等)")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="最近一次同步错误信息")

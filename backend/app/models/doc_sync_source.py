"""共享文档在线采集源配置模型 - 每行 = 一份共享文档(腾讯/金山)

与 doc_sync_configs(每平台 OAuth 开放平台接入一行)不同:
doc_sync_sources 描述"具体某一份共享文档如何采集", 一行一个 doc_url:
  - platform:        tencent=腾讯文档 / kdocs=金山文档
  - doc_url/doc_name: 文档链接与展示名
  - target_type:      complaint=投诉(315+卡商2sheet) / manual_refund=商户号退款
  - sheet_rules:      sheet→目标表映射(JSONB), 如
                      [{sheet:'监督局-315投诉',target:'external_complaint'},...]
  - state_file:       登录态文件名(默认按平台取 tencent_doc.json/kdocs.json)
  - enabled/interval + 最近同步状态(last_sync_at/status/summary/error)

采集方式: 读 settings.BROWSER_STATE_DIR 下登录态 + Playwright headless 打开链接,
触发官方 UI 导出 xlsx → pandas(calamine) 读 Sheet → 中文表头 dict 行,
再复用 complaint_sync / manual_refund_sync 既有入库管线(见 shared_doc_sync_service)。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class DocSyncSource(IDMixin, TimestampMixin, Base):
    """共享文档在线采集源配置 - 每行一份共享文档"""

    __tablename__ = "doc_sync_sources"

    platform: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="平台: tencent=腾讯文档 / kdocs=金山文档"
    )
    doc_url: Mapped[str] = mapped_column(
        String(512), nullable=False, comment="共享文档链接"
    )
    doc_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="文档名(展示)"
    )
    target_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="目标业务类型: complaint=投诉(315+卡商2sheet) / manual_refund=商户号退款",
    )
    sheet_rules: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="sheet→data_type映射, 如 [{'sheet':'监督局-315投诉','target':'external_complaint'},{'sheet':'卡商群投诉','target':'card_complaint'}]",
    )
    state_file: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="登录态文件名(默认 tencent_doc.json/kdocs.json)"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, comment="启用; false=停用不采集"
    )
    sync_interval_minutes: Mapped[int] = mapped_column(
        Integer, default=60, nullable=False, comment="轮询间隔(分钟)"
    )
    # ── 最近同步状态(模式参考 doc_sync_configs) ──
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近同步时间(UTC)"
    )
    last_sync_status: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="最近同步状态 success/partial/failed"
    )
    last_sync_summary: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment="最近同步统计(各sheet行数/增改等)"
    )
    last_error: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="最近一次同步错误信息"
    )

    __table_args__ = (
        # 同一平台同一链接只保留一份采集配置
        UniqueConstraint("platform", "doc_url", name="uq_doc_sync_sources_platform_doc_url"),
    )

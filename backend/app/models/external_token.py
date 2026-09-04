"""外部只读访问令牌模型 - workbuddy 等外部工具按 scope 拉取只读数据

设计(见 docs/arch/A-20260902-021-外部工具只读开放平台.md LOCKED v1.0):
- 系统管理员在系统内生成 token, 分发给外部调用方(如 workbuddy 每半小时拉简报)。
- token 明文只在生成时展示一次; 库中仅存 SHA-256 hash + token_prefix(前8位, 管理界面识别)。
- scopes(JSONB): 权限范围集合, 一期: reception(接待)/refund(退款)/complaint(投诉)/brief(简报)。
- 吊销 = enabled=False(软删, 即时生效); 不套 SCD Type2(无需保留历史版本)。
- rate_limit_per_min: 每 token 个性化限频(Redis 固定窗口), NULL = 用全局默认。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class ExternalAccessToken(IDMixin, TimestampMixin, Base):
    """外部只读访问令牌 - 每行一个外部调用方 token"""

    __tablename__ = "external_access_tokens"

    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="调用方标识(如 workbuddy-简报), 审计/管理界面显示")
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False, comment="SHA-256(token), 不存明文"
    )
    token_prefix: Mapped[str] = mapped_column(String(12), nullable=False, comment="明文前 8 位, 管理界面识别用")
    scopes: Mapped[list | None] = mapped_column(
        JSONB, nullable=False, comment="权限范围: reception/refund/complaint/brief, 可扩展"
    )
    rate_limit_per_min: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="每 token 个性化限频(次/分钟); NULL=用全局默认"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="总开关: false=已吊销(软删)")
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True, comment="可选过期时间"
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最后使用时间(管理界面看活跃度)"
    )
    owner_note: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="责任人/用途备注(可选, F-P2-5)"
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人账号")
    revoked_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="吊销人账号")

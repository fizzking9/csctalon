"""统一变更审计模型 (S-P1-11 / #261 B4-5)

参照 zammad histories / helpdesk TicketChange 的统一历史表范式:
任何业务实体的人工/系统变更, 以「实体 + 动作 + 字段级 before/after」落一行,
与 operation_logs (HTTP 层审计) 互补 —— 前者回答"这个字段谁改成了什么",
后者回答"谁在什么时候打了哪个接口"。

设计取舍:
- entity_id 统一字符串(兼容 int 主键与复合标识), 查询按 entity_type + entity_id 走索引
- changes JSONB: {field: [before, after]}; create 动作 before=None; delete 动作 after=None
- actor 冗余 username —— 用户被删后仍可审计(与 operation_logs 同纪律)
- 保留期 730 天(settings.AUDIT_CHANGE_RETENTION_DAYS), 由夜批 purge_expired_logs 清理
- 分区方案(按月原生分区)设计先行未执行: 见 docs/arch/A-20261008-003-统一变更审计与日志保留期.md
"""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class AuditChange(IDMixin, TimestampMixin, Base):
    """统一变更审计 - created_at 即变更发生时间"""

    __tablename__ = "audit_changes"
    __table_args__ = {"comment": "统一变更审计: 实体字段级 before/after(#261 B4-5)"}

    entity_type: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, comment="实体类型(表名, 如 persons/manual_refunds)"
    )
    entity_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, comment="实体主键(统一字符串)"
    )
    action: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="动作: create/update/delete"
    )
    actor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, comment="操作人ID(系统触发为空)"
    )
    actor_name: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="操作人账号(冗余,用户删除后仍可审计)"
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, index=True, comment="来源: api/night_batch/manual_import/system"
    )
    changes: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment="字段级变更: {field: [before, after]}"
    )
    summary: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="人类可读摘要(如 张三: 部门 A→B, 状态 在职→离职)"
    )

"""共享文档登录历史日志 - 每次页面内扫码登录的结果审计

记录某次登录会话的最终结果(成功/失败), 供运维/管理员查看
「谁在何时用什么账号登录成功/失败」, 定位登录态被覆盖或失效的时序。

- result: success=校验通过+登录态已保存 / failed=校验未通过或保存失败
- uid: 脱敏账号标识(腾讯取 uin/uid cookie, 金山取 __WPSENV__.user.id; 取不到为空)
- cookies_count: 保存成功后的 cookie 条数; 失败时为 0
- 保留 ≥90 天, 写日志时顺带清理超期记录(后台任务用 /login/logs 前的简单清理)
"""
from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class DocSyncLoginLog(IDMixin, TimestampMixin, Base):
    """共享文档登录历史 - 一行 = 一次登录会话的最终结果"""

    __tablename__ = "doc_sync_login_logs"

    platform: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True, comment="平台: tencent=腾讯 / kdocs=金山"
    )
    source_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="触发登录的采集源 id(可为空: CLI 上传登录态)"
    )
    uid: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="登录账号(脱敏, 取不到为空)"
    )
    cookies_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="保存成功后的 cookie 条数(失败为 0)"
    )
    result: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="success=登录态已保存 / failed=校验或保存失败"
    )
    error: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="失败原因(成功为空)"
    )

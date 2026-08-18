"""微信扫码注册相关模型

设计思路:
- 用户在微信/小程序打开登录页 → 前端调用 wx.login() 获取 code → 后端 code2session 拿到 openid/unionid
- openid 关联到 users 表, 首次扫码自动创建一条待审批的注册申请 (WechatRegistration)
- 管理员在后台"用户管理→注册审批"页面审批通过/驳回, 通过后自动激活用户并分配角色/分组
- 已绑定用户再次扫码 → 直接签发 JWT 登录

小提示:
- 小程序使用 openid 作为唯一标识
- 如同时需支持公众号扫码, 使用 unionid 作为跨应用统一标识
- 审批通过后可根据 qw_name (企业微信姓名) 自动匹配 Person 维表, 便于绩效/看板对齐
"""

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class RegisterStatus(str, enum.Enum):
    PENDING = "pending"        # 等待管理员审批
    APPROVED = "approved"      # 审批通过, 已创建用户
    REJECTED = "rejected"      # 审批驳回
    CANCELLED = "cancelled"    # 用户主动取消/超时


class RegisterSource(str, enum.Enum):
    MINIAPP = "miniapp"        # 微信小程序
    OFFICIAL_ACCOUNT = "oa"    # 微信公众号
    QR_LOGIN = "qr"            # PC 扫码登录


class WechatRegistration(IDMixin, TimestampMixin, Base):
    """微信扫码注册申请(待审批)

    生命周期:
      1. 用户首次扫码 → status=pending, 前端显示"已提交,等待管理员审批"
      2. 管理员在审批页面 approve → 自动创建 User, 激活账号, 分配 role/group
      3. 已注册用户再次扫码 → 走直接登录流程, 不再产生新申请
    """

    __tablename__ = "wechat_registrations"

    # 微信侧身份标识
    openid: Mapped[str] = mapped_column(String(64), index=True, nullable=False, comment="微信 openid(小程序/公众号)")
    unionid: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="微信 unionid(跨应用统一)")
    nickname: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="微信昵称")
    avatar: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="微信头像URL")
    source: Mapped[str] = mapped_column(String(16), default=RegisterSource.MINIAPP.value, nullable=False)

    # 用户填写的资料 + 审批结果
    desired_username: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="期望登录账号(审批通过后作为 username)")
    desired_display_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="期望显示名")
    desired_role: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="期望角色(由管理员最终确定)")
    desired_group: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="期望分组")
    qw_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="企微姓名(用于 Person 维表自动匹配)")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="申请备注")

    status: Mapped[str] = mapped_column(String(16), default=RegisterStatus.PENDING.value, index=True, nullable=False)
    reviewed_at: Mapped["datetime | None"] = mapped_column(DateTime, nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, comment="审批人 user_id")
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True, comment="驳回原因")

    # 审批通过后创建的用户 ID
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True, comment="关联 users.id")

    # 是否订阅小程序消息通知
    subscribed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class WechatAccount(IDMixin, Base):
    """微信账号与 User 的绑定关系

    一个 User 可绑定多个微信身份 (如同时绑定小程序和公众号).
    """

    __tablename__ = "wechat_accounts"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    openid: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    unionid: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default=RegisterSource.MINIAPP.value, nullable=False)
    bound_at: Mapped["datetime"] = mapped_column(DateTime, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

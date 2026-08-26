"""用户与角色模型"""

import enum

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class RoleEnum(str, enum.Enum):
    """五级角色(规划设计 v0.5 §二)

    系统管理员 admin / 主管 supervisor / 专项组长 specialty_lead / 专员 specialist / 普通客服 agent。
    数据范围: admin=全量; supervisor=全部组+全部专项; specialty_lead=本专项(业务专项负责人);
    specialist=本人+被指派专项; agent(普通客服)=仅本人。
    """

    ADMIN = "admin"  # 系统管理员:全量
    SUPERVISOR = "supervisor"  # 主管:全部组+全部专项
    SPECIALTY_LEAD = "specialty_lead"  # 专项组长:本专项(业务专项负责人)
    SPECIALIST = "specialist"  # 专员:本人+被指派专项
    AGENT = "agent"  # 普通客服:仅本人


# 管理层角色(可看决策看板/管理闭环): 管理员 + 主管 + 专项组长
MANAGER_ROLES = (RoleEnum.ADMIN.value, RoleEnum.SUPERVISOR.value, RoleEnum.SPECIALTY_LEAD.value)


class User(IDMixin, TimestampMixin, Base):
    """用户表 - Web 账密登录"""

    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    """登录账号"""
    display_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="显示姓名")
    hashed_password: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default=RoleEnum.AGENT.value, nullable=False, comment="角色:admin/supervisor/specialty_lead/specialist/agent")
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="所属组(主管用)")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 绑定企微姓名(用于匹配数据中的"企微-XXX"操作人)
    qw_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="企业微信姓名")
    # JWT 吊销版本号(M7): 签发 token 时写入 tkv, 校验时与该值比对;
    # 改密/登出时 +1, 使所有已签发 token 立即失效。0=初始。
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0", comment="JWT吊销版本号")
    # 微信侧: 便于扫码登录时快速查询 (openid/unionid 真正的多对多关系见 WechatAccount)
    wechat_openid: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="绑定的主微信 openid")
    wechat_unionid: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="绑定的主微信 unionid")


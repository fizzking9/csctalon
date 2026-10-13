"""SCRM 客服人员(人设)信息独立表

与现有的 ``scrm_operators`` (聚合平台内部客服快照,含 operator_id/Person 归一) 区别:
  * 本模型存储的是 **白音企微 SCRM /wxWork/personalityManagement 页面**解析出的「人设信息」,
    包含 企微人设昵称(含@企业) / 实名 / 别名(微信号) / 对外展示名 / 性别 / 设备类型 /
    自动通过好友请求 / 通过群聊验证 / 人设模板 / 新客运营模板 / 朋友圈封面 等。
  * 双外键归一化匹配:
        - person_id  → persons.id (统一人员实体,推荐主链路)
        - user_id    → users.id  (本地 Web 登录账号,可选,便于快速定位)
  * 每次全量同步时按 ``(source_config_id, wecom_nick, enterprise_suffix)``
    或唯一键 ``staff_unique_key`` upsert 覆盖更新。
"""
from __future__ import annotations

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class ScrmStaffProfile(IDMixin, TimestampMixin, Base):
    """SCRM 人设信息 - 每行一条 SCRM 企微号人设档案"""

    __tablename__ = "scrm_staff_profiles"

    # -------- 唯一性: (源配置 + 企微人设名@企业) --------
    source_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("sync_source_configs.id", ondelete="SET NULL"),
        index=True, nullable=True,
        comment="来源 sync_source_configs.id(白音 SCRM 网页登录源)",
    )
    wecom_nick: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="企微人设昵称 如 '小苏（苏紫依）'",
    )
    enterprise_suffix: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True, comment="所属企业后缀(从 @拆分) 如 '自在联'/'云客猫'",
    )
    staff_unique_key: Mapped[str] = mapped_column(
        String(256), unique=True, index=True, nullable=False,
        comment="业务唯一键 = md5(source_config_id or 0 + '|' + wecom_nick + '|' + (enterprise_suffix or ''))",
    )

    # -------- 归一化匹配 --------
    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("persons.id", ondelete="SET NULL"), index=True, nullable=True,
        comment="归一化归属 Person;null=未匹配/待人工确认",
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True,
        comment="直接绑定本地 Web 登录账号 users.id; 可选",
    )
    match_status: Mapped[str] = mapped_column(
        String(16), default="unmatched", nullable=False, index=True,
        comment="匹配状态: unmatched=未匹配 / auto_matched=自动匹配 / manual_confirmed=人工确认 / rejected=排除",
    )
    match_score: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="自动匹配评分(越高越像),供人工确认参考",
    )
    match_reason: Mapped[str | None] = mapped_column(
        String(256), nullable=True, comment="自动匹配命中的规则说明",
    )

    # -------- 分组 / 企业 --------
    company_group: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True,
        comment="所属企业/分组 (页面 分组列) 如 '深圳市云客猫科技有限公司'",
    )

    # -------- 在线状态 --------
    online_status: Mapped[str | None] = mapped_column(
        String(16), index=True, nullable=True,
        comment="在线状态: online=在线 / offline=离线 / busy=忙碌 等(原文保留)",
    )

    # -------- 身份信息 --------
    real_name: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="实名 (页面 实名 列)",
    )
    alias_name: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="别名/微信号 (页面 别名 列 如 '随身WiFi-小苏')",
    )
    external_display_name: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="对外展示名 (客户侧看到的昵称)",
    )
    gender: Mapped[str | None] = mapped_column(
        String(8), nullable=True, comment="性别: 男/女/未设置",
    )

    # -------- 设备 / 账号属性 --------
    device_type: Mapped[str | None] = mapped_column(
        String(32), index=True, nullable=True,
        comment="设备类型: ipad扫码号 / 手机 / PC 网页等(页面 类型 列)",
    )
    auto_accept_friend: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="自动通过好友请求: 已开启/已关闭/未设置",
    )
    auto_accept_group_verify: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="通过群聊验证: 已开启/已关闭/未设置",
    )

    # -------- 运营配置(模板名/封面,暂存字符串原文,不深度解析) --------
    moments_cover: Mapped[str | None] = mapped_column(
        String(256), nullable=True, comment="朋友圈封面 (文件名或 URL,原文保留)",
    )
    persona_template: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="人设模板名称",
    )
    new_customer_template: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="新客运营模板名称",
    )

    # -------- 抓取元数据 --------
    last_synced_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True,
        comment="最近一次同步抓取时间",
    )
    sync_source_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="来源源名(冗余,便于前端展示)",
    )

    # -------- 原始数据冗余 --------
    raw_extra: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment="页面解析出的原始行(中文列名)冗余,方便后续扩字段",
    )
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ------ 索引 / 唯一约束 ------
    __table_args__ = (
        UniqueConstraint("source_config_id", "wecom_nick", "enterprise_suffix",
                         name="uq_scrm_staff_src_nick_ent"),
        Index("ix_scrm_staff_real_name_alias", "real_name", "alias_name"),
        Index("ix_scrm_staff_company_status", "company_group", "online_status"),
        # #319 数据分层: 信源表(SCRM 同步员工档案)归入 ods schema
        {"schema": "ods"},
    )

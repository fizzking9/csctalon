"""SCRM 会话主表(私聊 + 群聊 + 虚拟会话 合并一张,通过 session_type 区分)

核心价值:给绩效看板提供 时间×人员×响应时长×会话量 的统计粒度。
聊天记录明细(量大)按用户要求暂不入库,需要时通过 ScrmClient.get_chat_messages 实时拉。
但关键绩效指标(首响、均响、会话时长、消息数、转接次数)**在入库时预计算好**写入本表,
避免后续每次看板都去实时拉聊天记录。
"""

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class ScrmSession(IDMixin, TimestampMixin, Base):
    """SCRM 客服接待会话(三合一:private/group/virtual 用 session_type 区分)"""

    __tablename__ = "scrm_sessions"
    __table_args__ = {"schema": "ods"}

    # ---- 业务唯一键: SCRM 侧 sessionId。分 session_type 仍可能撞,因此全局唯一 ----
    session_id: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False, comment="SCRM 会话 ID(业务唯一键)"
    )
    session_type: Mapped[str] = mapped_column(
        String(16), index=True, nullable=False,
        comment="会话类型:private=私聊;group=群聊;virtual=虚拟会话(来自 SCRM 三个列表接口)"
    )

    # ---- 归属客服(人维度统计) ----
    operator_id: Mapped[str | None] = mapped_column(
        String(128), index=True, nullable=True, comment="SCRM 客服ID → scrm_operators.operator_id"
    )
    operator_name: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="客服显示名原文(用于别名归一:PERSON_FIELD_SOURCES)"
    )
    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("persons.id", ondelete="SET NULL"), index=True, nullable=True,
        comment="别名归一后归属 Person；null 表示待归并"
    )

    # ---- 客户侧(后续做"接待人数""复聊率"需要,customer_id 可空) ----
    customer_id: Mapped[str | None] = mapped_column(
        String(128), index=True, nullable=True, comment="SCRM 客户 ID/外显微信 ID(如有)"
    )
    customer_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="客户昵称/备注"
    )
    group_id: Mapped[str | None] = mapped_column(
        String(128), index=True, nullable=True, comment="群聊ID(仅群聊会话有)"
    )
    group_name: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # ---- 时间(看板核心维度) ----
    start_time: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True, comment="会话开始时间"
    )
    end_time: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True, comment="会话结束时间(空=仍在进行)"
    )
    duration_sec: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="会话时长(秒):end-start 预计算,避免现场算"
    )

    # ---- 工作量统计(预计算) ----
    msg_count_customer: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="客户消息数(列表接口能直接给就填,否则 0 或同步时扫接口汇总)"
    )
    msg_count_staff: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="客服回复消息数"
    )

    # ---- 工作质量核心指标(入库时预计算,不依赖聊天记录明细) ----
    first_response_sec: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="首次响应时长(秒): 客户首条消息 → 客服首条回复"
    )
    avg_response_sec: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="平均响应时长(秒):所有客消息→下一条服消息的均值"
    )
    transfer_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="转接次数(0=未转接;1+=转接N次)"
    )

    # ---- 问题类型(后续难度系数得分:咨询×1.0/套餐×1.8/退款×2.2/投诉×3.0) ----
    question_type_id: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="SCRM 问题类型ID(如有 getQuestionOverTypeList)"
    )
    question_type_name: Mapped[str | None] = mapped_column(
        String(128), index=True, nullable=True, comment="问题类型名称原文"
    )

    # ---- 对账/状态 ----
    sync_status: Mapped[str] = mapped_column(
        String(16), default="open", index=True, nullable=False,
        comment="open=进行中;closed=已结束;reconciled=已对账确认"
    )
    # 若接口未直接提供质量字段,则 messages_fetched 标记是否已懒拉取聊天记录计算过质量
    quality_computed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="首响/均响/时长 是否已计算完成"
    )

    raw_extra: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment="原始会话 JSON 冗余(便于后续加字段不用重拉)"
    )


# 复合索引(看板查询高概率用到的组合)——在 alembic 里显式建:
#   ix_scrm_sessions_person_start    (person_id, start_time)    : 某客服在某时段
#   ix_scrm_sessions_type_start      (session_type, start_time) : 类型趋势
#   ix_scrm_sessions_qtype_start     (question_type_name, start_time): 问题类型分布

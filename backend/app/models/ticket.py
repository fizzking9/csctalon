"""服务退款工单模型 - 服务工单,主键:工单号

记录退款原因及操作人(人员绩效来源)
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin
from app.models.quality_mixin import QualityMixin


class ServiceTicket(QualityMixin, TimestampMixin, Base):
    """服务退款工单"""

    __tablename__ = "service_tickets"

    ticket_no: Mapped[str] = mapped_column(String(64), primary_key=True, comment="工单号 GDC/GD...")
    device_sn: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("devices.sn"), index=True, nullable=True, comment="SN"
    )
    device_type: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备类型")
    device_type_remark: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="设备类型备注")
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="手机号")
    service_category: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True, comment="服务类别:退款")
    service_item: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="服务项:网慢/网卡...")
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="优先级")
    status: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True, comment="状态:待处理/已完成")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")
    register_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True, comment="登记时间")
    active_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="激活时间")
    dispatch_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="分发时间")
    finish_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True, comment="完成时间")
    handle_duration: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="处理时长")
    registrant: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="登记人")
    channel: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="录入渠道:H5")
    handle_node: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="处理节点")
    handler: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="处理人:企微-XXX(绩效来源)")
    handle_opinion: Mapped[str | None] = mapped_column(Text, nullable=True, comment="处理意见")
    is_appeal: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="是否申诉")
    order_no: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="订单编号")
    submit_params: Mapped[str | None] = mapped_column(Text, nullable=True, comment="提交参数(JSON)")
    refund_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True, comment="退款金额")
    estimated_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True, comment="预估可退金额")
    # ── 催单 & 人员分派新字段(2026-08-03 加, nullable, 不影响历史数据) ──
    urge_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="催单次数")
    urge_status: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="催单状态")
    last_dispatch_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最后派单时间"
    )
    assignee: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="分配人员")
    contact_person: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="联系人员")
    dispatcher: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="派发人员")
    last_urge_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="最后催单人")
    last_urge_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最后催单时间"
    )
    # ── 同步溯源 & 更新人拆分 (2026-08-06 新口径 P1-T4) ──
    # synced_at: 本条记录最后一次被「导入/同步任务」写回 DB 的时间 (每次 upsert 强制 now()),
    #   用于区分「上游 BMS/SCRM 真的改了这张单」还是「我们的同步任务每天扫表覆盖」,
    #   区别于 TimestampMixin.updated_at (所有 ORM save 都会改) 和 finish_time (89% 被批量覆盖不可靠)。
    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True,
        comment="最后一次被导入/同步任务写入时间(每次 upsert 强制 now())",
    )
    # updated_by_raw: 上游 BMS/SCRM 导出的「更新人/最后操作人」原样保留,
    #   不和 handler / operator / auditor 这些业务意义的人员字段混合,
    #   用于在「更新时间不可靠」的情况下, 反向追溯「最后是谁在上游改了这张单」。
    updated_by_raw: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True,
        comment="上游(BMS/SCRM)原始更新人/最后操作人,原样导入不参与业务逻辑",
    )

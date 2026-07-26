"""服务退款工单模型 - 服务工单,主键:工单号

记录退款原因及操作人(人员绩效来源)
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class ServiceTicket(TimestampMixin, Base):
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
    register_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="登记时间")
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

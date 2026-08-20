"""外部投诉模型 - 监督局/315投诉记录

来源:315投诉.xlsx Sheet「监督局-315投诉」。记录外部投诉(12315平台/监督局)明细,
是周报「投诉专项-外部投诉」的源头数据。投诉分类字段对应周报的类别分布
(套餐/计费、网络/技术、退款/售后、充值/规则、其他)。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class ExternalComplaint(IDMixin, TimestampMixin, Base):
    """外部投诉(监督局/315)"""

    __tablename__ = "external_complaints"

    complaint_no: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True,
        comment="投诉编号(同一编号可对应多条投诉, 去重以 dedup_hash 指纹为准)",
    )
    complaint_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True, comment="投诉日期(主时间字段)"
    )
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="平台")
    complaint_account: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="投诉账号")
    company_entity: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="公司主体")
    complaint_shop: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="投诉店铺")
    purchase_order_no: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True, comment="购买订单编号")
    complainant: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="投诉人")
    complainant_phone: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True, comment="投诉手机号")
    device_sn: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("devices.sn"), index=True, nullable=True, comment="投诉设备号 SN"
    )
    wechat_id: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="微信账户id")
    problem: Mapped[str | None] = mapped_column(Text, nullable=True, comment="投诉问题")
    keyword: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True, comment="投诉关键词")
    complaint_category: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="投诉分类:套餐/计费、网络/技术、退款/售后、充值/规则、其他"
    )
    solution: Mapped[str | None] = mapped_column(String(512), index=True, nullable=True, comment="解决方案")
    result: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="结果:投诉无效/有效...")
    follower: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="跟进人(人员别名来源)")
    handle_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="处理时间")
    handle_detail: Mapped[str | None] = mapped_column(Text, nullable=True, comment="处理明细")
    matched_ticket_no: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="关联服务工单号(按设备SN匹配外部投诉类工单)"
    )
    matched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="关联工单时间"
    )
    dedup_hash: Mapped[str | None] = mapped_column(
        String(32), unique=True, index=True, nullable=True, comment="业务去重指纹(md5):投诉日期+手机号+SN+归一化问题;不含编号(上游易变)"
    )

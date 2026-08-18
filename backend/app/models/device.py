"""设备主数据与标签模型 - 领域核心"""

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Table, Column, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin

# 设备-标签 多对多关联表
device_tag_rel = Table(
    "device_tag_rel",
    Base.metadata,
    Column("device_sn", String(64), ForeignKey("devices.sn"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("device_tags.id"), primary_key=True),
    Column("created_at", DateTime(timezone=True), server_default="now()", nullable=False),
    Column("created_by", String(64), nullable=True, comment="打标人"),
    # v0.5 增强(规划设计 §4.3): 标签批次 + 生命周期
    Column("batch_id", Integer, nullable=True, comment="来源批次(device_tag_batches.id)"),
    Column("status", String(16), server_default="active", nullable=False, comment="active/inactive"),
    Column("expired_at", DateTime(timezone=True), nullable=True, comment="失效时间"),
)


class Device(TimestampMixin, Base):
    """设备主表 - 贯穿所有业务表的领域核心"""

    __tablename__ = "devices"

    sn: Mapped[str] = mapped_column(String(64), primary_key=True, comment="设备SN")
    device_type: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备类型")
    device_type_remark: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="设备类型备注")
    active_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="激活时间")
    merchant_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="所属商户名称")
    status: Mapped[str | None] = mapped_column(String(32), nullable=True, default="正常", comment="设备状态")

    tags: Mapped[list["DeviceTag"]] = relationship(secondary=device_tag_rel, lazy="selectin")


class DeviceTag(IDMixin, TimestampMixin, Base):
    """设备标签 - 预设(清退/涨价/限速) + 自定义"""

    __tablename__ = "device_tags"

    tag_name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    tag_type: Mapped[str] = mapped_column(String(32), default="preset", comment="preset预设/custom自定义")
    color: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="标签颜色")
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)


class DeviceTagBinding(Base):
    """设备-标签绑定明细(与 device_tag_rel 同表,用于 ORM upsert 和数据浏览)

    同表双映射: device_tag_rel 作为多对多 secondary 使用;
    DeviceTagBinding 作为独立行级 upsert/数据浏览用的 ORM 模型。
    注意: 必须加 extend_existing=True,否则 SQLAlchemy 会报错「表已在 MetaData 中定义」。
    注意: 不继承 TimestampMixin — 因为真实的 device_tag_rel 表没有 updated_at 列;
         created_at 单独声明,与关联表中的 created_at 同列复用。
    """

    __tablename__ = "device_tag_rel"
    __table_args__ = {"extend_existing": True}

    device_sn: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.sn"), primary_key=True, comment="设备SN"
    )
    tag_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("device_tags.id"), primary_key=True, comment="标签ID"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False, comment="打标时间"
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="打标人")
    batch_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="来源批次(device_tag_batches.id)")
    status: Mapped[str] = mapped_column(
        String(16), server_default="active", nullable=False, comment="active/inactive"
    )
    expired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="失效时间"
    )


class DeviceTagBatch(IDMixin, TimestampMixin, Base):
    """设备专项标签批量导入批次(规划设计 §4.3.1)"""

    __tablename__ = "device_tag_batches"

    batch_no: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False, comment="批次号"
    )
    tag_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("device_tags.id"), index=True, nullable=False, comment="打标标签"
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="批次标题")
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="来源文件名")
    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="提交总数")
    matched_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="成功打标数")
    note: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人")

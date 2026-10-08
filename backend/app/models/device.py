"""设备主数据与标签模型 - 领域核心"""

from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class DimDeviceType(Base):
    """设备类型维度表 (#246 B3-1, A-20261007-002 §3.2)

    把 devices.device_type 自由文本归一为稳定维度码; 原始文本保留在
    devices.device_type, 本表 normalized_from 记录归并来源(快照可回溯)。
    """

    __tablename__ = "dim_device_type"

    type_code: Mapped[str] = mapped_column(String(32), primary_key=True, comment="归一类型码(如 4G_CPE)")
    type_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="类型中文名")
    normalized_from: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="归并来源原始文本列表(快照)"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", comment="active/disabled")

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
    # #249 B3-3 (A-20261007-002 §3.6): 业务事实来源(哪个投诉/质检行触发的打标)
    Column("source_table", String(32), nullable=True, comment="来源表名(可空, 老批次数据为 NULL)"),
    Column("source_id", String(64), nullable=True, comment="来源行主键值(可空)"),
    Column("reason", Text, nullable=True, comment="打标原因(可空)"),
)


class Device(TimestampMixin, Base):
    """设备主表 - 贯穿所有业务表的领域核心"""

    __tablename__ = "devices"

    sn: Mapped[str] = mapped_column(String(64), primary_key=True, comment="设备SN")
    device_type: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备类型")
    device_type_code: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("dim_device_type.type_code"),
        nullable=True,
        index=True,
        comment="归一设备类型码(#246 B3-1, 原始 device_type 文本保留)",
    )
    device_type_remark: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="设备类型备注")
    active_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="激活时间")
    merchant_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="所属商户名称")
    status: Mapped[str | None] = mapped_column(String(32), nullable=True, default="正常", comment="设备状态")

    # ── BMS 设备管理档案扩展(P4, A-20260905-001 / #18, 2026-09-05) ──
    main_merchant_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="主商户名称(设备管理CSV: 主商户)"
    )
    active_flag: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="是否激活(已激活/未激活)")
    service_valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="服务有效期至"
    )
    current_combo_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="当前套餐"
    )
    start_iccid: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="启动卡ICCID")
    virtual_iccid: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="虚拟卡ICCID")
    app_version: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="应用版本")
    region: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="区域")
    batch_no: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="批次号")

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
    # #249 B3-3 (A-20261007-002 §3.6): 业务事实来源
    source_table: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="来源表名")
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="来源行主键值")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True, comment="打标原因")


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

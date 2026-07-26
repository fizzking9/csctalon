"""卡商群投诉模型 - 卡商群内的投诉记录

来源:315投诉.xlsx Sheet「卡商群投诉」。结构较简单,无天然唯一键,
采用 IDMixin 自增主键 + pk_strategy=none,导入时简单 insert(on_conflict_do_nothing 兜底),
与 cs_record/wallet 一致。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class CardComplaint(IDMixin, TimestampMixin, Base):
    """卡商群投诉"""

    __tablename__ = "card_complaints"

    complaint_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True, comment="投诉日期(主时间字段)"
    )
    complaint_group: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="投诉群")
    complainant: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="投诉人")
    complainant_phone: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True, comment="投诉手机号")
    device_sn: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("devices.sn"), index=True, nullable=True, comment="设备号 SN"
    )
    problem: Mapped[str | None] = mapped_column(Text, nullable=True, comment="投诉问题")
    problem_category: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="投诉问题分类(二级)")
    result: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True, comment="结果")
    solution: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True, comment="解决方案")
    handler: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="处理人(人员别名来源)")
    handle_result: Mapped[str | None] = mapped_column(Text, nullable=True, comment="处理结果")

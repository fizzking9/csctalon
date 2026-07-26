"""模型汇总 - 导入所有模型,供 Alembic 与应用初始化使用"""

from app.models.analysis_view import AnalysisView, ClassificationRule  # noqa: F401
from app.models.base import IDMixin, TimestampMixin  # noqa: F401
from app.models.cs_record import CsRecord  # noqa: F401
from app.models.data_source import DataSource, DataSourceField, SemanticRole, SourceType  # noqa: F401
from app.models.device import Device, DeviceTag, device_tag_rel  # noqa: F401
from app.models.merchant import Merchant  # noqa: F401
from app.models.merchant_refund import MerchantRefund  # noqa: F401
from app.models.person import Person, PersonAlias  # noqa: F401
from app.models.refund import Refund  # noqa: F401
from app.models.strategy import StrategyDoc, StrategyReadReceipt  # noqa: F401
from app.models.task import SpecialTask, TaskDevice, TaskFollowup  # noqa: F401
from app.models.ticket import ServiceTicket  # noqa: F401
from app.models.todo import TodoFollowup, TodoItem  # noqa: F401
from app.models.upload import DataMatchRelation, UploadRecord  # noqa: F401
from app.models.user import RoleEnum, User  # noqa: F401
from app.models.wallet import WalletWithdrawal  # noqa: F401

__all__ = [
    "AnalysisView",
    "ClassificationRule",
    "CsRecord",
    "Device",
    "DeviceTag",
    "Merchant",
    "MerchantRefund",
    "Refund",
    "ServiceTicket",
    "WalletWithdrawal",
    "UploadRecord",
    "DataMatchRelation",
    "User",
    "RoleEnum",
    "TodoItem",
    "TodoFollowup",
    "SpecialTask",
    "TaskDevice",
    "TaskFollowup",
    "StrategyDoc",
    "StrategyReadReceipt",
    "DataSource",
    "DataSourceField",
    "SemanticRole",
    "SourceType",
    "Person",
    "PersonAlias",
    "IDMixin",
    "TimestampMixin",
]

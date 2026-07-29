"""模型汇总 - 导入所有模型,供 Alembic 与应用初始化使用"""

from app.models.analysis_view import AnalysisView, ClassificationRule  # noqa: F401
from app.models.audit_note import AuditJudgment, AuditNote  # noqa: F401
from app.models.base import IDMixin, TimestampMixin  # noqa: F401
from app.models.bms_sync_log import BmsSyncLog  # noqa: F401
from app.models.card_complaint import CardComplaint  # noqa: F401
from app.models.cs_record import CsRecord  # noqa: F401
from app.models.dashboard import Dashboard  # noqa: F401
from app.models.data_source import DataSource, DataSourceField, SemanticRole, SourceType  # noqa: F401
from app.models.device import Device, DeviceTag, device_tag_rel  # noqa: F401
from app.models.device_combo import DeviceCombo  # noqa: F401
from app.models.external_complaint import ExternalComplaint  # noqa: F401
from app.models.incident import Incident  # noqa: F401
from app.models.measure import Measure  # noqa: F401
from app.models.merchant import Merchant  # noqa: F401
from app.models.merchant_refund import MerchantRefund  # noqa: F401
from app.models.operation_log import OperationLog  # noqa: F401
from app.models.person import Person, PersonAlias  # noqa: F401
from app.models.refund import Refund  # noqa: F401
from app.models.report import Report, ReportStatus, ReportType  # noqa: F401
from app.models.report_comment import CommentTarget, ReportComment  # noqa: F401
from app.models.strategy import StrategyDoc, StrategyReadReceipt  # noqa: F401
from app.models.sync_source_config import SyncSourceConfig  # noqa: F401
from app.models.task import SpecialTask, TaskDevice, TaskFollowup  # noqa: F401
from app.models.ticket import ServiceTicket  # noqa: F401
from app.models.todo import TodoFollowup, TodoItem  # noqa: F401
from app.models.upload import DataMatchRelation, UploadRecord  # noqa: F401
from app.models.user import RoleEnum, User  # noqa: F401
from app.models.wallet import WalletWithdrawal  # noqa: F401
from app.models.weekly_report import WeeklyReport, WeeklyReportStatus  # noqa: F401
from app.models.weekly_section_binding import WeeklySectionBinding  # noqa: F401

__all__ = [
    "AnalysisView",
    "AuditJudgment",
    "AuditNote",
    "ClassificationRule",
    "Dashboard",
    "CardComplaint",
    "CsRecord",
    "BmsSyncLog",
    "Device",
    "DeviceCombo",
    "DeviceTag",
    "ExternalComplaint",
    "Incident",
    "Measure",
    "Merchant",
    "MerchantRefund",
    "OperationLog",
    "Refund",
    "Report",
    "ReportStatus",
    "ReportType",
    "ReportComment",
    "CommentTarget",
    "ServiceTicket",
    "SyncSourceConfig",
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
    "WeeklyReport",
    "WeeklyReportStatus",
    "WeeklySectionBinding",
    "IDMixin",
    "TimestampMixin",
]

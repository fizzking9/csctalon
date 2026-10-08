"""模型汇总 - 导入所有模型,供 Alembic 与应用初始化使用"""

from app.models.ai_focus_config import AiFocusConfig  # noqa: F401
from app.models.ai_report import AiReport  # noqa: F401
from app.models.ai_report_feedback import AiReportFeedback  # noqa: F401
from app.models.ai_report_template import AiReportTemplate  # noqa: F401
from app.models.alert import Alert, AlertDelivery, AlertRule  # noqa: F401
from app.models.analysis_view import AnalysisView, ClassificationRule  # noqa: F401
from app.models.audit_note import AuditJudgment, AuditNote  # noqa: F401
from app.models.audit_change import AuditChange  # noqa: F401  # #261 统一变更审计
from app.models.base import IDMixin, TimestampMixin  # noqa: F401
from app.models.bms_order import BmsOrder  # noqa: F401
from app.models.bms_sync_log import BmsSyncLog  # noqa: F401
from app.models.batch_job import BatchJob, BatchJobRun  # noqa: F401
from app.models.card_complaint import CardComplaint  # noqa: F401
from app.models.cash_flow_wide import CashFlowWide  # noqa: F401
from app.models.combo_dict import ComboDict, ComboNameAlias  # noqa: F401
from app.models.complaint_audit import ComplaintAudit  # noqa: F401
from app.models.complaint_same_source import (  # noqa: F401
    ComplaintSameSourceGroup,
    ComplaintSameSourceMember,
)
from app.models.complaint_sync_log import ComplaintSyncLog  # noqa: F401
from app.models.cs_record import CsRecord  # noqa: F401
from app.models.dashboard import Dashboard  # noqa: F401
from app.models.data_quality import DataQualityRule  # noqa: F401
from app.models.data_source import (  # noqa: F401
    DataSource,
    DataSourceField,
    SemanticRole,
    SourceType,
)
from app.models.data_source_spec import (  # noqa: F401
    SPEC_STATUS_APPLIED,
    SPEC_STATUS_CONFIRMED,
    SPEC_STATUS_DRAFT,
    SPEC_STATUS_DRAFTING,
    SPEC_STATUS_FAILED,
    SPEC_STATUS_REJECTED,
    DataSourceSpec,
)
from app.models.dept_info_sync import DeptInfoSync  # noqa: F401
from app.models.device import (  # noqa: F401
    Device,
    DeviceTag,
    DeviceTagBatch,
    DeviceTagBinding,
    DimDeviceType,
    device_tag_rel,
)
from app.models.device_combo import DeviceCombo  # noqa: F401
from app.models.device_flow_usage import DeviceFlowUsage  # noqa: F401
from app.models.doc_sync_config import DocSyncConfig  # noqa: F401
from app.models.doc_sync_login_log import DocSyncLoginLog  # noqa: F401
from app.models.doc_sync_source import DocSyncSource  # noqa: F401
from app.models.expense import ExpenseInvoice, Reimbursement, ReimbursementItem  # noqa: F401
from app.models.external_complaint import ExternalComplaint  # noqa: F401
from app.models.external_token import ExternalAccessToken  # noqa: F401
from app.models.gap_record import GapRecord  # noqa: F401
from app.models.governance_finding import GovernanceFinding  # noqa: F401
from app.models.governance_prompt import GovernancePrompt  # noqa: F401
from app.models.governance_prompt_iteration import PromptIteration  # noqa: F401
from app.models.governance_rule import GovernanceRule  # noqa: F401
from app.models.group import Group, GroupLeadershipHistory, PersonGroupMembership  # noqa: F401
from app.models.incident import Incident, IncidentFollowup  # noqa: F401
from app.models.ingest_mode_config import IngestModeConfig  # noqa: F401
from app.models.ingest_rejected_row import IngestRejectedRow  # noqa: F401
from app.models.integration_profile import IntegrationProfile  # noqa: F401
from app.models.kb_article import KbArticle  # noqa: F401
from app.models.llm_provider import LlmCallLog, LlmProvider  # noqa: F401
from app.models.llm_role_config import LlmRoleConfig  # noqa: F401
from app.models.llm_role_member import LlmRoleMember  # noqa: F401
from app.models.manual_refund import ManualRefund  # noqa: F401
from app.models.manual_refund_sync_log import ManualRefundSyncLog  # noqa: F401
from app.models.measure import Measure  # noqa: F401
from app.models.merchant import DeviceMerchantLink, Merchant  # noqa: F401
from app.models.merchant_refund import MerchantRefund  # noqa: F401
from app.models.metric import (
    MetricDailySnapshot,
    MetricDictionary,
    MetricRegistry,
    MetricThreshold,
)  # noqa: F401
from app.models.notification import Notification  # noqa: F401
from app.models.notification_channel import NotificationChannel  # noqa: F401
from app.models.onboarding_prompt import OnboardingPrompt  # noqa: F401
from app.models.operation_log import OperationLog  # noqa: F401
from app.models.person import Person, PersonAlias, PersonSkillTag  # noqa: F401
from app.models.policy import Policy  # noqa: F401
from app.models.prompt_config import PromptConfig  # noqa: F401
from app.models.quality_disposition_rule import QualityDispositionRule  # noqa: F401
from app.models.reconcile_check import ReconcileCheck  # noqa: F401
from app.models.reconcile_match import ReconcileMatch  # noqa: F401
from app.models.refund import Refund  # noqa: F401
from app.models.refund_record_report import RefundRecordReport  # noqa: F401
from app.models.refund_wide import RefundWide  # noqa: F401
from app.models.report import Report, ReportStatus, ReportType  # noqa: F401
from app.models.report_comment import CommentTarget, ReportComment  # noqa: F401
from app.models.report_template import ReportTemplate  # noqa: F401
from app.models.return_tracking import ReturnTracking  # noqa: F401
from app.models.schedule import Attendance, Schedule, ShiftTemplate  # noqa: F401
from app.models.scheduler_settings import SchedulerSettings  # noqa: F401
from app.models.scrm_operator import ScrmOperator  # noqa: F401
from app.models.scrm_session import ScrmSession  # noqa: F401
from app.models.scrm_session_transfer import ScrmSessionTransfer  # noqa: F401
from app.models.scrm_staff_profile import ScrmStaffProfile  # noqa: F401
from app.models.scrm_sync_log import ScrmSyncLog  # noqa: F401
from app.models.shop import (  # noqa: F401
    DeviceShopLink,
    Enterprise,
    Shop,
    ShopEnterpriseHistory,
)
from app.models.source_group import SourceGroup  # noqa: F401
from app.models.specialty import (  # noqa: F401
    STANDARD_SPECIALTIES,
    PersonSpecialtyAssignment,
    Specialty,
    SpecialtyLeadershipHistory,
)
from app.models.staff_assignment import StaffAssignment  # noqa: F401
from app.models.strategy import StrategyDoc, StrategyReadReceipt  # noqa: F401
from app.models.sync_source_config import SyncSourceConfig  # noqa: F401
from app.models.system_event import SystemEvent  # noqa: F401
from app.models.task import SpecialTask, TaskDevice, TaskFollowup  # noqa: F401
from app.models.ticket import ServiceTicket  # noqa: F401
from app.models.ticket_action_log import TicketActionLog  # noqa: F401
from app.models.todo import TodoFollowup, TodoItem  # noqa: F401
from app.models.upload import DataMatchRelation, UploadRecord  # noqa: F401
from app.models.upstream_ticket import UpstreamTicket  # noqa: F401
from app.models.user import RoleEnum, User  # noqa: F401
from app.models.wallet import WalletWithdrawal  # noqa: F401
from app.models.wechat import (  # noqa: F401
    RegisterSource,
    RegisterStatus,
    WechatAccount,
    WechatRegistration,
)
from app.models.weekly_report import WeeklyReport, WeeklyReportStatus  # noqa: F401
from app.models.weekly_section_binding import WeeklySectionBinding  # noqa: F401

__all__ = [
    "AiFocusConfig",
    "AiReport",
    "AiReportFeedback",
    "AiReportTemplate",
    "AnalysisView",
    "Alert",
    "AlertRule",
    "AlertDelivery",
    "BatchJob",
    "BatchJobRun",
    "MetricRegistry",
    "MetricThreshold",
    "MetricDailySnapshot",
    "MetricDictionary",
    "NotificationChannel",
    "AuditJudgment",
    "AuditNote",
    "AuditChange",
    "ClassificationRule",
    "Dashboard",
    "CardComplaint",
    "CashFlowWide",
    "ComplaintAudit",
    "ComplaintSyncLog",
    "CsRecord",
    "BmsSyncLog",
    "DataQualityRule",
    "DeptInfoSync",
    "Device",
    "DeviceCombo",
    "DeviceFlowUsage",
    "DeviceTag",
    "DeviceTagBatch",
    "DeviceTagBinding",
    "DocSyncConfig",
    "ExpenseInvoice",
    "ExternalComplaint",
    "ExternalAccessToken",
    "Group",
    "GroupLeadershipHistory",
    "PersonGroupMembership",
    "Incident",
    "IncidentFollowup",
    "IngestModeConfig",
    "IntegrationProfile",
    "SchedulerSettings",
    "SchedulerSettings",
    "IngestRejectedRow",
    "KbArticle",
    "LlmCallLog",
    "LlmProvider",
    "Measure",
    "ManualRefund",
    "ManualRefundSyncLog",
    "Merchant",
    "MerchantRefund",
    "BmsOrder",
    "RefundRecordReport",
    "ReturnTracking",
    "Notification",
    "OnboardingPrompt",
    "OperationLog",
    "Policy",
    "PromptConfig",
    "QualityDispositionRule",
    "Person",
    "PersonAlias",
    "PersonSkillTag",
    "ReconcileCheck",
    "ReconcileMatch",
    "Refund",
    "RefundWide",
    "Report",
    "ReportStatus",
    "ReportType",
    "ReportComment",
    "CommentTarget",
    "ReportTemplate",
    "Reimbursement",
    "ReimbursementItem",
    "Schedule",
    "ShiftTemplate",
    "Attendance",
    "Specialty",
    "SpecialtyLeadershipHistory",
    "PersonSpecialtyAssignment",
    "STANDARD_SPECIALTIES",
    "ScrmOperator",
    "ScrmSession",
    "ScrmSessionTransfer",
    "ScrmStaffProfile",
    "ScrmSyncLog",
    "SourceGroup",
    "SystemEvent",
    "ServiceTicket",
    "StaffAssignment",
    "TicketActionLog",
    "SyncSourceConfig",
    "WalletWithdrawal",
    "UploadRecord",
    "DataMatchRelation",
    "User",
    "RoleEnum",
    "WechatRegistration",
    "WechatAccount",
    "RegisterStatus",
    "RegisterSource",
    "TodoItem",
    "TodoFollowup",
    "UpstreamTicket",
    "SpecialTask",
    "TaskDevice",
    "TaskFollowup",
    "StrategyDoc",
    "StrategyReadReceipt",
    "DataSource",
    "DataSourceField",
    "SemanticRole",
    "SourceType",
    "WeeklyReport",
    "WeeklyReportStatus",
    "WeeklySectionBinding",
    "IDMixin",
    "TimestampMixin",
]

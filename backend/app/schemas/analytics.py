"""分析看板相关 Pydantic 模型"""

from pydantic import BaseModel


class TrendPoint(BaseModel):
    """趋势数据点"""

    date: str
    value: float
    count: int | None = None


class CategoryPoint(BaseModel):
    """分类数据点"""

    name: str
    value: float
    count: int | None = None
    ratio: float | None = None


class OperatorStat(BaseModel):
    """人员操作统计"""

    operator: str
    ticket_count: int = 0
    refund_amount: float = 0.0
    avg_handle_duration: float | None = None
    audit_count: int = 0
    audit_amount: float = 0.0


class DashboardSummary(BaseModel):
    """看板汇总卡片"""

    refund_total: float = 0.0
    refund_count: int = 0
    cash_out_total: float = 0.0
    wallet_balance_total: float = 0.0
    pending_todo: int = 0
    urgent_todo: int = 0

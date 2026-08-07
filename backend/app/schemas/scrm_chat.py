"""SCRM 聊天记录查询 schemas (独立功能预留)

- 聊天记录用户说 "偶尔调用",所以接口先预留骨架:
  * 如果 query 参数缺少「日期范围 + 至少一个筛选条件」,直接返回空(不触发 Playwright 抓取);
  * 后续在「设备/人员接待审核」等功能需要时,再填 full 筛选条件调用,后端内部再调用 Playwright 或 查本地 scrm_sessions 表。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


# ============================================================
#  1. 查询入参
# ============================================================
class ScrmChatQueryParams(BaseModel):
    """聊天记录筛选条件(预留)

    所有字段可选; 但是实际执行抓取前需要「日期范围 + 至少一个额外筛选」才会真的去查。
    """

    # 主维度: 日期范围
    start_date: date | None = None
    end_date: date | None = None

    # 会话类型: 私聊/群聊/虚拟 (对应 3 个 data_type)
    session_type: Literal["private", "group", "virtual"] | None = None

    # 客户维度
    customer_name: str | None = Field(default=None, description="客户昵称/备注名 模糊搜索")
    customer_phone: str | None = None

    # 客服维度
    staff_name: str | None = Field(default=None, description="接待客服名(企微人设/别名/实名 皆可) 模糊搜索")
    staff_id: int | None = Field(default=None, description="本地 ScrmStaffProfile.id,精确匹配")
    person_id: int | None = Field(default=None, description="归一化人员 Person.id")

    # 会话状态
    session_status: str | None = Field(default=None, description="处理中 / 已结束 / 超时 等原文保留")

    # 所属企微企业
    enterprise_suffix: str | None = Field(default=None, description="@企业后缀,如 云客猫/自在联")

    # 分页
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=500)

    # 兜底: 允许强制执行抓取(即使筛选条件少), 默认 False
    force_crawl: bool = False

    @model_validator(mode="after")
    def _check_date_order(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date 不能晚于 end_date")
        return self

    def has_minimum_filter(self) -> bool:
        """是否满足「至少日期范围 + 一个额外筛选条件」才去抓取(force_crawl=True 跳过)"""
        if self.force_crawl:
            return True
        if not (self.start_date and self.end_date):
            return False
        extra = any([
            self.session_type,
            self.customer_name,
            self.customer_phone,
            self.staff_name,
            self.staff_id,
            self.person_id,
            self.session_status,
            self.enterprise_suffix,
        ])
        return extra


# ============================================================
#  2. 单条聊天记录
# ============================================================
class ScrmChatRecordItem(BaseModel):
    """一条聊天会话摘要行(解析自 tuoke/customer/chatRecord 表格)"""
    customer_name: str | None = None
    customer_suffix: str | None = Field(default=None, description="客户 @ 标签,如 @客户")
    wecom_persona: str | None = Field(default=None, description="企微人设,如 小欢（李欢欢）")
    wecom_suffix: str | None = Field(default=None, description="企微 @ 企业,如 @云客猫")
    staff_display_name: str | None = Field(default=None, description="接待客服显示名,如 云客猫-龙佳颖")
    last_time: datetime | None = Field(default=None, description="最近消息时间")
    status: str | None = None
    unread_count: int | None = None
    extra: dict | None = Field(default=None, description="原始行(中文列名)冗余,便于后续解析更多字段")

    # 关联归一 ID (后续填充,当前先 None)
    scrm_staff_id: int | None = None
    person_id: int | None = None

    created_at: datetime | None = None


# ============================================================
#  3. 查询响应
# ============================================================
class ScrmChatQueryResponse(BaseModel):
    query: ScrmChatQueryParams
    skipped: bool = Field(default=True, description="True=未触发抓取(条件不足/预留接口占位)")
    skip_reason: str | None = None
    total: int = 0
    data_source: str = Field(default="placeholder", description="placeholder / web_crawler / local_db / remote_api")
    items: list[ScrmChatRecordItem] = []
    # 本次抓取耗时(预留)
    elapsed_ms: int = 0

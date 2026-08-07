"""SCRM 客服人员(人设)信息 Pydantic schemas"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ============================================================
#  1. 出参: ScrmStaffProfile 列表/详情
# ============================================================
class ScrmStaffProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_config_id: int | None = None
    sync_source_name: str | None = None

    # ---- 企微人设标识 ----
    wecom_nick: str
    enterprise_suffix: str | None = None
    staff_unique_key: str

    # ---- 归一化匹配 ----
    person_id: int | None = None
    user_id: int | None = None
    match_status: str = "unmatched"  # unmatched/auto_matched/manual_confirmed/rejected
    match_score: int | None = None
    match_reason: str | None = None

    # ---- 企业/在线状态 ----
    company_group: str | None = None
    online_status: str | None = None

    # ---- 身份信息 ----
    real_name: str | None = None
    alias_name: str | None = None
    external_display_name: str | None = None
    gender: str | None = None

    # ---- 设备/账号属性 ----
    device_type: str | None = None
    auto_accept_friend: str | None = None
    auto_accept_group_verify: str | None = None

    # ---- 运营模板 ----
    moments_cover: str | None = None
    persona_template: str | None = None
    new_customer_template: str | None = None

    last_synced_at: datetime | None = None
    remark: str | None = None
    raw_extra: dict | None = None
    created_at: datetime
    updated_at: datetime


# ============================================================
#  2. 入参: 更新/手动绑定 person_id/user_id
# ============================================================
class ScrmStaffProfileUpdate(BaseModel):
    """手动更新人设信息,主要用于:绑定 person_id / user_id / 改 match_status / 填 remark"""
    person_id: int | None = None
    user_id: int | None = None
    match_status: str | None = Field(
        default=None, pattern=r"^(unmatched|auto_matched|manual_confirmed|rejected)$",
    )
    remark: str | None = None

    # 允许覆盖业务字段(罕见,仅人工修正时传)
    real_name: str | None = None
    alias_name: str | None = None
    external_display_name: str | None = None
    company_group: str | None = None
    device_type: str | None = None


# ============================================================
#  3. 匹配建议结果(归一化候选)
# ============================================================
class ScrmStaffMatchCandidate(BaseModel):
    """一条未匹配人设的归一建议(匹配到 Person 或 User)"""
    target_kind: str  # "person" / "user"
    target_id: int
    target_display_name: str
    target_qw_name: str | None = None
    target_username: str | None = None  # 仅 user
    score: int
    reasons: list[str] = []

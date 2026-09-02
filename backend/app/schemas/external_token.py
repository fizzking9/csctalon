"""外部只读访问令牌 - Pydantic 模型(管理接口)"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# 可用 scope(与 external_service.SCOPES 一致, 供前端选项)
EXTERNAL_SCOPES = ["reception", "refund", "complaint", "brief"]


class ExternalTokenCreate(BaseModel):
    """生成外部只读 token 入参"""

    name: str = Field(..., min_length=1, max_length=64, description="调用方标识(如 workbuddy-简报)")
    scopes: list[str] = Field(..., min_length=1, description="权限范围: reception/refund/complaint/brief")
    rate_limit_per_min: int | None = Field(None, ge=1, description="个性化限频(次/分钟); 空=全局默认")
    expires_at: datetime | None = Field(None, description="可选过期时间(北京时间)")


class ExternalTokenUpdate(BaseModel):
    """改 name/scopes/限频/吊销(部分更新)"""

    name: str | None = Field(None, min_length=1, max_length=64)
    scopes: list[str] | None = Field(None, min_length=1)
    rate_limit_per_min: int | None = Field(None, ge=1)
    enabled: bool | None = None


class ExternalTokenOut(BaseModel):
    """列表输出: 不含明文(仅 prefix)"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    token_prefix: str
    scopes: list
    rate_limit_per_min: int | None = None
    enabled: bool
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    created_by: str | None = None
    revoked_by: str | None = None
    created_at: datetime


class ExternalTokenCreated(BaseModel):
    """创建成功响应: 明文仅此一次"""

    id: int
    name: str
    token: str
    token_prefix: str
    scopes: list
    rate_limit_per_min: int | None = None
    expires_at: datetime | None = None

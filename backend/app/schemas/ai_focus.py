"""AI 助手关注点配置 - Pydantic schema(管理接口 / 工单 #26)"""

from pydantic import BaseModel, ConfigDict, Field

# 合法 scope_key(与 service 层一致)
FOCUS_SCOPES = ("global", "refund", "complaint", "reception")


class AiFocusOut(BaseModel):
    """列表输出。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    scope_key: str
    title: str
    content: str
    sort_order: int
    enabled: bool
    created_by: str | None = None
    updated_by: str | None = None
    created_at: object | None = None


class AiFocusCreate(BaseModel):
    """新建关注点。"""

    code: str = Field(..., min_length=1, max_length=32, description="唯一标识(如 focus-refund-litigation)")
    scope_key: str = Field("global", pattern="^(global|refund|complaint|reception)$", description="global/refund/complaint/reception")
    title: str = Field(..., min_length=1, max_length=64)
    content: str = Field(..., min_length=1, description="关注点正文")
    sort_order: int = Field(0, ge=0)
    enabled: bool = True


class AiFocusUpdate(BaseModel):
    """更新(部分)。scope_key/code 变更需谨慎(影响路由/标识), 默认只允许改 title/content/sort_order/enabled。"""

    title: str | None = Field(None, min_length=1, max_length=64)
    content: str | None = Field(None, min_length=1)
    sort_order: int | None = Field(None, ge=0)
    enabled: bool | None = None
    scope_key: str | None = Field(None, pattern="^(global|refund|complaint|reception)$")

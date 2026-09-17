"""LLM 多 Provider - Pydantic schema (管理接口 / 工单 #108)"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LlmProviderOut(BaseModel):
    """列表输出: 不含 api_key 明文(仅 prefix)"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    base_url: str
    model: str
    api_key_prefix: str | None = None
    api_key_configured: bool = False  # 是否已配 key(由 service 层填)
    priority: int
    enabled: bool
    pregen_only: bool = False  # #131 仅供后台预生成, 不进实时默认链/不参与启动预热
    healthy: bool
    thinking: str
    timeout_s: float
    retry_max: int
    fail_threshold: int
    fail_count: int
    last_fail_at: datetime | None = None
    last_success_at: datetime | None = None
    note: str | None = None
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LlmProviderCreate(BaseModel):
    """新建 Provider; api_key 明文仅此一次, service 加密入库"""

    code: str = Field(..., min_length=1, max_length=64, description="唯一标识(如 talon-gmk-qwen3-coder-30b)")
    name: str = Field(..., min_length=1, max_length=128)
    base_url: str = Field(..., min_length=1, max_length=256, description="OpenAI 兼容完整前缀(含/不含 /v1 以服务商为准)")
    model: str = Field(..., min_length=1, max_length=128)
    api_key: str | None = Field(None, description="API key 明文; 无鉴权服务(本地无 key)可留空")
    priority: int = Field(100, ge=0, le=9999, description="小=高; 调用按 priority 升序遍历")
    enabled: bool = True
    pregen_only: bool = Field(False, description="#131 仅供后台预生成; 不进实时默认链/不参与启动预热")
    thinking: str = Field("disabled", pattern="^(disabled|low|high)$")
    timeout_s: float = Field(120.0, ge=1.0, le=600.0)
    retry_max: int = Field(2, ge=0, le=10)
    fail_threshold: int = Field(3, ge=1, le=20)
    note: str | None = Field(None, max_length=255)


class LlmProviderUpdate(BaseModel):
    """更新(部分); code 不可改(唯一标识)"""

    name: str | None = Field(None, min_length=1, max_length=128)
    base_url: str | None = Field(None, min_length=1, max_length=256)
    model: str | None = Field(None, min_length=1, max_length=128)
    # 留空=不改 key; 提供值=重置 key
    api_key: str | None = Field(None, description="传值=重置 key; 留空=不改")
    priority: int | None = Field(None, ge=0, le=9999)
    enabled: bool | None = None
    pregen_only: bool | None = Field(None, description="#131 仅供后台预生成标记")
    healthy: bool | None = Field(None, description="手动覆盖健康状态(强制恢复/降级)")
    thinking: str | None = Field(None, pattern="^(disabled|low|high)$")
    timeout_s: float | None = Field(None, ge=1.0, le=600.0)
    retry_max: int | None = Field(None, ge=0, le=10)
    fail_threshold: int | None = Field(None, ge=1, le=20)
    note: str | None = Field(None, max_length=255)


class LlmTestConnRequest(BaseModel):
    """测试连接入参(不依赖 Provider id, 可独立测任意配置)"""

    base_url: str = Field(..., min_length=1, max_length=256)
    model: str = Field(..., min_length=1, max_length=128)
    api_key: str | None = Field(None, description="无鉴权服务可留空")
    timeout_s: float = Field(30.0, ge=5.0, le=120.0)


class LlmTestConnResult(BaseModel):
    """测试连接结果"""

    ok: bool
    status_code: int | None = None
    duration_ms: int
    error: str | None = None
    sample: str | None = Field(None, description="成功时的样例输出(脱敏截断)")

"""接入配置包模板 - Pydantic 模型 (#44)"""

from pydantic import BaseModel, ConfigDict, Field


class ProfileVariable(BaseModel):
    """模板变量声明"""

    key: str = Field(..., max_length=32, description="变量名(须在服务端白名单内)")
    label: str = Field(..., max_length=64, description="表单显示名")
    required: bool = Field(False, description="是否必填")


class IntegrationProfileCreate(BaseModel):
    """新建模板入参"""

    code: str = Field(..., min_length=1, max_length=48, description="唯一标识")
    name: str = Field(..., min_length=1, max_length=64, description="显示名")
    scene: str = Field(..., description="场景: push / pull / manual_refund")
    content_md: str = Field(..., min_length=1, description="Markdown 模板(含 {{变量}})")
    variables: list[ProfileVariable] | None = Field(None, description="变量声明")
    enabled: bool | None = Field(True, description="是否启用")
    sort_order: int | None = Field(0, description="排序(小在前)")


class IntegrationProfileUpdate(BaseModel):
    """更新模板入参(部分更新)"""

    name: str | None = Field(None, min_length=1, max_length=64)
    scene: str | None = None
    content_md: str | None = Field(None, min_length=1)
    variables: list[ProfileVariable] | None = None
    enabled: bool | None = None
    sort_order: int | None = None


class IntegrationProfileOut(BaseModel):
    """模板输出"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    scene: str
    content_md: str
    # #47: 历史版本字段(无则 None / False, 前端按 has_previous 显隐「回滚到上一版」按钮)
    previous_content_md: str | None = None
    has_previous: bool = False
    variables: list = []
    enabled: bool
    sort_order: int
    created_by: str | None = None
    updated_by: str | None = None
    updated_at: str | None = None


class ProfileRollbackRequest(BaseModel):
    """#47: 回滚请求体(二次确认)。"""

    confirm: bool = Field(
        False,
        description="二次确认: 必须显式传 true 才执行回滚, 防误点(空体或 confirm!=true 返回 400)",
    )


class ProfileRenderRequest(BaseModel):
    """渲染请求"""

    doc_url: str | None = Field(None, description="共享文档地址")
    doc_name: str | None = Field(None, description="文档显示名")
    token: str | None = Field(None, description="令牌(占位符语义, 一般留空由用户自行粘贴)")
    target_type: str | None = Field(None, description="目标业务(complaint / manual_refund)")
    sync_interval: int | None = Field(None, ge=1, le=1440, description="同步间隔(分钟)")
    base_url: str | None = Field(None, description="系统对外地址(缺省按请求推导)")


class ProfileRenderOut(BaseModel):
    """渲染结果"""

    markdown: str
    char_count: int
    unresolved: list = []
    ignored_keys: list = []

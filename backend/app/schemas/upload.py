"""上传与数据导入相关 Pydantic 模型"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ColumnMapping(BaseModel):
    """列名映射:源列名 -> 目标字段"""

    source_column: str
    target_field: str


class ParsePreviewRequest(BaseModel):
    """解析预览请求"""

    data_type: str
    filename: str
    sheet_name: str | None = None
    encoding: str | None = None


class ParsePreviewResponse(BaseModel):
    """解析预览响应"""

    model_config = ConfigDict(from_attributes=True)

    columns: list[str]
    sample_rows: list[dict[str, Any]]
    total_rows: int
    detected_encoding: str | None = None
    suggested_mapping: dict[str, str]
    sheet_names: list[str] | None = None


class ImportRequest(BaseModel):
    """确认导入请求"""

    data_type: str
    filename: str
    sheet_name: str | None = None
    encoding: str | None = None
    column_mapping: dict[str, str]


class ImportResponse(BaseModel):
    """导入结果"""

    model_config = ConfigDict(from_attributes=True)

    upload_id: int
    data_type: str
    filename: str
    total_rows: int
    inserted_rows: int
    updated_rows: int
    failed_rows: int
    matched_rows: int
    status: str
    errors: list[dict[str, Any]] | None = None
    finished_at: datetime | None = None


class UploadRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    data_type: str
    filename: str
    status: str
    total_rows: int
    inserted_rows: int
    updated_rows: int
    failed_rows: int
    matched_rows: int
    uploaded_by: str | None = None
    created_at: datetime
    finished_at: datetime | None = None

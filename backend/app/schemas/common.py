"""通用响应与分页模型"""

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ResponseBase(BaseModel):
    """统一响应包装"""

    model_config = ConfigDict(from_attributes=True)

    code: int = 0
    message: str = "ok"


class R(ResponseBase, Generic[T]):
    """带数据的统一响应"""

    data: T | None = None


class PageBase(BaseModel, Generic[T]):
    """分页数据"""

    model_config = ConfigDict(from_attributes=True)

    items: list[T]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return (self.total + self.page_size - 1) // self.page_size if self.page_size else 0

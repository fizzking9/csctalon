"""用户相关 Pydantic 模型"""

from pydantic import BaseModel, ConfigDict

from app.models.user import RoleEnum


class UserBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    username: str
    display_name: str
    role: RoleEnum = RoleEnum.AGENT
    group_name: str | None = None
    qw_name: str | None = None
    is_active: bool = True


class UserCreate(UserBase):
    password: str


class UserUpdate(BaseModel):
    display_name: str | None = None
    role: RoleEnum | None = None
    group_name: str | None = None
    qw_name: str | None = None
    is_active: bool | None = None
    password: str | None = None


class UserOut(UserBase):
    id: int


class UserBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: RoleEnum
    group_name: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut

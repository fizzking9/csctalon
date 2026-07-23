"""认证路由:登录、当前用户信息"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DBSession
from app.core.database import get_db
from app.core.security import create_access_token, verify_password
from app.models.user import RoleEnum, User
from app.schemas.common import R
from app.schemas.user import LoginRequest, Token, UserOut

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login", response_model=R[Token], summary="账号密码登录")
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """OAuth2 password 流程登录,返回 JWT"""
    user = db.scalar(select(User).where(User.username == form.username))
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已停用")
    token = create_access_token(
        user.id, extra={"username": user.username, "role": user.role.value, "name": user.display_name}
    )
    return R(data=Token(access_token=token, user=UserOut.model_validate(user)))


@router.post("/login-json", response_model=R[Token], summary="JSON方式登录(前端用)")
def login_json(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已停用")
    token = create_access_token(
        user.id, extra={"username": user.username, "role": user.role.value, "name": user.display_name}
    )
    return R(data=Token(access_token=token, user=UserOut.model_validate(user)))


@router.get("/me", response_model=R[UserOut], summary="当前登录用户")
def me(current_user: CurrentUser):
    return R(data=UserOut.model_validate(current_user))

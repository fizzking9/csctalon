"""认证路由:登录、当前用户信息"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DBSession
from app.core.database import get_db
from app.core.security import create_access_token, verify_password
from app.models.user import RoleEnum, User
from app.schemas.common import R
from app.schemas.user import LoginRequest, Token, UserOut
from app.services.operation_logger import log_operation

router = APIRouter(prefix="/auth", tags=["认证"])


def _client_ip(request: Request) -> str | None:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post("/login", response_model=R[Token], summary="账号密码登录")
def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """OAuth2 password 流程登录,返回 JWT"""
    user = db.scalar(select(User).where(User.username == form.username))
    if not user or not verify_password(form.password, user.hashed_password):
        # 登录失败也记录(用户名来自表单,便于审计爆破)
        log_operation(
            db, username=form.username, method="POST", path="/api/v1/auth/login",
            action="认证.登录失败", target_type="auth", status_code=401,
            detail={"username": form.username}, ip=_client_ip(request),
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    if not user.is_active:
        log_operation(
            db, user_id=user.id, username=user.username, method="POST", path="/api/v1/auth/login",
            action="认证.登录拒绝", target_type="auth", status_code=403,
            detail={"reason": "账号已停用"}, ip=_client_ip(request),
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已停用")
    token = create_access_token(
        user.id, extra={"username": user.username, "role": user.role, "name": user.display_name}
    )
    log_operation(
        db, user_id=user.id, username=user.username, method="POST", path="/api/v1/auth/login",
        action="认证.登录", target_type="auth", status_code=200, ip=_client_ip(request),
    )
    return R(data=Token(access_token=token, user=UserOut.model_validate(user)))


@router.post("/login-json", response_model=R[Token], summary="JSON方式登录(前端用)")
def login_json(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not verify_password(payload.password, user.hashed_password):
        log_operation(
            db, username=payload.username, method="POST", path="/api/v1/auth/login-json",
            action="认证.登录失败", target_type="auth", status_code=401,
            detail={"username": payload.username}, ip=_client_ip(request),
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    if not user.is_active:
        log_operation(
            db, user_id=user.id, username=user.username, method="POST", path="/api/v1/auth/login-json",
            action="认证.登录拒绝", target_type="auth", status_code=403,
            detail={"reason": "账号已停用"}, ip=_client_ip(request),
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已停用")
    token = create_access_token(
        user.id, extra={"username": user.username, "role": user.role, "name": user.display_name}
    )
    log_operation(
        db, user_id=user.id, username=user.username, method="POST", path="/api/v1/auth/login-json",
        action="认证.登录", target_type="auth", status_code=200, ip=_client_ip(request),
    )
    return R(data=Token(access_token=token, user=UserOut.model_validate(user)))


@router.get("/me", response_model=R[UserOut], summary="当前登录用户")
def me(current_user: CurrentUser):
    return R(data=UserOut.model_validate(current_user))

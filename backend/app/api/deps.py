"""FastAPI 依赖注入:数据库会话、当前用户、权限校验"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.user import MANAGER_ROLES, RoleEnum, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_PREFIX if hasattr(settings, 'API_PREFIX') else '/api/v1'}/auth/login", auto_error=False)


async def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """从 JWT 解析当前用户"""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = int(payload["sub"])
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用")
    # M7: JWT 吊销校验 —— token 中 tkv 必须与 user.token_version 一致。
    # 旧 token(无 tkv)或改密/登出后版本不匹配 → 失效, 强制重新登录。
    tkv = payload.get("tkv")
    if tkv is None or tkv != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌已失效, 请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_role(*roles: RoleEnum):
    """角色校验依赖工厂"""

    allowed = [r.value for r in roles]

    async def _check(current_user: Annotated[User, Depends(get_current_user)]) -> User:
        if current_user.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        return current_user

    return _check


# 常用快捷依赖
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_role(RoleEnum.ADMIN))]
AdminOrSupervisor = Annotated[User, Depends(require_role(RoleEnum.ADMIN, RoleEnum.SUPERVISOR))]
# 管理层: 管理员 + 主管 + 专项组长(可看决策看板/管理闭环, 见规划设计 v0.5 §二)
ManagerUser = Annotated[User, Depends(require_role(*[RoleEnum(r) for r in MANAGER_ROLES]))]
DBSession = Annotated[Session, Depends(get_db)]

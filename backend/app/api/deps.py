"""FastAPI 依赖注入:数据库会话、当前用户、权限校验"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.user import MANAGER_ROLES, RoleEnum, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_PREFIX if hasattr(settings, 'API_PREFIX') else '/api/v1'}/auth/login", auto_error=False)


# 注意：这里刻意用同步 def 而非 async —— 本依赖是全站最高频依赖（每个鉴权请求都执行），
# 体内 db.get() 是同步 SQLAlchemy 调用；若声明成 async，它会在事件循环线程里阻塞，
# 拖垮同进程所有并发请求。FastAPI 对普通 def 依赖自动放入线程池（run_in_threadpool）执行（#213 R-P1-10）。
def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """从 JWT 解析当前用户（同步 def：FastAPI 自动线程池调度，避免阻塞事件循环）"""
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
    # 2026-08-20 审计 SEC-08 修复: sub 非整数(畸形/伪造 token)按 401 处理, 而非 int() 抛 ValueError 打 500
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
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

    def _check(current_user: Annotated[User, Depends(get_current_user)]) -> User:
        # 同步 def：仅做内存角色比对、无 IO；与 get_current_user 一致由线程池调度（#213）。
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

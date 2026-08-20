"""用户管理路由"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.core.database import get_db
from app.core.security import hash_password
from app.models.user import RoleEnum, User
from app.schemas.common import PageBase, R
from app.schemas.user import UserBrief, UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["用户管理"])


@router.get("", response_model=R[PageBase[UserBrief]], summary="用户列表")
def list_users(
    db: DBSession,
    _: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
    role: RoleEnum | None = None,
):
    stmt = select(User)
    if keyword:
        kw = f"%{keyword}%"
        stmt = stmt.where(User.username.like(kw) | User.display_name.like(kw))
    if role:
        stmt = stmt.where(User.role == role)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(stmt.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size))
    )
    return R(data=PageBase(items=[UserBrief.model_validate(u) for u in items], total=total or 0, page=page, page_size=page_size))


@router.post("", response_model=R[UserOut], summary="创建用户")
def create_user(payload: UserCreate, db: DBSession, _: AdminUser):
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status_code=400, detail="用户名已存在")
    # 2026-08-20 审计 SEC-13: bcrypt 仅支持 72 字节, 超长密码转 400 而非 500
    try:
        hashed = hash_password(payload.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    user = User(
        username=payload.username,
        display_name=payload.display_name,
        hashed_password=hashed,
        role=payload.role.value,
        group_name=payload.group_name,
        qw_name=payload.qw_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return R(data=UserOut.model_validate(user))


@router.put("/{user_id}", response_model=R[UserOut], summary="更新用户")
def update_user(user_id: int, payload: UserUpdate, db: DBSession, current: CurrentUser):
    # 普通用户只能改自己,管理员可改任意
    if current.role != RoleEnum.ADMIN.value and current.id != user_id:
        raise HTTPException(status_code=403, detail="只能修改自己的信息")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    data = payload.model_dump(exclude_unset=True)
    password_changed = False
    if password := data.pop("password", None):
        # 验旧密码: 用户改自己密码必须验旧(防会话劫持后改密锁户);
        # 管理员重置他人密码(current.id != user_id)不强制验旧
        old_password = data.pop("old_password", None)
        require_old = current.id == user_id
        if require_old:
            if not old_password:
                raise HTTPException(status_code=400, detail="修改自己的密码需提供原密码(old_password)")
            from app.core.security import verify_password
            if not verify_password(old_password, user.hashed_password):
                raise HTTPException(status_code=400, detail="原密码错误")
        # 2026-08-20 审计 SEC-13: 超长密码(>72 字节)转 400
        try:
            user.hashed_password = hash_password(password)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        password_changed = True
    else:
        # 即便不改密, old_password 字段也不应残留入库
        data.pop("old_password", None)
    # 非管理员不能改角色
    if current.role != RoleEnum.ADMIN.value:
        data.pop("role", None)
        data.pop("is_active", None)
    else:
        # role 是 RoleEnum,存为字符串
        if "role" in data and data["role"] is not None:
            data["role"] = payload.role.value if payload.role else data["role"]
    for k, v in data.items():
        setattr(user, k, v)
    # M7: 改密后自增 token_version, 使该用户所有已签发 token 立即失效(含当前会话, 需重新登录)
    if password_changed:
        user.token_version = (user.token_version or 0) + 1
    db.commit()
    db.refresh(user)
    return R(data=UserOut.model_validate(user))


@router.delete("/{user_id}", response_model=R, summary="删除用户")
def delete_user(user_id: int, db: DBSession, current: AdminUser):
    if current.id == user_id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    db.delete(user)
    db.commit()
    return R()

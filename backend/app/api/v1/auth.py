"""认证路由:登录、当前用户信息"""


from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import check_login_rate
from app.core.security import create_access_token, verify_password
from app.models.user import User
from app.schemas.common import R
from app.schemas.user import LoginRequest, SsoEntriesOut, SsoTicketOut, Token, UserOut
from app.services.operation_logger import log_operation
from app.services.sso_ticket import build_netmgr_entry_url

router = APIRouter(prefix="/auth", tags=["认证"])


def _client_ip(request: Request) -> str | None:
    """取真实客户端 IP。

    取 X-Forwarded-For 末段(由可信反代 nginx 追加的真实 client IP),
    而非首段(首段可被客户端伪造, 会导致按 IP 限速被绕过)。
    无 XFF 时回退到直连 client.host。
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]  # 末段 = 反代追加的真实 IP
    return request.client.host if request.client else None


@router.post("/login", response_model=R[Token], summary="账号密码登录")
def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """OAuth2 password 流程登录,返回 JWT"""
    # 登录限速(防爆破): 按 IP 限流, 5 分钟内最多 10 次
    ip = _client_ip(request)
    allowed, retry = check_login_rate(ip)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"登录尝试过于频繁, 请 {retry} 秒后重试",
        )
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
    # M7: 签发带 tkv=token_version 的 token, 后续请求校验不一致即失效
    token = create_access_token(
        user.id,
        extra={"username": user.username, "role": user.role, "name": user.display_name},
        token_version=user.token_version,
    )
    log_operation(
        db, user_id=user.id, username=user.username, method="POST", path="/api/v1/auth/login",
        action="认证.登录", target_type="auth", status_code=200, ip=_client_ip(request),
    )
    return R(data=Token(access_token=token, user=UserOut.model_validate(user)))


@router.post("/login-json", response_model=R[Token], summary="JSON方式登录(前端用)")
def login_json(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    # 登录限速(防爆破): 按 IP 限流, 5 分钟内最多 10 次
    ip = _client_ip(request)
    allowed, retry = check_login_rate(ip)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"登录尝试过于频繁, 请 {retry} 秒后重试",
        )
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
    # M7: 签发带 tkv=token_version 的 token
    token = create_access_token(
        user.id,
        extra={"username": user.username, "role": user.role, "name": user.display_name},
        token_version=user.token_version,
    )
    log_operation(
        db, user_id=user.id, username=user.username, method="POST", path="/api/v1/auth/login-json",
        action="认证.登录", target_type="auth", status_code=200, ip=_client_ip(request),
    )
    return R(data=Token(access_token=token, user=UserOut.model_validate(user)))


@router.get("/me", response_model=R[UserOut], summary="当前登录用户")
def me(current_user: CurrentUser):
    return R(data=UserOut.model_validate(current_user))


@router.get(
    "/sso/entries",
    response_model=R[SsoEntriesOut],
    summary="外部管理台入口可用性(仅管理员)",
)
def sso_entries(_current_user: AdminUser):
    """工单 #187：告诉管理端「有哪些外部管理台入口可用」。

    只回可用性与地址，**不回票据** —— 票据在用户点击时经 POST /auth/sso/ticket 单独签发，
    避免页面加载即产生一次性凭证。地址由配置提供，不在代码中硬编码内网 IP。
    """
    return R(data=SsoEntriesOut(
        netmgr_enabled=bool(settings.SSO_SHARED_SECRET),
        talon_url=settings.SSO_TALON_URL,
    ))


@router.post(
    "/sso/ticket",
    response_model=R[SsoTicketOut],
    summary="签发免密进入 netmgr 网络管理面板的票据(仅管理员)",
)
def sso_ticket(current_user: AdminUser, db: DBSession, request: Request):
    """工单 #187：管理员点管理端入口 → 签 60 秒一次性票据 → 浏览器跳 netmgr 落地页。

    方向为「csctalon 签发 → netmgr 消费」：netmgr 面板持有 docker.sock（= 宿主 root），
    不适合当业务系统的身份权威，故由本系统签发、面板消费。

    票据放在 URL fragment（`#ticket=…`），浏览器不会把 fragment 发给服务端 ——
    因此票据不进两侧 access log、不随 Referer 外泄、不留服务端痕迹。
    """
    if not settings.SSO_SHARED_SECRET:
        # 未配置共享密钥 = 免密入口关闭（不影响本系统自身登录）
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="免密入口未配置（缺少 SSO_SHARED_SECRET）",
        )

    url = build_netmgr_entry_url(current_user.username)
    log_operation(
        db, user_id=current_user.id, username=current_user.username,
        method="POST", path="/api/v1/auth/sso/ticket",
        action="认证.签发免密票据", target_type="auth", status_code=200,
        # ⚠️ 只记目标与有效期，绝不记录 url/ticket 内容（票据即凭证）
        detail={"target": "netmgr", "ttl_seconds": settings.SSO_TICKET_TTL_SECONDS},
        ip=_client_ip(request),
    )
    return R(data=SsoTicketOut(url=url, expires_in=settings.SSO_TICKET_TTL_SECONDS))


@router.post("/logout", response_model=R, summary="登出(吊销当前用户所有 token)")
def logout(
    current_user: CurrentUser,
    db: DBSession,
    request: Request,
):
    """M7: 服务端登出 —— 自增 token_version, 使该用户所有已签发 token 立即失效。

    前端在清本地 token 前调用本接口; 即便请求失败(网络异常/已 401), 前端也应继续清本地状态。
    """
    current_user.token_version = current_user.token_version + 1
    db.commit()
    log_operation(
        db, user_id=current_user.id, username=current_user.username,
        method="POST", path="/api/v1/auth/logout",
        action="认证.登出", target_type="auth", status_code=200, ip=_client_ip(request),
    )
    return R(message="已登出")

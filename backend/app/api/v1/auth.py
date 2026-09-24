"""认证路由:登录、当前用户信息"""


import json
import re
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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
from app.services.netmgr_sso import verify_ticket as verify_netmgr_ticket
from app.services.operation_logger import log_operation
from app.services.sso_ticket import build_netmgr_entry_url

router = APIRouter(prefix="/auth", tags=["认证"])


def _client_ip(request: Request) -> str | None:
    """取真实客户端 IP。

    不变量(2026-09-24 审计 IP 修复): 到达本服务的 X-Forwarded-For 已被本系统的
    nginx(csctalon-web)用 map 净化并**覆写为单值** —— 隧道流量取云端边缘注入的
    真实 IP, 局域网/ZT 直连取对端地址; 客户端自带的 XFF 一律被丢弃(不可伪造)。
    故此处"末段"即该净化值; 仍兼容多值(取末段 = 最靠近本服务的一跳)。
    无 XFF(如容器内自调用)时回退到直连 client.host。
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
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


# 安全的 next 路径正则：仅允许站内 / 开头路径，防开放重定向
_SSO_SAFE_NEXT = re.compile(r"^/[A-Za-z0-9\-._~!$&'()*+,;=:@/?%]{0,511}$")


@router.get("/sso", response_class=HTMLResponse, summary="面板跨子域 SSO 入口(验票发 JWT)")
def sso_enter(
    request: Request,
    ticket: str | None = None,
    next: str = "/",
    db: Session = Depends(get_db),
):
    """netmgr 面板 HMAC 票据验票 → 发本系统 JWT → 引导页写 localStorage 跳转。

    方向：**本系统是消费方**，netmgr 面板(panel.kimirock.top)是身份源。
      - 无票：302 回面板签票(面板有会话会立刻签新票，无感知)
      - 坏票/过期：403(不回面板，避免坏票循环)
      - 好票：验 sub 对应账号存在且 active → 签 JWT → 返回引导 HTML 写 localStorage 后跳转

    与 /auth/sso/ticket(本系统→面板方向)是独立链路，不共享密钥。
    """
    safe_next = next if _SSO_SAFE_NEXT.match(next) else "/"
    sub = verify_netmgr_ticket(ticket) if ticket else None

    if sub is None:
        if ticket:
            # 有票但验票失败：直接 403，不回面板(避免坏票循环)
            log_operation(
                db, method="GET", path="/api/v1/auth/sso",
                action="认证.SSO验票失败", target_type="auth", status_code=403,
                detail={"reason": "invalid_ticket"}, ip=_client_ip(request),
            )
            return JSONResponse(
                {"detail": "invalid or expired SSO ticket"}, status_code=403
            )
        # 无票：回面板签票。
        # 2026-09-23 止血: 反向 SSO(netmgr 面板→本系统)需两侧同时就绪 —— 面板侧
        # GET /sso/csctalon 签发端 + 本侧 NETMGR_SSO_SECRET。未配置密钥说明该链路从未
        # 启用(此前无条件外跳, 把登录过期用户甩到面板 404 页)。未启用 → 回本站登录页。
        if not settings.NETMGR_SSO_SECRET:
            return RedirectResponse("/login?expired=1")
        panel_url = settings.NETMGR_SSO_PANEL_URL
        return RedirectResponse(
            f"{panel_url}?{urlencode({'next': safe_next})}"
        )

    # 验票成功：按 sub 查账号(sub = 面板固定映射的用户名)
    user = db.scalar(select(User).where(User.username == sub))
    if not user:
        log_operation(
            db, username=sub, method="GET", path="/api/v1/auth/sso",
            action="认证.SSO拒绝", target_type="auth", status_code=403,
            detail={"reason": "user_not_found", "sub": sub}, ip=_client_ip(request),
        )
        return JSONResponse(
            {"detail": f"SSO 用户不存在: {sub}"}, status_code=403
        )
    if not user.is_active:
        log_operation(
            db, user_id=user.id, username=user.username, method="GET", path="/api/v1/auth/sso",
            action="认证.SSO拒绝", target_type="auth", status_code=403,
            detail={"reason": "账号已停用"}, ip=_client_ip(request),
        )
        return JSONResponse({"detail": "账号已停用"}, status_code=403)

    # 复用 login-json 的签发逻辑(带 tkv=token_version)
    token = create_access_token(
        user.id,
        extra={"username": user.username, "role": user.role, "name": user.display_name},
        token_version=user.token_version,
    )
    user_out = UserOut.model_validate(user)
    log_operation(
        db, user_id=user.id, username=user.username, method="GET", path="/api/v1/auth/sso",
        action="认证.SSO登录", target_type="auth", status_code=200, ip=_client_ip(request),
    )

    # 引导页：写 localStorage(与前端 stores/auth.js login 约定一致)，然后跳转。
    # user 存为 JSON 字符串(前端 JSON.parse(localStorage.getItem('user')))。
    user_json = json.dumps(user_out.model_dump(mode="json"), ensure_ascii=False)
    bootstrap = (
        "<!doctype html><meta charset=utf-8>\n"
        "<script>\n"
        f"localStorage.setItem('token', {json.dumps(token)});\n"
        f"localStorage.setItem('user', {json.dumps(user_json)});\n"
        f"location.replace({json.dumps(safe_next)});\n"
        "</script>"
    )
    return HTMLResponse(bootstrap, headers={"Cache-Control": "no-store"})


@router.get("/sso-config", summary="登录页公开配置(无需登录)")
def sso_config():
    """告诉未登录前端「netmgr 面板跨子域 SSO(面板→本系统方向)是否启用」。

    无需鉴权: 登录页/401 拦截器在没有 JWT 时据此决定「JWT 过期跳面板无感续期」
    还是「直接回登录页」。开关 = NETMGR_SSO_SECRET 已配置(面板侧 /sso/csctalon
    签发端也须同步就绪, 否则外跳必 404)。
    """
    return R(data={"netmgr_sso_enabled": bool(settings.NETMGR_SSO_SECRET)})


@router.get(
    "/sso/entries",
    response_model=R[SsoEntriesOut],
    summary="外部管理台入口可用性(仅管理员)",
)
def sso_entries(_current_user: AdminUser):
    """工单 #187/#188：告诉管理端「有哪些外部管理台入口可用」。

    只回可用性与地址，**不回票据** —— 票据在用户点击时经 POST /auth/sso/ticket 单独签发，
    避免页面加载即产生一次性凭证。地址由配置提供，不在代码中硬编码内网 IP。

    talon 回两个候选地址（办公网 / ZeroTier）：它没有公网入口，由前端按当前访问来源
    选一个，避免「点开一片空白、也不知道为什么」。
    """
    return R(data=SsoEntriesOut(
        netmgr_enabled=bool(settings.SSO_SHARED_SECRET),
        talon_url_lan=settings.SSO_TALON_URL_LAN,
        talon_url_zt=settings.SSO_TALON_URL_ZT,
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

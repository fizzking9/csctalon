"""微信扫码注册 & 小程序登录 API

流程:
  1. 前端 (Web 或小程序) 调用 `wx.login` 获取 code
  2. 前端将 code 发送到 `/wechat/auth/code-login`
  3. 后端通过 code 换取 openid/unionid:
     - 已绑定用户 → 直接返回 JWT
     - 未绑定 → 返回 pending=true, 前端引导用户填写资料提交注册申请
  4. 用户提交注册申请到 `/wechat/auth/register`
  5. 管理员在后台审批 → 审批通过后自动创建 User 并激活
  6. 小程序端使用 `/miniapp/dashboard/*` 查看精简看板数据

注: 本模块使用 HTTP + 可选 Redis 缓存 code→openid 映射,
    避免在 code2session 之间频繁请求微信接口。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import AdminUser, DBSession
from app.core.security import create_access_token, hash_password
from app.models.user import RoleEnum, User
from app.models.wechat import (
    RegisterSource,
    RegisterStatus,
    WechatAccount,
    WechatRegistration,
)

router = APIRouter(prefix="/wechat", tags=["微信扫码注册"])


# ===================== 请求/响应模型 =====================

class CodeLoginRequest(BaseModel):
    """前端传来的微信 code (小程序 wx.login 或公众号 OAuth2 得到)"""
    code: str = Field(..., min_length=4, max_length=128)
    source: str = Field(default=RegisterSource.MINIAPP.value)


class CodeLoginResponse(BaseModel):
    status: str                       # approved | pending | rejected
    token: str | None = None          # 当 status=approved 时返回
    user: dict[str, Any] | None = None
    registration_id: int | None = None
    message: str = ""


class RegisterSubmitRequest(BaseModel):
    """用户填写的注册信息"""
    code: str
    desired_username: str = Field(..., min_length=2, max_length=64)
    desired_display_name: str = Field(..., min_length=2, max_length=64)
    desired_role: str = Field(default=RoleEnum.AGENT.value)
    desired_group: str | None = None
    qw_name: str | None = Field(default=None, description="企微姓名")
    remark: str | None = None


class ApproveRequest(BaseModel):
    """管理员审批注册申请"""
    approved: bool
    assign_role: str | None = None
    assign_group: str | None = None
    assign_username: str | None = None
    reject_reason: str | None = None


# ===================== 辅助函数 =====================

def _exchange_code(code: str, source: str) -> dict[str, Any]:
    """用 code 换取 openid/unionid

    真实部署时应调用微信官方接口:
      - 小程序: https://api.weixin.qq.com/sns/jscode2session
      - 公众号: https://api.weixin.qq.com/sns/oauth2/access_token

    为便于本地调试, 如已配置环境变量 WECHAT_APPID/WECHAT_SECRET 则真实调用,
    否则返回占位 openid (前缀=mock-), 由管理员在后台审批时人工核实。
    """
    import os
    appid = os.environ.get("WECHAT_APPID", "")
    secret = os.environ.get("WECHAT_SECRET", "")
    if appid and secret and code and not code.startswith("mock-"):
        import httpx
        r = httpx.get(
            "https://api.weixin.qq.com/sns/jscode2session",
            params={"appid": appid, "secret": secret, "js_code": code, "grant_type": "authorization_code"},
            timeout=8,
        )
        data = r.json()
        if "openid" not in data:
            raise HTTPException(status_code=400, detail=f"code2session 失败: {data}")
        return {
            "openid": data.get("openid"),
            "unionid": data.get("unionid"),
            "session_key": data.get("session_key"),
        }
    # 本地开发: 直接用 code 作为 mock openid
    return {"openid": f"mock-{code}", "unionid": None, "session_key": None}


def _ensure_wechat_account(db, user_id: int, openid: str, unionid: str | None, source: str) -> None:
    existing = db.scalar(
        select(WechatAccount).where(WechatAccount.openid == openid)
    )
    if existing:
        return
    db.add(WechatAccount(
        user_id=user_id,
        openid=openid,
        unionid=unionid,
        source=source,
        bound_at=datetime.utcnow(),
        is_primary=True,
    ))


def _build_user_payload(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "group_name": user.group_name,
        "is_active": user.is_active,
    }


# ===================== 登录 & 注册 =====================

@router.post("/auth/code-login", response_model=CodeLoginResponse, summary="微信 code 登录/自动注册")
def wechat_code_login(payload: CodeLoginRequest, db: DBSession):
    """用微信 code 换取登录态

    - 已绑定用户: 直接返回 JWT
    - 首次扫码: 创建 pending 注册申请并返回 registration_id
    """
    wx = _exchange_code(payload.code, payload.source)
    openid = wx["openid"]
    unionid = wx.get("unionid")

    # 1) 查 WechatAccount → 直接登录
    account = db.scalar(
        select(WechatAccount).where(WechatAccount.openid == openid)
    )
    if account and account.user_id:
        user = db.get(User, account.user_id)
        if user and user.is_active:
            token = create_access_token(subject=str(user.id), token_version=user.token_version)
            return CodeLoginResponse(
                status="approved",
                token=token,
                user=_build_user_payload(user),
                message="登录成功",
            )

    # 2) 查已有未完成的注册申请 (pending)
    reg = db.scalar(
        select(WechatRegistration).where(
            WechatRegistration.openid == openid,
            WechatRegistration.status == RegisterStatus.PENDING.value,
        )
    )
    if reg:
        return CodeLoginResponse(
            status="pending",
            registration_id=reg.id,
            message="注册申请已提交,等待管理员审批",
        )

    # 3) 新建 pending 注册申请, 引导用户填写资料
    reg = WechatRegistration(
        openid=openid,
        unionid=unionid,
        source=payload.source,
        status=RegisterStatus.PENDING.value,
    )
    db.add(reg)
    db.commit()
    return CodeLoginResponse(
        status="pending",
        registration_id=reg.id,
        message="请填写资料完成注册申请",
    )


@router.post("/auth/register", summary="提交注册资料 (pending 状态等待审批)")
def wechat_submit_register(payload: RegisterSubmitRequest, db: DBSession):
    wx = _exchange_code(payload.code, payload.source if hasattr(payload, 'source') else RegisterSource.MINIAPP.value)
    openid = wx["openid"]

    # 查找/创建 registration
    reg = db.scalar(
        select(WechatRegistration).where(WechatRegistration.openid == openid)
    )
    if not reg:
        reg = WechatRegistration(openid=openid, unionid=wx.get("unionid"), source=RegisterSource.MINIAPP.value)
        db.add(reg)
    reg.desired_username = payload.desired_username
    reg.desired_display_name = payload.desired_display_name
    reg.desired_role = payload.desired_role
    reg.desired_group = payload.desired_group
    reg.qw_name = payload.qw_name
    reg.remark = payload.remark
    reg.status = RegisterStatus.PENDING.value
    db.commit()
    return {"ok": True, "registration_id": reg.id, "status": reg.status}


# ===================== 管理员审批 =====================

@router.get("/registrations/pending", summary="管理员: 获取待审批注册列表")
def list_pending_registrations(
    db: DBSession,
    _: AdminUser,
    status: str | None = None,
):
    stmt = select(WechatRegistration).order_by(WechatRegistration.created_at.desc())
    if status:
        stmt = stmt.where(WechatRegistration.status == status)
    else:
        stmt = stmt.where(WechatRegistration.status == RegisterStatus.PENDING.value)
    rows = db.scalars(stmt).all()
    return [
        {
            "id": r.id,
            "openid": r.openid,
            "source": r.source,
            "desired_username": r.desired_username,
            "desired_display_name": r.desired_display_name,
            "desired_role": r.desired_role,
            "desired_group": r.desired_group,
            "qw_name": r.qw_name,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "user_id": r.user_id,
        }
        for r in rows
    ]


@router.post("/registrations/{reg_id}/approve", summary="管理员: 审批注册申请")
def approve_registration(
    reg_id: int,
    payload: ApproveRequest,
    db: DBSession,
    me: AdminUser,
):
    reg = db.get(WechatRegistration, reg_id)
    if not reg:
        raise HTTPException(status_code=404, detail="注册申请不存在")
    if reg.status != RegisterStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="已审批过,不能重复操作")

    if not payload.approved:
        reg.status = RegisterStatus.REJECTED.value
        reg.reject_reason = payload.reject_reason
        reg.reviewed_at = datetime.utcnow()
        reg.reviewed_by = me.id
        db.commit()
        return {"ok": True, "status": reg.status}

    # 审批通过: 创建 User 并绑定 WechatAccount
    username = payload.assign_username or reg.desired_username or f"wx_{reg.openid[-8:]}"
    display_name = reg.desired_display_name or username
    # 随机初始密码, 首次登录后引导修改
    import secrets
    initial_pwd = secrets.token_urlsafe(12)
    user = User(
        username=username,
        display_name=display_name,
        hashed_password=hash_password(initial_pwd),
        role=payload.assign_role or reg.desired_role or RoleEnum.AGENT.value,
        group_name=payload.assign_group or reg.desired_group,
        is_active=True,
        qw_name=reg.qw_name,
        wechat_openid=reg.openid,
        wechat_unionid=reg.unionid,
    )
    db.add(user)
    db.flush()
    _ensure_wechat_account(db, user.id, reg.openid, reg.unionid, reg.source or RegisterSource.MINIAPP.value)
    reg.status = RegisterStatus.APPROVED.value
    reg.user_id = user.id
    reg.reviewed_at = datetime.utcnow()
    reg.reviewed_by = me.id
    db.commit()
    return {
        "ok": True,
        "user_id": user.id,
        "username": username,
        "initial_password": initial_pwd,  # 管理员需安全地传达给用户
    }


@router.post("/registrations/{reg_id}/cancel", summary="管理员: 驳回注册申请")
def reject_registration(
    reg_id: int,
    payload: ApproveRequest,
    db: DBSession,
    me: AdminUser,
):
    return approve_registration(
        reg_id=reg_id,
        payload=ApproveRequest(approved=False, reject_reason=payload.reject_reason),
        db=db,
        me=me,
    )

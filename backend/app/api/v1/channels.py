"""通知通道路由 - 通道配置 CRUD + 测试发送(预警推送分发用)

通道类型: inapp(站内信, 内置) / webhook(企业微信/钉钉/飞书群机器人)。
(#185 清理: 原 email 通道为未接入占位, 已删除)
"""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import AdminUser, DBSession, ManagerUser
from app.models.notification_channel import NotificationChannel
from app.schemas.common import R
from app.services.notification_channel import get_channel

router = APIRouter(prefix="/channels", tags=["通知通道"])


@router.get("", response_model=R, summary="通知通道列表")
def list_channels(db: DBSession, _: ManagerUser):
    rows = db.scalars(select(NotificationChannel).order_by(NotificationChannel.id)).all()
    return R(data={"items": [
        {
            "id": c.id,
            "code": c.code,
            "name_cn": c.name_cn,
            "channel_type": c.channel_type,
            "enabled": c.enabled,
            "config": c.config or {},
            "description": c.description,
        }
        for c in rows
    ], "total": len(rows)})


@router.post("", response_model=R, summary="新建通道")
def create_channel(payload: dict, db: DBSession, _: AdminUser):
    code = (payload.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="code 必填")
    exists = db.scalar(select(NotificationChannel).where(NotificationChannel.code == code))
    if exists:
        raise HTTPException(status_code=400, detail="通道code已存在")
    c = NotificationChannel(
        code=code,
        name_cn=payload.get("name_cn") or code,
        channel_type=payload.get("channel_type") or "webhook",
        config=payload.get("config") or {},
        enabled=bool(payload.get("enabled", True)),
        description=payload.get("description"),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return R(data={"id": c.id, "code": c.code})


@router.put("/{channel_id}", response_model=R, summary="更新通道")
def update_channel(channel_id: int, payload: dict, db: DBSession, _: AdminUser):
    c = db.get(NotificationChannel, channel_id)
    if not c:
        raise HTTPException(status_code=404, detail="通道不存在")
    for f in ("name_cn", "channel_type", "config", "enabled", "description"):
        if f in payload:
            setattr(c, f, payload[f])
    db.commit()
    return R(message="已更新")


@router.delete("/{channel_id}", response_model=R, summary="删除通道(站内信不可删)")
def delete_channel(channel_id: int, db: DBSession, _: AdminUser):
    c = db.get(NotificationChannel, channel_id)
    if not c:
        raise HTTPException(status_code=404, detail="通道不存在")
    if c.code == "inapp":
        raise HTTPException(status_code=400, detail="站内信通道不可删除")
    db.delete(c)
    db.commit()
    return R(message="已删除")


@router.post("/{code}/test", response_model=R, summary="测试发送(验证通道配置)")
def test_channel(code: str, db: DBSession, _: ManagerUser, payload: dict | None = None):
    ch = get_channel(db, code)
    if ch is None:
        raise HTTPException(status_code=400, detail="通道未启用或不存在")
    title = (payload or {}).get("title") or "测试通知"
    content = (payload or {}).get("content") or "这是一条预警通道测试消息(可忽略)。"
    r = ch.send(db, title, content, ["admin", "supervisor"])
    db.commit()
    return R(data={"channel": code, "ok": r.ok, "detail": r.detail})

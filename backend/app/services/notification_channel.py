"""通知通道抽象 - 预警通知分发(站内信/群机器人, 可扩充)

Channel 接口:
    send(db, title, content, recipients) -> ChannelResult

- InAppChannel: 站内信(复用 notifier.notify)
- WebhookChannel: 企业微信/钉钉/飞书 群机器人(通用 webhook, markdown 文本)
  (#185 清理: 原 EmailChannel 为"未接入(占位)"空壳, 前端却有可选入口, 已删除)

分发: dispatch_alert(db, alert, channels, roles, silence_window)
  - 静默时段内不推送(仅入库, 投递记录=skipped)
  - 通道失败降级: webhook 失败 → 站内信兜底(仅在有站内信接收人时)
  - 每次投递写 alert_deliveries(幂等/失败可查)
"""

import logging
import re
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alert import AlertDelivery
from app.models.notification_channel import NotificationChannel
from app.models.user import RoleEnum
from app.services.notifier import notify as _notify

logger = logging.getLogger(__name__)


class ChannelResult:
    def __init__(self, ok: bool, detail: str = "", degraded: bool = False) -> None:
        self.ok = ok
        self.detail = detail
        self.degraded = degraded


class Channel(Protocol):
    code: str

    def send(self, db: Session, title: str, content: str | None, recipients: list[str]) -> ChannelResult: ...  # noqa: E704


class InAppChannel:
    """站内信通道(默认, 必选)"""

    code = "inapp"

    def send(self, db: Session, title: str, content: str | None, recipients: list[str]) -> ChannelResult:
        count = _notify(db, None, roles=recipients, type="alert", title=title, content=content,
                        ref_type="alert")
        return ChannelResult(ok=count > 0, detail=f"站内信 {count} 条")


class WebhookChannel:
    """群机器人 webhook 通道(企业微信/钉钉/飞书通用)"""

    def __init__(self, code: str, name_cn: str, config: dict | None) -> None:
        self.code = code
        self.name_cn = name_cn
        self.config = config or {}

    def send(self, db: Session, title: str, content: str | None, recipients: list[str]) -> ChannelResult:
        url = self.config.get("webhook_url") or self.config.get("url")
        if not url:
            return ChannelResult(ok=False, detail="未配置 webhook_url")
        text = f"**【预警】** {title}"
        if content:
            text += f"\n{content}"
        try:
            import httpx
            payload = {"msgtype": "text", "text": {"content": text[:1800]}}
            resp = httpx.post(url, json=payload, timeout=10)
            ok = 200 <= resp.status_code < 300
            return ChannelResult(ok=ok, detail=f"HTTP {resp.status_code}")
        except Exception as e:  # noqa: BLE001
            return ChannelResult(ok=False, detail=f"发送异常: {e}")


_CHANNEL_FACTORIES: dict[str, type] = {}

def register_channel_type(channel_type: str, factory: type) -> None:
    _CHANNEL_FACTORIES[channel_type] = factory


def get_channel(db: Session, code: str) -> Channel | None:
    """按通道code实例化(从 DB 配置 + 通道类型工厂)"""
    row = db.scalar(select(NotificationChannel).where(NotificationChannel.code == code))
    if not row or not row.enabled:
        return None
    if row.channel_type == "inapp":
        return InAppChannel()
    if row.channel_type == "webhook":
        return WebhookChannel(row.code, row.name_cn, row.config)
    return None


def _in_silence(silence_window: dict | None) -> bool:
    """是否处于静默时段(北京时间 HH:MM~HH:MM)"""
    if not silence_window:
        return False
    from datetime import timedelta
    now = datetime.now(timezone.utc).astimezone(timedelta(hours=8))
    start = silence_window.get("start")
    end = silence_window.get("end")
    if not start or not end or not re.match(r"^\d{2}:\d{2}$", start) or not re.match(r"^\d{2}:\d{2}$", end):
        return False
    cur = now.strftime("%H:%M")
    if start <= end:
        return start <= cur <= end
    # 跨天时段(如 22:00~06:00)
    return cur >= start or cur <= end


def dispatch_alert(
    db: Session,
    alert_id: int,
    title: str,
    content: str | None,
    channels: list[str] | None,
    roles: list[str] | None,
    silence_window: dict | None = None,
) -> list[dict]:
    """按规则配置分发预警通知, 写投递记录. 返回投递结果列表"""
    channels = channels or ["inapp"]
    roles = roles or [RoleEnum.ADMIN.value, RoleEnum.SUPERVISOR.value]
    results: list[dict] = []
    silenced = _in_silence(silence_window)

    for code in channels:
        ch = get_channel(db, code)
        if ch is None:
            db.add(AlertDelivery(alert_id=alert_id, channel=code, status="skipped", detail="通道未启用"))
            results.append({"channel": code, "status": "skipped"})
            continue
        if silenced:
            db.add(AlertDelivery(alert_id=alert_id, channel=code, status="silenced", detail="静默时段不推送"))
            results.append({"channel": code, "status": "silenced"})
            continue
        try:
            r = ch.send(db, title, content, roles)
            status = "success" if r.ok else "failed"
            if not r.ok and code != "inapp":
                # 群机器人失败 → 降级站内信兜底
                try:
                    fallback = InAppChannel().send(db, title, content, roles)
                    status = "degraded"
                    r.detail = f"降级站内信({fallback.detail})"
                except Exception as e:  # noqa: BLE001
                    r.detail = f"{r.detail}; 降级失败 {e}"
            db.add(AlertDelivery(alert_id=alert_id, channel=code, status=status, detail=r.detail))
            results.append({"channel": code, "status": status})
        except Exception as e:  # noqa: BLE001
            db.add(AlertDelivery(alert_id=alert_id, channel=code, status="failed", detail=str(e)))
            results.append({"channel": code, "status": "failed"})
    db.flush()
    return results

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

    def send(self, db: Session, title: str, content: str | None, recipients: list[str],
             prefix: str = "【预警】") -> ChannelResult: ...  # noqa: E704


class InAppChannel:
    """站内信通道(默认, 必选)"""

    code = "inapp"

    def send(self, db: Session, title: str, content: str | None, recipients: list[str],
             prefix: str = "【预警】") -> ChannelResult:
        count = _notify(db, None, roles=recipients, type="alert", title=title, content=content,
                        ref_type="alert")
        return ChannelResult(ok=count > 0, detail=f"站内信 {count} 条")


class WebhookChannel:
    """群机器人 webhook 通道(企业微信/钉钉/飞书通用)"""

    def __init__(self, code: str, name_cn: str, config: dict | None) -> None:
        self.code = code
        self.name_cn = name_cn
        self.config = config or {}

    def send(self, db: Session, title: str, content: str | None, recipients: list[str],
             prefix: str = "【预警】") -> ChannelResult:
        url = self.config.get("webhook_url") or self.config.get("url")
        if not url:
            return ChannelResult(ok=False, detail="未配置 webhook_url")
        text = f"{prefix} {title}"
        if content:
            text += f"\n{content}"
        try:
            import httpx
            # 三家机器人文本消息 payload 不同(2026-09-29 补飞书):
            #   企微/钉钉: {"msgtype":"text","text":{"content":...}}
            #   飞书:      {"msg_type":"text","content":{"text":...}}
            # config.provider: wecom(默认)/dingtalk/feishu
            provider = (self.config.get("provider") or "wecom").lower()
            if provider == "feishu":
                payload = {"msg_type": "text", "content": {"text": text[:1800]}}
            else:
                payload = {"msgtype": "text", "text": {"content": text[:1800]}}
            resp = httpx.post(url, json=payload, timeout=10)
            ok = 200 <= resp.status_code < 300
            # 飞书业务错误也在 200 响应体里(code/StatusCode 任一非 0 即失败)
            if ok and provider == "feishu":
                try:
                    body = resp.json()
                    biz_codes = [body[k] for k in ("code", "StatusCode") if k in body]
                    if any(c not in (0, None) for c in biz_codes):
                        ok = False
                except Exception:  # noqa: BLE001 非 JSON 响应按 HTTP 状态处理
                    pass
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


# ─────────────────────────────────────────────────────────────────────
# 系统级 error 事件外发(2026-09-29)
# ─────────────────────────────────────────────────────────────────────
# 与业务预警 dispatch_alert 的区别:
#   - 系统事件(LLM 鉴权挂/Redis 挂等)是"系统自身故障", 无 alert 规则/静默时段概念,
#     夜间也必须送达(事故常在非工作时间发生), 故不做静默时段判断;
#   - 频率已在 record_system_event 的 dedup_minutes 处收敛, 这里只负责送达;
#   - fire-and-forget 后台线程: webhook 同步 httpx 最长 10s, 绝不能阻塞业务提交;
#   - 通道选择: 优先 code='system_alert' 的启用 webhook(可与业务预警分群);
#     未配置则回退任意启用的 webhook 通道; 一个都没有 → 静默跳过。
def dispatch_system_alert_async(
    *, title: str, summary: str | None, category: str, action: str
) -> None:
    """在后台 daemon 线程把一条系统告警推送到群机器人。任何异常都不外抛。"""
    import threading
    from datetime import timedelta

    def _run() -> None:
        # 延迟导入避免循环依赖(SessionLocal 在大部分运行期已可用)
        from app.core.database import SessionLocal

        db = SessionLocal()
        try:
            row = db.scalar(select(NotificationChannel).where(
                NotificationChannel.code == "system_alert",
                NotificationChannel.channel_type == "webhook",
                NotificationChannel.enabled.is_(True),
            ))
            if row is None:
                row = db.scalar(select(NotificationChannel).where(
                    NotificationChannel.channel_type == "webhook",
                    NotificationChannel.enabled.is_(True),
                ).order_by(NotificationChannel.id))
            if row is None:
                return  # 未配置任何 webhook 通道 → 不外发(站内角标仍可见)
            ch = WebhookChannel(row.code, row.name_cn, row.config)
            now_cn = datetime.now(timezone.utc).astimezone(
                timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
            body = f"时间: {now_cn}\n来源: {category}/{action}"
            if summary:
                body += f"\n{summary}"
            r = ch.send(db, title, body, [], prefix="【系统告警】")
            if not r.ok:
                logger.warning("系统告警 webhook 投递失败(channel=%s): %s", row.code, r.detail)
        except Exception as e:  # noqa: BLE001
            logger.warning("系统告警外发异常(不影响主流程): %s", e)
        finally:
            db.close()

    threading.Thread(target=_run, name="sys-alert-webhook", daemon=True).start()

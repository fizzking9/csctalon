"""BMS 验证码交互登录会话管理器

闪猫等数据源登录需要验证码,无法全自动识别。
采用「前端交互验证码输入」流程:
  1. 前端 POST /fetch/captcha-login/start → 后端启动 Playwright,进入登录页,截取验证码图片,返回 session_id + base64
  2. 用户在前端页面输入验证码 → POST /fetch/captcha-login/submit {session_id, captcha}
  3. 后端填账号密码+验证码提交登录,成功后保存 storage_state 文件供后续抓取复用

会话最多存活 10 分钟(用户不提交就自动清理)。
"""

import asyncio
import base64
import logging
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 会话最大存活秒数(用户读验证码+输入的允许时间)
SESSION_TTL = 600  # 10 分钟


@dataclass
class CaptchaSession:
    session_id: str
    data_type: str
    # Playwright 资源
    playwright: object = None  # async_playwright() 返回值
    browser: object = None
    context: object = None
    page: object = None
    # 登录配置(运行时填充)
    base_url: str = ""
    page_path: str = ""
    username: str = ""
    password: str = ""
    created_at: float = field(default_factory=time.time)
    state_path: Path = None  # storage_state 保存路径

    async def cleanup(self):
        if self.page:
            with suppress(Exception):
                await self.page.close()
        if self.context:
            with suppress(Exception):
                await self.context.close()
        if self.browser:
            with suppress(Exception):
                await self.browser.close()
        if self.playwright:
            with suppress(Exception):
                await self.playwright.stop()
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None


class CaptchaSessionManager:
    """简单内存会话管理: session_id → CaptchaSession"""

    def __init__(self):
        self._sessions: dict[str, CaptchaSession] = {}

    def _new_id(self) -> str:
        return uuid.uuid4().hex[:16]

    def _is_expired(self, s: CaptchaSession) -> bool:
        return (time.time() - s.created_at) > SESSION_TTL

    async def _purge_expired(self):
        expired = [sid for sid, s in self._sessions.items() if self._is_expired(s)]
        for sid in expired:
            logger.info(f"[CaptchaSession] 会话过期自动清理: {sid}")
            s = self._sessions.pop(sid, None)
            if s:
                await s.cleanup()

    async def create(self, data_type: str, base_url: str, page_path: str,
                     username: str, password: str, state_path: Path) -> CaptchaSession:
        await self._purge_expired()
        sid = self._new_id()
        sess = CaptchaSession(
            session_id=sid,
            data_type=data_type,
            base_url=base_url,
            page_path=page_path,
            username=username,
            password=password,
            state_path=state_path,
        )
        self._sessions[sid] = sess
        logger.info(f"[CaptchaSession] 创建会话 {sid} 用于 {data_type}")
        return sess

    def get(self, session_id: str) -> Optional[CaptchaSession]:
        s = self._sessions.get(session_id)
        if s and self._is_expired(s):
            asyncio.create_task(self.remove(session_id))
            return None
        return s

    async def remove(self, session_id: str):
        s = self._sessions.pop(session_id, None)
        if s:
            await s.cleanup()
            logger.info(f"[CaptchaSession] 会话已销毁: {session_id}")


# 全局单例
captcha_session_manager = CaptchaSessionManager()


def bytes_to_data_uri(png_bytes: bytes) -> str:
    """把 PNG 字节转成 data: URI(前端 <img src=...> 直接可用)"""
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"

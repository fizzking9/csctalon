"""SCRM 微信聚合平台 - OpenAPI 客户端封装

核心职责:
  1. Token 获取与缓存(提前 5 分钟刷新,避免请求时 401)
  2. 统一异常包装: ScrmApiError (网络/业务码非0/超时)
  3. 三个关键会话列表 + 客服列表 + 聊天记录详情(聊天记录提供方法但暂不入库,
     用户明确要求"聊天记录量大,需要时再查")

与 BMS 模式对齐:
  - 默认种子 SCRM_DATA_SOURCES 写入 SyncSourceConfig(同 BMS_DATA_SOURCES 方式)
  - 凭证复用 SyncSourceConfig.username = Fernet(clientId), password = Fernet(clientSecret)
  - fetch_mode = "openapi" 在 sync_source_config_service 种子分流到 scrm_sync
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---- SCRM 同步源默认种子(首次启动写入 SyncSourceConfig;运行时以 DB 为准) ----
#  data_type 命名: scrm_operator / scrm_session_private / scrm_session_group / scrm_session_virtual
#  说明: 三种会话列表都是"会话",但接口不同、参数不同。同步服务内合并为 scrm_sessions 表,
#        这里分开 seed 便于在同步配置中独立开关 / 独立时间范围。
#
# options 扩展参数说明:
#   fetch_mode:
#     - "openapi"     → 走 ClientId/ClientSecret 的 OpenAPI(原默认方式,稳定)
#     - "playwright"  → 走网页 Playwright 登录抓取 (验证码类型由 captcha_type 决定)
#     - "dual"        → 先尝试 OpenAPI,失败或无凭证时自动回退 Playwright(双保险推荐)
#   captcha_type:
#     - "none" → 无验证码
#     - "text" → 普通字符验证码 (闪猫同款,ddddocr 直接识别)
#     - "math" → 数学计算式 (SCRM 网页登录,识别后计算结果再填入)
#   其他选择器/路径:可留空走自动探测。
SCRM_DATA_SOURCES: dict[str, dict] = {
    "scrm_operator": {
        "name": "SCRM-客服账号列表",
        "base_url": "https://gateway.bilinl.com",
        "page_path": "/thirdparty/partner/merChat/getMerChatOperator",  # 仅文档参考
        "default_sync": True,
        # 2026-08-06 修复: 默认抓取模式从 openapi → playwright (OpenAPI 在部分环境会
        # 被平台风控拦截/限流,同时客户反馈 OpenAPI 抓取频率过低缺今日增量, Playwright
        # 直接模拟浏览器导出,更稳定拿到与后台页面一致的今日数据, 默认使用 Playwright)
        "fetch_mode": "playwright",
        "requires_captcha": True,  # Playwright 登录需要数学公式验证码
        "options": {
            "fetch_mode": "playwright",
            "captcha_type": "math",
            "login_url_path": "/login",
            "web_operator_entry_path": "/index#/system/user-manage",
            "captcha_img_selector": "img.show-code-img",
            "username_placeholder": "请输入登录账号/手机号",
            "password_placeholder": "请输入密码",
            "captcha_input_placeholder": "请输入验证码",
            "submit_button_text": "登录",
        },
    },
    "scrm_session_private": {
        "name": "SCRM-私聊会话列表",
        "base_url": "https://gateway.bilinl.com",
        "page_path": "/thirdparty/customer/chatSession/privateChat/getUserChatListByPage",
        "default_sync": True,
        "fetch_mode": "playwright",
        "requires_captcha": True,
        "options": {
            "fetch_mode": "playwright",
            "captcha_type": "math",
            "login_url_path": "/login",
            "web_session_entry_path": "/index#/chat/session-list",
            "captcha_img_selector": "img.show-code-img",
            "submit_button_text": "登录",
        },
    },
    "scrm_session_group": {
        "name": "SCRM-群聊会话列表",
        "base_url": "https://gateway.bilinl.com",
        "page_path": "/thirdparty/customer/chatSession/groupChat/getUserChatListByGroup",
        "default_sync": False,  # 群聊先关闭,后续业务确认口径后开启
        "fetch_mode": "playwright",
        "requires_captcha": True,
        "options": {
            "fetch_mode": "playwright",
            "captcha_type": "math",
            "login_url_path": "/login",
            "captcha_img_selector": "img.show-code-img",
            "submit_button_text": "登录",
        },
    },
    "scrm_session_virtual": {
        "name": "SCRM-虚拟会话列表",
        "base_url": "https://gateway.bilinl.com",
        "page_path": "/thirdparty/customer/chatSession/virtualSession/getVirtualChatSessionPage",
        "default_sync": False,  # 虚拟会话先关闭,待确认是否算客服工作量
        "fetch_mode": "playwright",
        "requires_captcha": True,
        "options": {
            "fetch_mode": "playwright",
            "captcha_type": "math",
            "login_url_path": "/login",
            "captcha_img_selector": "img.show-code-img",
            "submit_button_text": "登录",
        },
    },
}

SCRM_PAGE_SIZE = 100  # 列表分页大小(平台默认 100)
SCRM_TIMEOUT = 30  # 单次 HTTP 调用超时秒
SCRM_TOKEN_REFRESH_BEFORE = 300  # token 过期前 5 分钟刷新


class ScrmApiError(RuntimeError):
    """SCRM 接口错误: 网络异常 / 业务 code != 0 / 超时"""

    def __init__(self, message: str, code: int | None = None, raw: Any = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.raw = raw


@dataclass
class ScrmPageResult:
    """分页列表结果"""
    records: list[dict]
    total: int
    size: int
    current: int
    pages: int


class ScrmClient:
    """SCRM OpenAPI 客户端 - 线程/协程安全,单例复用

    用法:
        async with ScrmClient(base_url, client_id, client_secret) as c:
            token = await c.get_token()
            ops = await c.list_operators()
            priv = await c.list_private_sessions(start, end)
    """

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        timeout: int = SCRM_TIMEOUT,
    ):
        if not base_url:
            raise ValueError("ScrmClient: base_url 不能为空")
        if not client_id or not client_secret:
            raise ValueError("ScrmClient: clientId/clientSecret 未配置(请在同步设置页填写)")
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout

        self._client: httpx.AsyncClient | None = None
        # token 缓存: (access_token, expire_at_monotonic_seconds, expiredTime 原值)
        self._token_cache: tuple[str, float, int] | None = None
        self._token_lock = asyncio.Lock()

    async def __aenter__(self) -> "ScrmClient":
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client:
            try:
                await self._client.aclose()
            finally:
                self._client = None

    # ---- 内部基础: token 管理 + 通用请求 ----
    async def get_token(self, force_refresh: bool = False) -> str:
        """获取 access_token,自动缓存与过期前刷新(线程安全: asyncio.Lock)

        真实 SCRM 平台 (百引AI / gateway.bilinl.com):
          接口: POST /thirdparty/user/login/client
          请求 Body: {clientId, clientSecret}
          响应: code=0, data.value (token字符串),
                data.expiredTime (毫秒级timestamp,如 1785989965000)
                data.expired (可读字符串,如 "2026-08-06 12:19:25")
          业务接口鉴权: Authorization: Bearer <data.value> (Header)
        """
        if not force_refresh and self._token_cache:
            tok, expire_mono, _ = self._token_cache
            if time.monotonic() < expire_mono:
                return tok
        async with self._token_lock:
            # double-check: 等待期间可能被其他协程刷新
            if not force_refresh and self._token_cache:
                tok, expire_mono, _ = self._token_cache
                if time.monotonic() < expire_mono:
                    return tok
            try:
                resp = await self._client.post(
                    f"{self.base_url}/thirdparty/user/login/client",
                    json={
                        "clientId": self.client_id,
                        "clientSecret": self.client_secret,
                    },
                )
                resp.raise_for_status()
            except httpx.HTTPError as e:
                raise ScrmApiError(f"获取 token 网络失败: {e}") from e
            payload = self._parse_payload(resp, action="login/client")
            # 兼容字段: value 是真实平台名,同时保留 accessToken / token 作为 fallback
            access_token = (
                payload.get("value")
                or payload.get("token")
                or payload.get("accessToken")
            )
            # 过期时间: expiredTime 可能是毫秒 timestamp,也可能是秒;做归一化
            expired_raw = payload.get("expiredTime")
            expired_sec: int
            if isinstance(expired_raw, (int, float)):
                # > 10^12 视为毫秒
                expired_sec = int(expired_raw / 1000) if expired_raw > 10_000_000_000 else int(expired_raw)
            else:
                expired_sec = 7200  # 默认 2 小时
            if not access_token:
                raise ScrmApiError(f"获取 token 响应缺少 value/token: {payload!r}")
            # 缓存并提前 5 分钟过期
            ttl = max(1, expired_sec - int(time.time()))
            cache_ttl = max(1, ttl - SCRM_TOKEN_REFRESH_BEFORE)
            expire_mono = time.monotonic() + cache_ttl
            self._token_cache = (access_token, expire_mono, expired_sec)
            logger.info(f"[scrm] token 获取成功,ttl≈{ttl}s,缓存 {cache_ttl}s (提前 5min 刷新)")
            return access_token

    @staticmethod
    def _parse_payload(resp: httpx.Response, action: str) -> dict:
        """统一解析响应体: code==0 → 返回 data;非 0 → 抛 ScrmApiError"""
        try:
            body = resp.json()
        except ValueError as e:
            raise ScrmApiError(f"[{action}] 响应非 JSON: {resp.text[:200]!r}") from e
        code = body.get("code")
        if code != 0:
            msg = body.get("message") or body.get("msg") or body.get("msg") or str(body)
            raise ScrmApiError(f"[{action}] 业务错误 code={code}: {msg}", code=code, raw=body)
        data = body.get("data")
        if not isinstance(data, dict) and not isinstance(data, list):
            # 有些接口 data 直接是 None(例如空分页)——兼容为 {}
            if data is None:
                return {}
            raise ScrmApiError(f"[{action}] data 非 dict/list: {data!r}")
        return data if isinstance(data, dict) else {"records": data}

    async def _post(self, path: str, body: dict, action: str, *, _retry: int = 1) -> dict:
        """通用 POST(自动注入 Authorization: Bearer <token>;
        401/403/2012(鉴权失效) 时强制刷新 token 后重试一次)"""
        if not self._client:
            raise RuntimeError("ScrmClient 未初始化,请使用 async with")
        token = await self.get_token()
        body = dict(body)
        # 真实 SCRM: Header 注入 Bearer token (不再放在 body.accessToken 中)
        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = await self._client.post(
                f"{self.base_url}{path}", json=body, headers=headers,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403) and _retry > 0:
                logger.warning(f"[scrm] {action} HTTP {e.response.status_code},强制刷新 token 后重试")
                await self.get_token(force_refresh=True)
                return await self._post(path, body, action, _retry=_retry - 1)
            raise ScrmApiError(f"[{action}] HTTP {e.response.status_code}: {e}") from e
        except httpx.HTTPError as e:
            raise ScrmApiError(f"[{action}] 网络失败: {e}") from e
        try:
            return self._parse_payload(resp, action=action)
        except ScrmApiError as api_err:
            # token 失效触发重刷: HTTP 层 401/403,或业务码 = 401/2012(鉴权失败)
            msg_lower = (api_err.message or "").lower()
            token_invalid = (
                api_err.code in (401, 2012)
                or "token" in msg_lower
                or "认证" in (api_err.message or "")
                or "登录" in (api_err.message or "")
            )
            if _retry > 0 and token_invalid:
                logger.warning(
                    f"[scrm] {action} token 业务失效(码={api_err.code},msg={api_err.message}),刷新后重试"
                )
                await self.get_token(force_refresh=True)
                return await self._post(path, body, action, _retry=_retry - 1)
            raise

    # ---- 对外方法:按接口文档 1:1 映射 ----

    async def list_operators(self) -> list[dict]:
        """获取商户下所有客服账号
        GET/POST? → 文档统一为 POST;按接口列表地址的 Action=merChatOperator
        /thirdparty/partner/merChat/getMerChatOperator
        返回 records: [{operatorId, operatorName, headImg, status, deptName, ...}]
        """
        data = await self._post(
            "/thirdparty/partner/merChat/getMerChatOperator",
            body={},
            action="getMerChatOperator",
        )
        records = data.get("records") or data.get("list") or data.get("operators") or []
        if isinstance(records, list):
            return records
        # 某些接口直接返回 list
        return list(data.values()) if isinstance(data, dict) and not records else []

    # ---- 三个会话列表接口(分页自动合并) ----

    async def list_private_sessions(
        self,
        start_time: str,
        end_time: str,
        operator_id: str | None = None,
        chat_type: int | None = None,
    ) -> list[dict]:
        """私聊会话列表(自动翻页)
        /thirdparty/customer/chatSession/privateChat/getUserChatListByPage
        """
        return await self._auto_page(
            "/thirdparty/customer/chatSession/privateChat/getUserChatListByPage",
            base_body={
                "startTime": start_time,
                "endTime": end_time,
                **({"operatorId": operator_id} if operator_id else {}),
                **({"chatType": chat_type} if chat_type is not None else {}),
            },
            action="getUserChatListByPage(private)",
        )

    async def list_group_sessions(
        self, start_time: str, end_time: str, operator_id: str | None = None,
    ) -> list[dict]:
        """群聊会话列表(自动翻页)
        /thirdparty/customer/chatSession/groupChat/getUserChatListByGroup
        """
        return await self._auto_page(
            "/thirdparty/customer/chatSession/groupChat/getUserChatListByGroup",
            base_body={
                "startTime": start_time,
                "endTime": end_time,
                **({"operatorId": operator_id} if operator_id else {}),
            },
            action="getUserChatListByGroup",
        )

    async def list_virtual_sessions(
        self, start_time: str, end_time: str, operator_id: str | None = None,
    ) -> list[dict]:
        """虚拟会话列表(自动翻页)
        /thirdparty/customer/chatSession/virtualSession/getVirtualChatSessionPage
        """
        return await self._auto_page(
            "/thirdparty/customer/chatSession/virtualSession/getVirtualChatSessionPage",
            base_body={
                "startTime": start_time,
                "endTime": end_time,
                **({"operatorId": operator_id} if operator_id else {}),
            },
            action="getVirtualChatSessionPage",
        )

    async def _auto_page(self, path: str, base_body: dict, action: str) -> list[dict]:
        """自动翻页: 每页 SCRM_PAGE_SIZE 条, 拉到 pages 为止"""
        all_records: list[dict] = []
        current = 1
        while True:
            body = dict(base_body)
            body["current"] = current
            body["size"] = SCRM_PAGE_SIZE
            data = await self._post(path, body=body, action=action)
            records = data.get("records") or data.get("list") or []
            if not isinstance(records, list):
                records = []
            all_records.extend(records)
            total = int(data.get("total") or 0)
            pages = int(data.get("pages") or 0)
            size = int(data.get("size") or SCRM_PAGE_SIZE)
            # 任何终止条件: records 为空 / current >= pages / total <= len(all_records)
            if (
                not records
                or current >= pages
                or (total and len(all_records) >= total)
                or (size and len(records) < size)
            ):
                break
            current += 1
            # 简单限速(1 次/50ms, 避免平台 0 QPS 说明下的意外)
            await asyncio.sleep(0.05)
        return all_records

    # ---- 聊天记录详情(用户说量大暂不入库,但方法保留给"按需查询"场景) ----
    async def get_chat_messages(self, session_id: str) -> list[dict]:
        """按会话ID拉取聊天记录明细(按需查询,不入库)
        /thirdparty/customer/chatMsg/getChatMsgDetail
        """
        data = await self._post(
            "/thirdparty/customer/chatMsg/getChatMsgDetail",
            body={"sessionId": session_id},
            action="getChatMsgDetail",
        )
        records = data.get("records") or data.get("list") or data.get("msgs") or []
        return records if isinstance(records, list) else []

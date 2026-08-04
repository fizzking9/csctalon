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
SCRM_DATA_SOURCES: dict[str, dict] = {
    "scrm_operator": {
        "name": "SCRM-客服账号列表",
        "base_url": "https://scrm.baiyinqifu.com",
        "page_path": "/thirdparty/partner/merChat/getMerChatOperator",  # 仅文档参考
        "default_sync": True,
        "fetch_mode": "openapi",
    },
    "scrm_session_private": {
        "name": "SCRM-私聊会话列表",
        "base_url": "https://scrm.baiyinqifu.com",
        "page_path": "/thirdparty/customer/chatSession/privateChat/getUserChatListByPage",
        "default_sync": True,
        "fetch_mode": "openapi",
    },
    "scrm_session_group": {
        "name": "SCRM-群聊会话列表",
        "base_url": "https://scrm.baiyinqifu.com",
        "page_path": "/thirdparty/customer/chatSession/groupChat/getUserChatListByGroup",
        "default_sync": False,  # 群聊先关闭,后续业务确认口径后开启
        "fetch_mode": "openapi",
    },
    "scrm_session_virtual": {
        "name": "SCRM-虚拟会话列表",
        "base_url": "https://scrm.baiyinqifu.com",
        "page_path": "/thirdparty/customer/chatSession/virtualSession/getVirtualChatSessionPage",
        "default_sync": False,  # 虚拟会话先关闭,待确认是否算客服工作量
        "fetch_mode": "openapi",
    },
}

SCRM_PAGE_SIZE = 100  # 列表分页大小(平台默认 100)
SCRM_TIMEOUT = 30  # 单次 HTTP 调用超时秒
SCRM_TOKEN_REFRESH_BEFORE = 300  # token 过期前 5 分钟刷新


class ScrmApiError(RuntimeError):
    """SCRM 接口错误: 网络异常 / 业务 code != 0 / 超时"""

    def __init__(self, message: str, code: int | None = None, raw: Any = None):
        super().__init__(message)
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

        /thirdparty/partner/getOauthToken 文档:
          请求: POST, Body: clientId, clientSecret, userName?, passWord?
          响应: code=0, data.accessToken + data.expiredTime(秒,通常 7200)
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
                    f"{self.base_url}/thirdparty/partner/getOauthToken",
                    json={
                        "clientId": self.client_id,
                        "clientSecret": self.client_secret,
                    },
                )
                resp.raise_for_status()
            except httpx.HTTPError as e:
                raise ScrmApiError(f"获取 token 网络失败: {e}") from e
            payload = self._parse_payload(resp, action="getOauthToken")
            access_token = payload.get("accessToken")
            expired_sec = int(payload.get("expiredTime") or 7200)
            if not access_token:
                raise ScrmApiError(f"获取 token 响应缺少 accessToken: {payload!r}")
            # 缓存并提前 5 分钟过期
            expire_mono = time.monotonic() + max(1, expired_sec - SCRM_TOKEN_REFRESH_BEFORE)
            self._token_cache = (access_token, expire_mono, expired_sec)
            logger.info(f"[scrm] token 获取成功,缓存 {expired_sec}s (提前 5min 刷新)")
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
        """通用 POST(自动注入 accessToken;401 时 force 刷新 token 重试一次)"""
        if not self._client:
            raise RuntimeError("ScrmClient 未初始化,请使用 async with")
        token = await self.get_token()
        body = dict(body)
        body.setdefault("accessToken", token)
        try:
            resp = await self._client.post(f"{self.base_url}{path}", json=body)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403) and _retry > 0:
                logger.warning(f"[scrm] {action} 401/403,强制刷新 token 后重试")
                await self.get_token(force_refresh=True)
                return await self._post(path, body, action, _retry=_retry - 1)
            raise ScrmApiError(f"[{action}] HTTP {e.response.status_code}: {e}") from e
        except httpx.HTTPError as e:
            raise ScrmApiError(f"[{action}] 网络失败: {e}") from e
        try:
            return self._parse_payload(resp, action=action)
        except ScrmApiError as api_err:
            # token 过期但 code != 0(某些网关会用业务码表达)—— 重刷一次
            if _retry > 0 and (
                (api_err.code == 401)
                or (isinstance(api_err.message, str) and "token" in api_err.message.lower())
            ):
                logger.warning(f"[scrm] {action} token 业务失效(码={api_err.code}),刷新后重试")
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

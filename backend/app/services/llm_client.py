"""LLM 网关 - OpenAI 兼容客户端 (A-20260904-003 LOCKED)

- 契约: POST {LLM_BASE_URL}/chat/completions (DeepSeek 官方不带 /v1, 本地 Ollama 带 /v1;
  约定 LLM_BASE_URL 给"完整前缀", 本模块统一拼 /chat/completions)。
- 模型可插拔: 一期=deepseek-v4-flash(官方); 二期切本地 Ollama/vLLM 仅改配置。
- 错误分级: 5xx/429/超时 → transient(指数退避重试); 4xx(不含429, 含鉴权) → permanent 不重试。
- 单 worker: async httpx 直接 await; 不阻塞事件循环(纯 I/O)。
- 禁止把 LLM_API_KEY 打进日志/异常。
"""

import asyncio
import logging
import random
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class LLMError(Exception):
    """LLM 调用错误(permanent 不再重试)。"""

    def __init__(self, message: str, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def _endpoint() -> str:
    base = (settings.LLM_BASE_URL or "").rstrip("/")
    if not base:
        raise LLMError("LLM_BASE_URL 未配置", status_code=503)
    return base + "/chat/completions"


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if settings.LLM_API_KEY:
        h["Authorization"] = f"Bearer {settings.LLM_API_KEY}"
    return h


def _payload(messages: list[dict], *, temperature: float = 0.2, max_tokens: int | None = None) -> dict:
    """组装请求体; DeepSeek thinking 模式按配置 disabled/low/high。"""
    body: dict[str, Any] = {
        "model": settings.LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if max_tokens:
        body["max_tokens"] = max_tokens
    thinking = (settings.LLM_THINKING or "disabled").strip().lower()
    if thinking == "disabled":
        # DeepSeek 支持关闭思考; 其它 OpenAI 兼容服务忽略未知键会报错? 部分兼容服务允许 extra。
        # 为兼容 OpenAI 原生(无 thinking 字段), 用 try 在 400 时去掉重试由调用方处理。
        body["thinking"] = {"type": "disabled"}
    elif thinking in ("low", "high"):
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = thinking
    return body


def _backoff_sleep(attempt: int) -> float:
    base = float(getattr(settings, "LLM_RETRY_BASE_S", 1.0) or 1.0)
    cap = float(getattr(settings, "LLM_RETRY_CAP_S", 30.0) or 30.0)
    return min(base * (2 ** attempt), cap) * (0.8 + 0.4 * random.random())


async def chat_completion(messages: list[dict], *, temperature: float = 0.2,
                          max_tokens: int | None = None, timeout_s: float | None = None,
                          transport: httpx.AsyncBaseTransport | None = None) -> str:
    """调用 chat/completions 返回文本内容。

    transient(5xx/429/超时/连接错) 按 LLM_RETRY_MAX 指数退避重试; permanent(4xx) 抛 LLMError。
    transport 仅供测试注入(httpx.MockTransport); 生产为 None 走真实网络。
    """
    url = _endpoint()
    headers = _headers()
    body = _payload(messages, temperature=temperature, max_tokens=max_tokens)
    timeout = httpx.Timeout(
        connect=10.0,
        read=timeout_s or float(getattr(settings, "LLM_TIMEOUT_S", 120.0) or 120.0),
        write=30.0,
        pool=10.0,
    )
    retry_max = int(getattr(settings, "LLM_RETRY_MAX", 2) or 2)
    last_err: LLMError | None = None
    client_kwargs: dict = {"timeout": timeout}
    if transport is not None:
        client_kwargs["transport"] = transport
    async with httpx.AsyncClient(**client_kwargs) as client:
        for attempt in range(retry_max + 1):
            try:
                resp = await client.post(url, headers=headers, json=body)
            except httpx.TimeoutException as e:
                last_err = LLMError(f"LLM 请求超时: {e.__class__.__name__}", retryable=True)
                logger.warning("llm timeout attempt=%s err=%s", attempt + 1, e.__class__.__name__)
            except httpx.HTTPError as e:
                last_err = LLMError(f"LLM 请求异常: {e.__class__.__name__}", retryable=True)
                logger.warning("llm http err attempt=%s err=%s", attempt + 1, e.__class__.__name__)
            else:
                if resp.status_code == 200:
                    return _extract_content(resp.json())
                if resp.status_code in _RETRYABLE_STATUS:
                    last_err = LLMError(f"LLM 返回 {resp.status_code}", status_code=resp.status_code, retryable=True)
                    logger.warning("llm retryable status=%s attempt=%s body=%s",
                                   resp.status_code, attempt + 1, _safe_body(resp.text))
                else:
                    # permanent(4xx 等): 不重试。注意不要把 key/敏感 body 打进日志。
                    detail = _safe_body(resp.text)
                    logger.error("llm permanent status=%s detail=%s", resp.status_code, detail)
                    raise LLMError(f"LLM 调用失败(status={resp.status_code})", status_code=resp.status_code)
            if attempt < retry_max:
                await asyncio.sleep(_backoff_sleep(attempt))
    raise last_err or LLMError("LLM 调用失败(未知)")


def _extract_content(data: dict) -> str:
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError("LLM 响应缺少 choices[0].message.content")


def _safe_body(text: str) -> str:
    """响应体截断脱敏(避免超长/含敏感), 仅用于日志。"""
    if not text:
        return ""
    return text.replace("\n", " ")[:200]

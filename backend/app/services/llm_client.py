"""LLM 网关 - OpenAI 兼容客户端 (A-20260904-003 LOCKED / 工单 #108 多 Provider)

两种模式:
  1. DB 多 Provider 模式(默认): 从 llm_providers 表读 enabled+healthy 列表,
     按 priority 升序遍历, transient 错误(5xx/429/超时)降级下一个 Provider。
     库为空/全 disabled → 自动回退 .env 单源模式(向后兼容)。
  2. .env 单源模式: 库为空时使用 settings.LLM_*(原 v1.0 行为)。

- 契约: POST {base_url}/chat/completions (DeepSeek 官方不带 /v1, 本地 Ollama/Talon 带 /v1;
  约定 base_url 给"完整前缀", 本模块统一拼 /chat/completions)。
- 错误分级: 5xx/429/超时 → transient(降级下一个 Provider 或重试); 4xx → permanent。
- 调用日志: 每次调用一行审计(成功/失败均记), 不打 messages/响应正文(防泄露)。
- 失败计数: 连续失败 N 次自动标 unhealthy(下次跳过); 成功一次清零。
- 单 worker: async httpx 直接 await; 不阻塞事件循环(纯 I/O)。
- 禁止把 LLM_API_KEY 打进日志/异常。
"""

import asyncio
import logging
import random
import re
import time
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class LLMError(Exception):
    """LLM 调用错误(permanent 不再重试)。"""

    def __init__(self, message: str, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class LLMResult(str):
    """LLM 响应字符串 + usage 元数据(工单 #116)。

    继承 str 保证旧调用零侵入:
      - `content = await chat_completion(...)` → content 是 LLMResult 实例
      - `content == "分析结果"` → True(继承 str 的 == 仍然比对字符串本身)
      - `content.strip()/replace()/split()` → 返回纯 str(丢失 usage, 旧调用本就只用 str)
      - `content.usage` → 拿到 {prompt_tokens, completion_tokens, total_tokens} dict(新代码用)
    """

    usage: dict | None = None  # {prompt_tokens, completion_tokens, total_tokens}

    def __new__(cls, content: str, usage: dict | None = None) -> "LLMResult":
        s = super().__new__(cls, content or "")
        s.usage = usage
        return s


# ─────────────────────────────────────────────────────────────────────
# 公共辅助
# ─────────────────────────────────────────────────────────────────────
def _endpoint(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if not base:
        raise LLMError("base_url 未配置", status_code=503)
    return base + "/chat/completions"


def _headers(api_key: str | None) -> dict:
    h = {"Content-Type": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def _payload(messages: list[dict], model: str, *, temperature: float = 0.2,
             max_tokens: int | None = None, thinking: str = "disabled") -> dict:
    """组装请求体; thinking 模式 disabled/low/high(DeepSeek 专用, 其它服务忽略未知键)。

    兼容性: 部分非 DeepSeek 服务对未知 thinking 键会 400, llm_client 在 400 时
    去掉 thinking 重试一次(由调用方继续重试逻辑)。

    #111 (2026-09-15): ollama+Qwen3 系列默认 thinking=on 会烧光 max_tokens 致 content 空。
    #113 (2026-09-15): chat_template_kwargs 在长 prompt 下被 talon-api 丢弃; 改用顶层
    think:False, 由 talon-api 透传到 ollama /api/chat 原生字段。
    DeepSeek/OpenAI 官方端点忽略未知字段(无害), ollama 端点正确禁用 thinking。
    """
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if max_tokens:
        body["max_tokens"] = max_tokens
    thinking = (thinking or "disabled").strip().lower()
    if thinking == "disabled":
        body["thinking"] = {"type": "disabled"}
        # Qwen3 系列在 ollama 里禁用 thinking 的正确字段; 对 DeepSeek/OpenAI 官方端点无害
        body["chat_template_kwargs"] = {"enable_thinking": False}
        # #113 ollama /api/chat 原生字段, talon-api openai_compat 透传; 长 prompt 下最稳
        body["think"] = False
    elif thinking in ("low", "high"):
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = thinking
    return body


def _backoff_sleep(attempt: int, *, base_s: float = 1.0, cap_s: float = 30.0) -> float:
    return min(base_s * (2 ** attempt), cap_s) * (0.8 + 0.4 * random.random())


# ─────────────────────────────────────────────────────────────────────
# 超时自洽性保障 (2026-09-20, 事故: 主力模型 30% 调用超时)
# ─────────────────────────────────────────────────────────────────────
# 背景: 生产实测 qwen3.8:27b 的 decode 速度约 20~27 t/s, 而多处调用点配置为
#   max_tokens=1500/3000 + timeout_s=60/120 —— 60s 内根本产不出 1500 token。
#   结果: 27b 的 352 次调用中 104 次 ReadTimeout (29.5%), 且这些失败会触发
#   provider 降级, 污染调用链。
#
# 修复: 在发请求前，按 max_tokens 反推最低所需超时, 取 max(调用方 timeout, 所需值)。
#   这样调用方仍可"放宽"超时, 但不会再出现"按物理上不可能完成的时限去等"。
#
# 参数依据（生产实测, 见 talon 仓库 docs/reports/技术与经济性评估-20260920.md）:
#   qwen3.8:27b  (Dense) : decode ~20 t/s (慢路径保守值)
#   qwen3-coder:30b (MoE): decode ~77 t/s
#   本地生效值 ctx=65536 后速度稳定; 云端 DeepSeek 无需此保护(其延迟低且可缓存)。
#
# 采用最慢模型的解码速度作为兜底基准, 保证任何 provider 都不会被"过早掐断"。
_SLOWEST_DECODE_TPS = 15.0   # 保守基准: 低于实测最慢值(27b 长文本约 20 t/s)
_TIMEOUT_HEADROOM_S = 30.0   # 预留 prefill + 排队 + 网络 的开销
_MIN_TIMEOUT_S = 30.0        # 绝对下限, 避免过短


def _ensure_timeout_fits_tokens(timeout_s: float | None, max_tokens: int | None,
                                *, base_url: str = "") -> float | None:
    """按 max_tokens 反推最低合理超时, 返回 max(调用方值, 所需值)。

    例: max_tokens=1500 → 1500/15 + 30 = 130s。若调用方传 60s, 返回 130s。
    云端 API（api.deepseek.com 等）不适用此保护 —— 其延迟低且无本地慢模型问题。
    """
    if not max_tokens:
        return timeout_s
    if not _is_local_endpoint(base_url):
        return timeout_s
    needed = max(max_tokens / _SLOWEST_DECODE_TPS + _TIMEOUT_HEADROOM_S, _MIN_TIMEOUT_S)
    if timeout_s is None:
        return needed
    return max(float(timeout_s), needed)


def _is_local_endpoint(base_url: str) -> bool:
    """判断是否为本地自托管端点（talon 集群 / ollama / 内网 IP）。

    云端端点（https 公网域名）无需超时放宽 —— 其延迟低且有 prefix cache。
    """
    u = (base_url or "").strip().lower()
    if not u:
        return False
    # 本机 / 内网 IP / 已知自托管域名 → 本地
    if "127.0.0.1" in u or "localhost" in u or "host.docker.internal" in u:
        return True
    # 私有网段 10.x / 192.168.x / 172.16-31.x
    m = re.search(r'//(\d{1,3})\.(\d{1,3})\.', u)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a == 10 or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31):
            return True
    return False


def _extract_content(data: dict) -> tuple[str, dict | None]:
    """从 OpenAI 兼容响应取 (content, usage)。

    usage 字段(工单 #116): OpenAI/DeepSeek 官方端点 + ollama OpenAI 兼容端点都返回
    `{"prompt_tokens", "completion_tokens", "total_tokens"}` 字段。若服务端不返回 usage,
    保持 None(落库时 NULL, 不影响主流程)。
    """
    try:
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError("LLM 响应缺少 choices[0].message.content")
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
    return content, usage


def _safe_body(text: str) -> str:
    """响应体截断脱敏(避免超长/含敏感), 仅用于日志。"""
    if not text:
        return ""
    return text.replace("\n", " ")[:200]


def _prompt_chars(messages: list[dict]) -> int:
    """统计 messages 拼接后字符数(用于审计日志, 不含敏感上下文)。"""
    try:
        return sum(len(m.get("content", "")) for m in messages)
    except Exception:  # noqa: BLE001
        return 0


# ─────────────────────────────────────────────────────────────────────
# 单 Provider 单次调用(含指数退避重试)
# ─────────────────────────────────────────────────────────────────────
async def _call_single_provider(
    *, url: str, headers: dict, body: dict,
    timeout_s: float, retry_max: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[LLMResult | None, LLMError | None, int | None, str]:
    """对单个 Provider 发起调用, transient 错误按 retry_max 指数退避重试。

    返回 (content, last_err, status_code, err_class)
      - content 成功(非空 LLMResult 含 usage), last_err=None
      - 失败 content=None, last_err 非 None
    status_code: 成功=200; 失败=HTTP code(超时/连接错=None)
    err_class: 异常类名(便于审计日志分类)
    """
    timeout = httpx.Timeout(
        connect=10.0, read=timeout_s, write=30.0, pool=10.0,
    )
    client_kwargs: dict = {"timeout": timeout}
    if transport is not None:
        client_kwargs["transport"] = transport
    last_err: LLMError | None = None
    last_status: int | None = None
    last_err_class: str = ""
    async with httpx.AsyncClient(**client_kwargs) as client:
        for attempt in range(retry_max + 1):
            try:
                resp = await client.post(url, headers=headers, json=body)
            except httpx.TimeoutException as e:
                last_err = LLMError(f"LLM 请求超时: {e.__class__.__name__}", retryable=True)
                last_err_class = e.__class__.__name__
                logger.warning("llm timeout attempt=%s err=%s", attempt + 1, last_err_class)
            except httpx.HTTPError as e:
                last_err = LLMError(f"LLM 请求异常: {e.__class__.__name__}", retryable=True)
                last_err_class = e.__class__.__name__
                logger.warning("llm http err attempt=%s err=%s", attempt + 1, last_err_class)
            else:
                if resp.status_code == 200:
                    content, usage = _extract_content(resp.json())
                    return LLMResult(content, usage), None, 200, ""
                if resp.status_code in _RETRYABLE_STATUS:
                    last_err = LLMError(f"LLM 返回 {resp.status_code}",
                                         status_code=resp.status_code, retryable=True)
                    last_status = resp.status_code
                    last_err_class = f"http_{resp.status_code}"
                    logger.warning("llm retryable status=%s attempt=%s body=%s",
                                    resp.status_code, attempt + 1, _safe_body(resp.text))
                else:
                    # permanent(4xx 等): 不重试, 返回降级标志由上层决定
                    detail = _safe_body(resp.text)
                    logger.error("llm permanent status=%s detail=%s", resp.status_code, detail)
                    return (None,
                            LLMError(f"LLM 调用失败(status={resp.status_code})",
                                      status_code=resp.status_code),
                            resp.status_code, f"http_{resp.status_code}")
            if attempt < retry_max:
                await asyncio.sleep(_backoff_sleep(attempt))
    return None, last_err, last_status, last_err_class or "unknown"


# ─────────────────────────────────────────────────────────────────────
# 主入口: chat_completion
# ─────────────────────────────────────────────────────────────────────
async def chat_completion(
    messages: list[dict], *,
    temperature: float = 0.2,
    max_tokens: int | None = None,
    timeout_s: float | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    db: Session | None = None,
    source: str | None = None,
    provider_code: "str | list[str] | None" = None,
) -> LLMResult:
    """调用 chat/completions 返回文本内容。

    优先级(工单 #108): DB 多 Provider 模式 → 库为空时回退 .env 单源模式。

    DB 模式:
      - 默认链: 按 priority 升序遍历 enabled+healthy 且非 pregen_only 的 Provider
      - #131 provider_code 为 str(报告预生成): 指定 provider 置顶, 其后保留常规链
        作 transient 降级兜底(pregen_only 的慢模型只对显式指定可见)
      - 2026-09-21 provider_code 为 list(能力档位): 首选 → 备选 → 云端兜底,
        按序置顶且逐个跳过不可用者; 由 `llm_tiers.resolve_tier()` 生成
      - transient 错误(5xx/429/超时/连接错) 降级下一个 Provider
      - permanent 错误(4xx) 立即抛 LLMError
      - 全部 Provider 失败 → 抛 LLMError(聚合所有错误信息)
      - 每次调用一行审计日志(成功/失败均记)
      - 连续失败 N 次标 unhealthy(降级); 成功一次清零

    .env 单源模式(向后兼容, db=None 或库空时):
      - 用 settings.LLM_BASE_URL/LLM_API_KEY/LLM_MODEL/LLM_THINKING/LLM_TIMEOUT_S/LLM_RETRY_MAX
      - transient 错误指数退避重试; permanent 抛 LLMError

    transport 仅供测试注入(httpx.MockTransport); 生产为 None 走真实网络。
    db 仅供 DB 模式; None 走 .env 模式(向后兼容)。
    source 用于审计日志来源(如 ai_analyze / llm_time_parse)。
    provider_code 用于预生成按模板指定专用慢模型(#131); None 走实时默认链。
    2026-09-21: 也接受 list[str] 表达能力档位链(首选/备选/兜底), 见 app/services/llm_tiers.py。

    返回 LLMResult(继承 str, 旧调用零侵入): `.usage` 取 token 统计(工单 #116)。
    """
    t0 = time.perf_counter()
    prompt_chars = _prompt_chars(messages)

    # 取 Provider 列表(DB 模式)
    providers: list[dict] = []
    if db is not None:
        try:
            from app.services import llm_provider_service
            # #131: provider_code 非空(预生成)时指定 provider 置顶 + 常规链兜底;
            # 为 None(实时问答)时只取非 pregen_only 的默认链
            providers = llm_provider_service.get_provider_chain(db, provider_code)
        except Exception as e:  # noqa: BLE001  查库失败不阻塞业务, 回退 .env
            logger.warning("llm get_provider_chain failed, fallback to .env: %s", e)
            providers = []

    # ── DB 多 Provider 模式 ──
    if providers:
        return await _call_multi_provider(
            providers, messages, temperature=temperature, max_tokens=max_tokens,
            override_timeout_s=timeout_s, transport=transport,
            db=db, source=source, t0=t0, prompt_chars=prompt_chars,
        )

    # ── .env 单源模式(向后兼容, 同 v1.0) ──
    return await _call_env_single(
        messages, temperature=temperature, max_tokens=max_tokens,
        override_timeout_s=timeout_s, transport=transport,
        db=db, source=source, t0=t0, prompt_chars=prompt_chars,
    )


async def _call_multi_provider(
    providers: list[dict], messages: list[dict], *,
    temperature: float, max_tokens: int | None,
    override_timeout_s: float | None,
    transport: httpx.AsyncBaseTransport | None,
    db: Session, source: str | None, t0: float, prompt_chars: int,
) -> LLMResult:
    """DB 多 Provider 调用: 按优先级遍历, transient 降级。"""
    from app.services import llm_provider_service

    last_err: LLMError | None = None
    last_err_class: str = ""
    last_status: int | None = None

    for p in providers:
        pid = p["id"]; pcode = p["code"]; model = p["model"]
        url = _endpoint(p["base_url"])
        headers = _headers(p["api_key"])
        body = _payload(messages, model, temperature=temperature,
                         max_tokens=max_tokens, thinking=p["thinking"])
        # timeout_s 优先用 override, 否则用 Provider 自己的 timeout_s
        t_s = override_timeout_s if override_timeout_s is not None else p["timeout_s"]
        # 超时自洽保障: 防止 max_tokens 大到 timeout 根本装不下（27b 30% 超时的根因）
        t_s = _ensure_timeout_fits_tokens(t_s, max_tokens, base_url=p["base_url"])
        logger.info("llm call provider=%s(%s) model=%s timeout_s=%.1f max_tokens=%s",
                     pid, pcode, model, t_s, max_tokens)
        content, err, status, err_cls = await _call_single_provider(
            url=url, headers=headers, body=body,
            timeout_s=t_s, retry_max=p["retry_max"], transport=transport,
        )
        duration_ms = int((time.perf_counter() - t0) * 1000)
        if content is not None:
            # 成功: 清零失败计数 + 自愈 healthy + 写审计日志
            llm_provider_service.mark_provider_succeeded(db, pid)
            llm_provider_service.record_call_log(
                db, provider_id=pid, provider_code=pcode, model=model,
                status="success", status_code=status,
                duration_ms=duration_ms, prompt_chars=prompt_chars,
                out_chars=len(content), source=source,
            )
            logger.info("llm call ok provider=%s(%s) model=%s duration_ms=%d out_chars=%d",
                         pid, pcode, model, duration_ms, len(content))
            return content
        # 失败: 区分 transient(降级) / permanent(立即抛)
        last_err = err
        last_err_class = err_cls
        last_status = status
        is_permanent = err is not None and not err.retryable
        # 写审计日志(本次 Provider 调用失败)
        log_status = "permanent" if is_permanent else "transient"
        llm_provider_service.record_call_log(
            db, provider_id=pid, provider_code=pcode, model=model,
            status=log_status, status_code=status,
            duration_ms=duration_ms, prompt_chars=prompt_chars,
            error_message=str(err) if err else "unknown",
            source=source,
        )
        if is_permanent:
            # permanent(4xx) 立即抛, 不降级(继续下个 Provider 也是同样的鉴权/参数错)
            raise err
        # transient: 失败计数 +1, 降级下一个 Provider
        llm_provider_service.mark_provider_failed(db, pid, fail_threshold=p["fail_threshold"])
        logger.warning("llm call transient fail provider=%s(%s) status=%s err=%s; 降级下一个",
                        pid, pcode, status, err_cls)

    # 所有 Provider 都失败 → 抛最后一个错误(附最后错误类型/状态码, 便于运维定位)
    duration_ms = int((time.perf_counter() - t0) * 1000)
    if last_err is not None:
        logger.error("llm all providers failed count=%s last=%s status=%s duration_ms=%d",
                     len(providers), last_err_class, last_status, duration_ms)
        raise last_err
    raise LLMError(f"LLM 调用失败: 无可用 Provider(共 {len(providers)} 个, 全部失败)")


async def _call_env_single(
    messages: list[dict], *, temperature: float, max_tokens: int | None,
    override_timeout_s: float | None,
    transport: httpx.AsyncBaseTransport | None,
    db: Session | None, source: str | None, t0: float, prompt_chars: int,
) -> LLMResult:
    """.env 单源模式(向后兼容, 同 v1.0)。"""
    url = _endpoint(settings.LLM_BASE_URL or "")
    headers = _headers(settings.LLM_API_KEY or "")
    body = _payload(messages, settings.LLM_MODEL or "",
                     temperature=temperature, max_tokens=max_tokens,
                     thinking=getattr(settings, "LLM_THINKING", "disabled"))
    timeout_s = override_timeout_s if override_timeout_s is not None else \
        float(getattr(settings, "LLM_TIMEOUT_S", 120.0) or 120.0)
    # 超时自洽保障（同多 Provider 路径）: 防止 max_tokens 装不下
    timeout_s = _ensure_timeout_fits_tokens(
        timeout_s, max_tokens, base_url=settings.LLM_BASE_URL or "")
    retry_max = int(getattr(settings, "LLM_RETRY_MAX", 2) or 2)
    content, err, status, err_cls = await _call_single_provider(
        url=url, headers=headers, body=body,
        timeout_s=timeout_s, retry_max=retry_max, transport=transport,
    )
    duration_ms = int((time.perf_counter() - t0) * 1000)
    # 写审计日志(.env 模式 provider_id=None)
    if db is not None:
        try:
            from app.services import llm_provider_service
            llm_provider_service.record_call_log(
                db, provider_id=None, provider_code=".env",
                model=settings.LLM_MODEL,
                status="success" if content is not None else (
                    "permanent" if (err and not err.retryable) else "transient"),
                status_code=status, duration_ms=duration_ms,
                prompt_chars=prompt_chars,
                out_chars=len(content) if content is not None else None,
                error_message=str(err) if err else None,
                source=source,
            )
        except Exception as e:  # noqa: BLE001  日志写失败不阻塞
            logger.warning("llm call log (.env) write failed: %s", e)
    if content is not None:
        logger.info("llm call ok (.env) model=%s duration_ms=%d out_chars=%d",
                     settings.LLM_MODEL, duration_ms, len(content))
        return content
    if err is not None:
        raise err
    raise LLMError("LLM 调用失败(未知)")

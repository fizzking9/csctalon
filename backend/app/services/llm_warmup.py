"""LLM 模型预热(工单 #108)

设计:
- 后端启动后异步发最小 prompt 触发 Talon/Ollama 模型加载到显存
- 失败不影响业务(只是首次冷启动稍慢)
- 也可由管理端 /llm-providers/warmup 手动触发

为什么需要:
- qwen3-coder:30b 首次加载到显存需 10~30s, 之后热响应 1~3s
- 通过启动预热, 用户首次问句走热路径
- 配合 Talon OLLAMA_KEEP_ALIVE=24h 让模型常驻(由运维在 Talon .env 设)
"""

import asyncio
import logging

from sqlalchemy import select

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.models.llm_provider import LlmProvider

logger = logging.getLogger(__name__)

# 进程级单飞锁, 避免并发预热
_warmup_lock = asyncio.Lock()


async def warmup_all_providers() -> dict:
    """对所有 enabled Provider 发最小 prompt, 触发模型加载到显存。

    返回 {provider_code: {ok, duration_ms, error}} 汇总
    """
    if _warmup_lock.locked():
        logger.info("llm warmup: 已有预热在跑, 跳过")
        return {"skipped": "already_running"}
    async with _warmup_lock:
        from app.core.database import SessionLocal
        from app.services.llm_client import _call_single_provider, _endpoint, _headers, _payload
        db = SessionLocal()
        result = {}
        try:
            rows = db.scalars(
                select(LlmProvider)
                .where(LlmProvider.enabled.is_(True))
                .order_by(LlmProvider.priority, LlmProvider.id)
            ).all()
            if not rows:
                logger.info("llm warmup: 无 enabled Provider, 跳过")
                return {"skipped": "no_enabled_provider"}
            logger.info("llm warmup: 开始预热 %d 个 Provider", len(rows))
            for r in rows:
                api_key = decrypt_secret(r.api_key_cipher) or ""
                # 用最小 prompt, max_tokens=8, 避免触发长生成
                body = _payload(
                    [{"role": "user", "content": "ping"}],
                    r.model, temperature=0.0, max_tokens=8,
                    thinking=r.thinking,
                )
                try:
                    content, err, status, _ = await _call_single_provider(
                        url=_endpoint(r.base_url),
                        headers=_headers(api_key),
                        body=body,
                        timeout_s=min(60.0, float(r.timeout_s)),
                        retry_max=0,  # 预热不重试, 失败就跳过
                    )
                    ok = content is not None
                    result[r.code] = {"ok": ok, "error": str(err) if err else None}
                    if ok:
                        logger.info("llm warmup ok: %s(%s) model=%s", r.id, r.code, r.model)
                    else:
                        logger.warning("llm warmup fail: %s(%s) model=%s err=%s",
                                       r.id, r.code, r.model, err)
                except Exception as e:  # noqa: BLE001
                    result[r.code] = {"ok": False, "error": str(e)[:200]}
                    logger.warning("llm warmup exception: %s(%s) %s", r.id, r.code, e)
        finally:
            db.close()
        return result


def warmup_on_startup() -> None:
    """启动后异步预热(非阻塞, 失败不影响业务)。

    延迟 30s 启动, 避开启动初期 DB/Redis 抢资源。
    """
    if not settings.LLM_ENABLED:
        logger.debug("llm warmup: LLM_ENABLED=false, 跳过")
        return
    async def _delayed():
        await asyncio.sleep(30)
        try:
            await warmup_all_providers()
        except Exception as e:  # noqa: BLE001
            logger.warning("llm warmup_on_startup failed: %s", e)
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_delayed())
    except RuntimeError:
        # 无事件循环(不在 async 上下文) → 跳过
        logger.debug("llm warmup: 无事件循环, 跳过")

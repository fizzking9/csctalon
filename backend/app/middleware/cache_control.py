"""动态 API 防缓存中间件

背景(2026-09-08, #36): 公网入口(云端 1Panel OpenResty 反代 → frp → 本地)对 GET
响应做了 proxy_cache(x-cache: HIT), 导致实时查询(采集日志等)命中旧缓存——
手动同步成功写入新日志, 但页面看到的还是缓存里的旧列表/旧标签。

本中间件对 /api/ 动态响应统一加 Cache-Control: no-store, 使网关/浏览器不缓存:
  - 若云端 OpenResty 尊重上游 Cache-Control → 治本(所有接口不再被缓存)
  - 前端已另行对采集日志请求加 _t 随机参数兜底(绕缓存键)
"""

import logging

from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

_NO_CACHE_HEADERS = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


class NoCacheMiddleware(BaseHTTPMiddleware):
    """给 /api/ 动态响应附加防缓存响应头"""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            for k, v in _NO_CACHE_HEADERS.items():
                response.headers[k] = v
        return response

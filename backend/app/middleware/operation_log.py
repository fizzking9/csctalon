"""操作日志中间件 - 自动记录所有变更请求(POST/PUT/PATCH/DELETE)

策略:
- 只记录变更请求,GET/HEAD/OPTIONS 不记录(避免噪音)
- 跳过 /auth/login(由 auth 处理器显式记录,捕获用户名)、/health、/docs
- 从 JWT 解析操作人(无 JWT 则 user_id=None)
- 响应完成后在线程池中写入日志(不阻塞异步主循环)
- 任何异常都吞掉,绝不影响主业务响应
"""

import time

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.database import SessionLocal
from app.core.security import decode_access_token
from app.models.operation_log import OperationLog
from app.services.operation_logger import derive_action, should_log


def _write_log(
    user_id: int | None,
    username: str | None,
    method: str,
    path: str,
    status_code: int,
    ip: str | None,
) -> None:
    """同步写入一条日志(在线程池中执行)"""
    db = SessionLocal()
    try:
        action, target_type, target_id = derive_action(method, path)
        db.add(
            OperationLog(
                user_id=user_id,
                username=username,
                method=method,
                path=path,
                action=action,
                target_type=target_type,
                target_id=target_id,
                status_code=status_code,
                ip=ip,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


class OperationLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        method = request.method
        path = request.url.path

        if not should_log(method, path):
            return await call_next(request)

        # 从 Authorization 头解析操作人
        user_id: int | None = None
        username: str | None = None
        auth = request.headers.get("authorization")
        if auth and auth.lower().startswith("bearer "):
            payload = decode_access_token(auth.split(" ", 1)[1])
            if payload:
                try:
                    user_id = int(payload.get("sub"))  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    user_id = None
                username = payload.get("username")

        # 客户端 IP(支持反向代理)
        xff = request.headers.get("x-forwarded-for")
        if xff:
            # 2026-09-24 审计 IP 修复: nginx(csctalon-web)已用 map 把 XFF **覆写为单值**
            # 净化值(隧道取云端边缘注入的真实 IP / 直连取对端地址, 客户端伪造的 XFF 被丢弃)。
            # 取末段 = 该净化值; 仍兼容多值(末段 = 最靠近本服务的一跳)。口径与 auth.py 对齐。
            # 旧实现见 2026-08-20 SEC-06: 当时 nginx 追加式透传, 末段=网关(172.21.0.1)导致审计失真。
            ip = xff.split(",")[-1].strip()
        else:
            ip = request.client.host if request.client else None

        # 执行请求并捕获状态码
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            # 请求本身异常,仍记录(状态码留空)
            status_code = None
            try:
                await run_in_threadpool(_write_log, user_id, username, method, path, status_code, ip)
            except Exception:
                pass
            raise

        # 响应完成后异步写日志(不阻塞响应)
        try:
            await run_in_threadpool(_write_log, user_id, username, method, path, status_code, ip)
        except Exception:
            pass

        # 记录耗时(仅日志,不入库)
        _ = time.time()

        return response

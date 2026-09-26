# sagitta/Log/middleware.py
"""TraceMiddleware：trace_id 贯通 + HTTP 耗时。

这是**唯一**的 trace 中间件。此前 `App/bootstrap.py` 里另有一个用
`uuid4().hex[:16]` 且**不调 `set_trace()`** 的版本，而它是实际被装配的那个，
导致 `_TRACE_ID` 永远停在默认值 `"-"`：响应头不注入、日志无 trace、
慢 SQL 日志的 trace 字段全空。两套并存是重写未清理的残留，已收敛。

本版本用 `Utils/trace.gen_trace_id()`（uuid7，时间有序，便于按 trace 排序检索）。
"""
import time
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..Log.logger import reset_trace, set_trace
from ..Utils.trace import gen_trace_id

TRACE_HEADER = "X-Trace-Id"
REQUEST_TIME_HEADER = "X-Request-Time"

# trace_id 的合法字符集：只允许 hex / 字母数字 / 连字符，限长。
# 该值会进入日志与响应头，必须挡住换行（日志伪造）与超长（日志膨胀）。
_TRACE_MAX_LEN = 64
_TRACE_ALLOWED = set("0123456789abcdefABCDEF-_.")


def _logger():
    """延迟取 logger。

    不能在模块级 `logger = LogBackend.get_logger(...)`：那会在
    `LogBackend.init()` 之前就把后端固定成 stdlib，之后配 loguru 也不生效。
    """
    from .backend import LogBackend
    return LogBackend.get_logger("sg.http")


def _sanitize_tid(raw: str) -> str:
    tid = raw.strip()[:_TRACE_MAX_LEN]
    if not tid:
        return ""
    if any(ch not in _TRACE_ALLOWED for ch in tid):
        return ""
    return tid


class TraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request,
                       call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        # 客户端可带 X-Trace-Id 实现全链路串联（网关 → 后端 → 下游）
        incoming = _sanitize_tid(request.headers.get(TRACE_HEADER) or "")
        tid = incoming or gen_trace_id()

        # 必须在 call_next 之前设置：Starlette 在 call_next 内部才创建
        # 下游任务，anyio 会在创建时复制当前 context，trace 才能传进
        # endpoint 与 SQLAlchemy 的慢 SQL 监听器。
        token = set_trace(tid)
        request.state.trace_id = tid
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            ms = (time.perf_counter() - t0) * 1000
            _logger().exception("%s %s -> EXC %.0fms", request.method,
                                request.url.path, ms)
            raise
        else:
            ms = (time.perf_counter() - t0) * 1000
            request.state.status_code = response.status_code
            response.headers[TRACE_HEADER] = tid
            response.headers[REQUEST_TIME_HEADER] = f"{ms:.1f}ms"
            _logger().info("%s %s %d %.0fms", request.method, request.url.path,
                           response.status_code, ms)
            return response
        finally:
            reset_trace(token)


__all__ = ["TraceMiddleware", "TRACE_HEADER", "REQUEST_TIME_HEADER"]

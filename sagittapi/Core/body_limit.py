# sagitta/Core/body_limit.py
"""请求体大小限制中间件。

防止超大请求体导致 OOM。在 Content-Length 头可用时直接拦截。

设计决策：
  * **只检查 Content-Length**：这是最可靠的拦截点，在 ASGI body 解析
    之前就能判断。分块传输（chunked, 无 Content-Length）由路由层
    自行控制——ASGI receive 层的包装方案在 BaseHTTPMiddleware 下
    无法正确返回 Response 对象（receive 的返回值是 ASGI message dict，
    不是 HTTP Response），会破坏 Starlette 的内部协议。
  * Content-Length 缺失时放行，不阻止合法的流式上传。大文件上传场景
    应在路由层用 request.body() + len() 检查，或依赖前置网关
    （Nginx client_max_body_size）。

配置（conf/config.yaml）::

    server:
      max_body_bytes: 10485760   # 10MB，0 表示不限制
"""
from typing import Awaitable, Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..Core.errors import SgError


class PayloadTooLargeError(SgError):
    """请求体超过大小限制。"""

    code = "PAYLOAD_TOO_LARGE"
    status_code = 413
    default_message = "请求体过大"


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """检查 Content-Length，超过限制直接返回 413。

    中间件层不能 raise SgError（详见 SgError.to_json_response 的注释），
    因此直接返回 JSONResponse。
    """

    SKIP_METHODS = {"GET", "HEAD", "OPTIONS"}

    def __init__(self, app, *, max_body_bytes: int = 0,
                 skip_paths: Optional[list] = None):
        super().__init__(app)
        self.max_body_bytes = max_body_bytes
        self.skip_paths = set(skip_paths or ["/health", "/ready"])

    async def dispatch(self, request: Request,
                       call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if self.max_body_bytes <= 0:
            return await call_next(request)

        if request.method in self.SKIP_METHODS:
            return await call_next(request)

        path = request.url.path
        if path in self.skip_paths:
            return await call_next(request)

        cl = request.headers.get("Content-Length")
        if cl:
            try:
                cl_int = int(cl)
            except ValueError:
                # Content-Length 非法：交给下游 400 处理
                return await call_next(request)
            if cl_int > self.max_body_bytes:
                return PayloadTooLargeError(
                    f"请求体大小 {cl_int} 超过限制 {self.max_body_bytes} 字节"
                ).to_json_response()

        # 无 Content-Length（分块传输 / GET 无 body）→ 放行
        return await call_next(request)


__all__ = ["BodySizeLimitMiddleware", "PayloadTooLargeError"]

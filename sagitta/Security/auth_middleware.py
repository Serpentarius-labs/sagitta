import re
from contextvars import ContextVar
from typing import Callable, List, Optional

from fastapi import Depends, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from ..Core.errors import UnauthorizedError, ForbiddenError
from .jwt import JWT, TokenError, TokenPayload

_ctx_user: ContextVar[Optional[TokenPayload]] = ContextVar("user", default=None)

ACCESS_COOKIE = "sg_access"
REFRESH_COOKIE = "sg_refresh"


# ---------- 模块级 cookie 工具（控制器直接用这个）----------

def set_auth_cookies(
    resp: Response,
    *,
    access: Optional[str] = None,
    refresh: Optional[str] = None,
    max_age_access: int = 900,
    max_age_refresh: int = 604800,
    secure: bool = False,
    samesite: str = "strict",
    domain: Optional[str] = None,
) -> None:
    kw = {"httponly": True, "secure": secure, "samesite": samesite, "path": "/", "domain": domain}
    if access:
        resp.set_cookie(ACCESS_COOKIE, access, max_age=max_age_access, **kw)
    if refresh:
        resp.set_cookie(REFRESH_COOKIE, refresh, max_age=max_age_refresh, **kw)


def clear_auth_cookies(
    resp: Response,
    *,
    secure: bool = False,
    samesite: str = "strict",
    domain: Optional[str] = None,
) -> None:
    kw = {"httponly": True, "secure": secure, "samesite": samesite, "path": "/", "domain": domain, "max_age": 0}
    resp.set_cookie(ACCESS_COOKIE, "", **kw)
    resp.set_cookie(REFRESH_COOKIE, "", **kw)


# ---------- 中间件 ----------

class JwtAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        skip_prefixes: Optional[List[str]] = None,
        secure: bool = False,
        same_site: str = "strict",
        domain: Optional[str] = None,
    ):
        super().__init__(app)
        # 只跳过登录本身：/auth/logout、/auth/me 必须携带令牌，不能整段跳过。
        self.skip = re.compile("|".join(map(re.escape, skip_prefixes or [
            "/health", "/ready", "/docs", "/openapi.json", "/openapi/", "/auth/login",
        ])))
        self.secure = secure
        self.same_site = same_site
        self.domain = domain

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if self.skip.match(request.url.path):
            return await call_next(request)
        token = request.cookies.get(ACCESS_COOKIE) or self._bearer(request)
        payload: Optional[TokenPayload] = None
        if token:
            try:
                candidate = JWT.verify(token)
                # 吊销检查是登出安全闭环的一部分：只验签名会让注销后的令牌
                # 在剩余 TTL（access 15 分钟）内继续有效，logout 形同虚设。
                # 每请求一次 Redis EXISTS，本地约 0.2ms，可接受。
                if not await JWT.is_revoked(candidate.jti):
                    payload = candidate
            except TokenError:
                pass
        _ctx_user.set(payload)
        request.state.user = payload
        return await call_next(request)

    @staticmethod
    def _bearer(request: Request) -> Optional[str]:
        auth = request.headers.get("Authorization", "")
        return auth[7:] if auth.startswith("Bearer ") else None

    def set_cookies(self, resp: Response, *, access: Optional[str] = None, refresh: Optional[str] = None) -> None:
        set_auth_cookies(resp, access=access, refresh=refresh, secure=self.secure, samesite=self.same_site, domain=self.domain)

    def clear_cookies(self, resp: Response) -> None:
        clear_auth_cookies(resp, secure=self.secure, samesite=self.same_site, domain=self.domain)


# ---------- Depends ----------

def current_user(required: bool = True) -> Optional[TokenPayload]:
    u = _ctx_user.get()
    if required and u is None:
        raise UnauthorizedError()
    return u


def require_role(*roles: str):
    def dep(user: TokenPayload = Depends(current_user)):
        if not any(r in user.roles for r in roles):
            raise ForbiddenError()
        return user
    return dep

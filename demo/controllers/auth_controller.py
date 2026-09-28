# app/controllers/auth_controller.py
"""认证 Controller —— 登录 / 刷新 / 登出 / 当前用户的**正确示范**。

演示要点：
  * 凭证类接口配 LoginGuard（Redis 失败计数 + 锁定 + 递增延迟，防暴力破解）。
  * Cookie 与响应体双轨下发，浏览器和移动端共用一套接口。
  * 需要登录的接口直接 Depends(current_user)；令牌无效时中间件不写上下文，
    依赖抛 401。
"""
from uuid import UUID

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from sagitta.Core.errors import UnauthorizedError
from sagitta.Database.session import session_dep
from sagitta.Flow.controller import Controller
from sagitta.Security.auth_middleware import (
    REFRESH_COOKIE, clear_auth_cookies, current_user, set_auth_cookies,
)
from sagitta.Security.jwt import (
    JWT, TOKEN_TTL_ACCESS, TOKEN_TTL_REFRESH, TokenError, TokenPayload,
)
from sagitta.Security.login_guard import LoginGuard

from dto.auth_dto import LoginDTO
from services.auth_service import AuthService
from services.user_service import UserService
from vo.auth_vo import LoginResultVO

_db = Depends(session_dep())


class AuthController(Controller):
    prefix = "/auth"
    tags = ["认证"]

    svc: AuthService
    user_svc: UserService

    # ---------- 登录 ----------

    @Controller.post("/login", summary="登录")
    async def login(self, dto: LoginDTO, request: Request, response: Response,
                    db: AsyncSession = _db) -> LoginResultVO:
        ip = request.client.host if request.client else "-"
        await LoginGuard.check(dto.username, ip)
        try:
            access, refresh, vo = await self.svc.login(
                db, dto.username, dto.password)
        except UnauthorizedError:
            # 仅凭证失败计入爆破计数；DB 故障等 5xx 不计数（否则一次中间件
            # 抖动会把正常用户锁在门外）
            await LoginGuard.fail(dto.username, ip)
            raise
        await LoginGuard.success(dto.username, ip)
        set_auth_cookies(response, access=access, refresh=refresh)
        return LoginResultVO(access_token=access, refresh_token=refresh, user=vo)

    # ---------- 刷新 ----------

    @Controller.post("/refresh", summary="用 refresh token 换新 access token")
    async def refresh(self, request: Request, response: Response):
        rt = request.cookies.get(REFRESH_COOKIE)
        if not rt:
            raise UnauthorizedError("缺少刷新令牌")
        try:
            payload = self.svc.refresh_payload(rt)
        except TokenError as e:
            raise UnauthorizedError(e.message)
        if await JWT.is_revoked(payload.jti):
            raise UnauthorizedError("令牌已被吊销")
        access = self.svc.issue_access(payload)
        # 只滚动 access，refresh 不动（更严格的做法是两者一起轮换并把旧
        # refresh 拉黑，防止重放——按业务安全等级选择）
        set_auth_cookies(response, access=access)
        return {"refreshed": True}

    # ---------- 登出 ----------

    @Controller.post("/logout", summary="登出（吊销令牌并清 Cookie）")
    async def logout(self, request: Request, response: Response,
                     user: TokenPayload = Depends(current_user)):
        # 吊销 access
        await JWT.revoke(user.jti, TOKEN_TTL_ACCESS)
        # best-effort 吊销 refresh（jti 与 access 不同）
        rt = request.cookies.get(REFRESH_COOKIE)
        if rt:
            try:
                rp = self.svc.refresh_payload(rt)
                await JWT.revoke(rp.jti, TOKEN_TTL_REFRESH)
            except TokenError:
                pass
        clear_auth_cookies(response)
        return {"logged_out": True}

    # ---------- 当前用户 ----------

    @Controller.get("/me", summary="当前登录用户")
    async def me(self, user: TokenPayload = Depends(current_user),
                 db: AsyncSession = _db):
        # 以 DB 最新数据为准，令牌里的 roles 只做路由门禁
        return await self.user_svc.get(db, UUID(user.sub))


__all__ = ["AuthController"]

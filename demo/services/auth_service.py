# app/services/auth_service.py
"""认证 Service —— 登录凭证校验与令牌签发的**正确示范**。

要点：
  * DO（UserDO）只在本方法内出现，出方法时只剩 VO —— 分层不破。
  * 用户不存在 / 被禁用 / 密码错误使用**同一文案同一状态码**（防账号枚举）。
  * 哈希参数过时时在登录成功路径上**静默升级**（argon2 参数是可调的，
    升级不该要求用户改密码）。
"""
from typing import Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from sagittapi.Core.errors import UnauthorizedError
from sagittapi.Core.inject import ServiceBase
from sagittapi.Security.hash import PasswordHasher
from sagittapi.Security.jwt import (
    JWT, TOKEN_TTL_ACCESS, TOKEN_TTL_REFRESH, TokenPayload,
)

from demo.do.user_repo import UserRepository
from demo.vo.user_vo import UserVO


class AuthService(ServiceBase):

    async def login(
        self, db: AsyncSession, username: str, password: str
    ) -> Tuple[str, str, UserVO]:
        repo = UserRepository(db)
        row = await repo.get_by_username(username)
        if row is None or row.status != "active":
            # 刻意不在文案里区分「无此用户」与「密码错误」
            raise UnauthorizedError("用户名或密码错误")

        ok, new_hash = PasswordHasher.check_and_rehash(password, row.password_hash)
        if not ok:
            raise UnauthorizedError("用户名或密码错误")

        if new_hash:
            # 参数升级：本次请求内回写，事务随请求一起提交
            row.password_hash = new_hash
            await db.flush()

        vo = UserVO.model_validate(row)
        payload = TokenPayload(sub=str(row.id), roles=["user"], scopes=[])
        access = JWT.issue(payload, TOKEN_TTL_ACCESS, typ="access")
        refresh = JWT.issue(payload, TOKEN_TTL_REFRESH, typ="refresh")
        return access, refresh, vo

    @staticmethod
    def refresh_payload(refresh_token: str) -> TokenPayload:
        """校验 refresh token 并回传载荷（非法/过期 → 401）。"""
        return JWT.verify(refresh_token, require_type="refresh")

    @staticmethod
    def issue_access(payload: TokenPayload) -> str:
        return JWT.issue(payload, TOKEN_TTL_ACCESS, typ="access")


__all__ = ["AuthService"]

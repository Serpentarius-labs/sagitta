import os
import time
from dataclasses import dataclass, field
from typing import List, Optional

import jwt

from sagitta.Cache.redis import RedisRegistry

TOKEN_TTL_ACCESS = 15 * 60
TOKEN_TTL_REFRESH = 7 * 24 * 3600
ISSUER = "sagitta"


class TokenError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class TokenPayload:
    sub: str
    tenant_id: Optional[str] = None
    roles: List[str] = field(default_factory=list)
    scopes: List[str] = field(default_factory=list)
    jti: str = ""
    type: str = "access"


class JWT:
    _keys: dict[str, bytes] = {}

    @classmethod
    def register_key(cls, kid: str, key: bytes) -> None:
        cls._keys[kid] = key

    @classmethod
    def issue(
        cls, payload: TokenPayload, ttl: int = TOKEN_TTL_ACCESS, kid: str = "v1", typ: str = "access"
    ) -> str:
        now = int(time.time())
        claims = {
            "sub": payload.sub,
            "tenant_id": payload.tenant_id,
            "roles": payload.roles,
            "scopes": payload.scopes,
            "jti": payload.jti or os.urandom(16).hex(),
            "type": typ,
            "iss": ISSUER,
            "iat": now,
            "exp": now + ttl,
        }
        return jwt.encode(claims, cls._key(kid), algorithm="HS512", headers={"kid": kid, "typ": "JWT"})

    @classmethod
    def verify(cls, token: str, *, require_type: str = "access") -> TokenPayload:
        kid = jwt.get_unverified_header(token).get("kid", "v1")
        key = cls._keys.get(kid) or cls._key(kid)
        try:
            claims = jwt.decode(token, key, algorithms=["HS512"], issuer=ISSUER)
        except jwt.ExpiredSignatureError:
            raise TokenError("expired", "令牌已过期")
        except jwt.InvalidTokenError as e:
            raise TokenError("invalid", str(e))
        if claims.get("type") != require_type:
            raise TokenError("wrong_type", "令牌类型不符")
        return TokenPayload(
            sub=str(claims["sub"]),
            tenant_id=claims.get("tenant_id"),
            roles=claims.get("roles", []),
            scopes=claims.get("scopes", []),
            jti=claims["jti"],
            type=claims["type"],
        )

    @classmethod
    async def revoke(cls, jti: str, ttl: int = TOKEN_TTL_ACCESS) -> None:
        r = RedisRegistry.get_auth()
        await r.setex(f"jwt:black:{jti}", ttl, "1")

    @classmethod
    async def is_revoked(cls, jti: str) -> bool:
        r = RedisRegistry.get_auth()
        return await r.exists(f"jwt:black:{jti}") > 0

    @classmethod
    def _key(cls, kid: str) -> bytes:
        if kid not in cls._keys:
            from sagitta.Security.vault import Vault
            cls._keys[kid] = Vault.get("jwt_sign")
        return cls._keys[kid]

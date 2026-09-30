import asyncio

from fastapi import HTTPException

from ..Cache.redis import RedisRegistry

MAX_ATTEMPTS = 5
LOCK_SECONDS = 300
DELAY_BASE = 0.3
DELAY_MAX = 2.0


class LoginBlocked(HTTPException):
    def __init__(self, remaining: int):
        super().__init__(status_code=429, detail=f"尝试次数过多，请 {remaining} 秒后再试")


class LoginGuard:
    @classmethod
    async def check(cls, identity: str, ip: str) -> None:
        r = RedisRegistry.get_auth()
        for key in (f"lg:u:{identity}", f"lg:ip:{ip}"):
            cnt = await r.get(key)
            if cnt and int(cnt) >= MAX_ATTEMPTS:
                ttl = await r.ttl(key)
                raise LoginBlocked(max(1, ttl))

    @classmethod
    async def fail(cls, identity: str, ip: str) -> None:
        r = RedisRegistry.get_auth()
        for key in (f"lg:u:{identity}", f"lg:ip:{ip}"):
            n = await r.incr(key)
            if n == 1:
                await r.expire(key, LOCK_SECONDS)
        cnt = await r.get(f"lg:u:{identity}")
        delay = min(DELAY_BASE * (int(cnt) if cnt else 1), DELAY_MAX)
        await asyncio.sleep(delay)

    @classmethod
    async def success(cls, identity: str, ip: str) -> None:
        r = RedisRegistry.get_auth()
        await r.delete(f"lg:u:{identity}", f"lg:ip:{ip}")

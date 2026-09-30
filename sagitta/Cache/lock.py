# sagitta/Cache/lock.py
import asyncio
import time
import uuid
from .redis import RedisRegistry


class DistributedLock:
    def __init__(self, name: str, ttl: int = 30, client_name: str = "default"):
        self.key = f"lock:{name}"
        self.ttl = ttl
        self.client = RedisRegistry.get(client_name)
        self._token = str(uuid.uuid4())

    async def acquire(self, blocking: bool = True, timeout: float = 5) -> bool:
        end = time.time() + timeout
        while True:
            if await self.client.set(self.key, self._token, nx=True, ex=self.ttl):
                return True
            if not blocking or time.time() > end:
                return False
            await asyncio.sleep(0.05)

    async def release(self) -> None:
        script = 'if redis.call("get",KEYS[1])==ARGV[1] then return redis.call("del",KEYS[1]) else return 0 end'
        await self.client.eval(script, 1, self.key, self._token)

    async def __aenter__(self):
        if not await self.acquire():
            raise RuntimeError(f"获取分布式锁失败: {self.key}")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.release()
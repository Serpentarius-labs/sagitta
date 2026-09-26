# sagitta/Cache/redis.py
from typing import Dict
import redis.asyncio as aioredis
from redis import Redis


class RedisRegistry:
    _async: Dict[str, aioredis.Redis] = {}
    _sync: Dict[str, Redis] = {}

    @classmethod
    def init(cls, confs: Dict[str, dict]) -> None:
        for name, c in confs.items():
            if name in cls._async:
                continue
            url = c.get("url", str(c)) if isinstance(c, dict) else str(c)
            max_conn = int(c.get("max_connections", 10)) if isinstance(c, dict) else 10
            cls._async[name] = aioredis.from_url(url, max_connections=max_conn,
                                                  decode_responses=True, socket_keepalive=True)
            cls._sync[name] = Redis.from_url(url, max_connections=max_conn,
                                              decode_responses=True)

    @classmethod
    def init_from_config(cls, confs: Dict) -> None:
        """兼容入口：接受 SgSettings.redis dict 或原始 yaml dict。"""
        cls.init(confs)

    @classmethod
    async def close_all(cls) -> None:
        for r in cls._async.values():
            await r.aclose()
        cls._async.clear()
        cls._sync.clear()

    @classmethod
    def get(cls, name: str = "default") -> aioredis.Redis:
        return cls._async[name]

    @classmethod
    def get_sync(cls, name: str = "default") -> Redis:
        return cls._sync[name]


def redis_dep(name: str = "default"):
    async def _dep():
        yield RedisRegistry.get(name)
    return _dep

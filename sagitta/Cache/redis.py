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
        """取异步 Redis 实例。缺失时抛带修复指引的 KeyError，不返回 None。"""
        r = cls._async.get(name)
        if r is None:
            raise KeyError(
                f"Redis 实例 {name!r} 未初始化。已初始化: {list(cls._async) or '(无)'}。"
                f"请在 conf/config.yaml 的 redis.{name} 下配置，"
                f"或用环境变量 SG_REDIS_{name.upper()}_URL 注入。"
            )
        return r

    @classmethod
    def get_sync(cls, name: str = "default") -> Redis:
        """取同步 Redis 实例。缺失时抛带修复指引的 KeyError。"""
        r = cls._sync.get(name)
        if r is None:
            raise KeyError(
                f"Redis 实例 {name!r} 未初始化。已初始化: {list(cls._sync) or '(无)'}。"
            )
        return r

    @classmethod
    def get_auth(cls) -> aioredis.Redis:
        """获取认证用 Redis 实例，优先 auth，不存在则降级到 default。

        JWT 吊销检查与 LoginGuard 原本硬编码 'auth' 实例名，
        用户只配了 redis.default 时会 KeyError。此方法提供自动降级。
        """
        if "auth" in cls._async:
            return cls._async["auth"]
        return cls.get("default")


def redis_dep(name: str = "default"):
    async def _dep():
        yield RedisRegistry.get(name)
    return _dep

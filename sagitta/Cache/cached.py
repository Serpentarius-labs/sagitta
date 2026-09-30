# sagitta/Cache/cached.py
"""Redis 缓存装饰器，带防击穿保护。

防击穿策略：
  * 缓存未命中时，用 SET NX 获取互斥锁，仅一个请求回源。
  * 其余请求短暂等待后 double-check 缓存。
  * 等待后仍无缓存则直接回源（降级，不阻塞业务）。
  * 锁有 10s TTL 兜底，防止回源崩溃导致死锁。
"""
import asyncio
import functools
import json
from typing import Callable

from .redis import RedisRegistry


def cached(key_tpl: str, ttl: int = 300, client_name: str = "default", prefix: str = "cache"):
    def deco(fn: Callable):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            k = f"{prefix}:{key_tpl}".format(*_ak(args), **_kk(kwargs))
            client = RedisRegistry.get(client_name)
            raw = await client.get(k)
            if raw is not None:
                return json.loads(raw)

            # 防击穿：SET NX 获取锁，仅一个请求回源
            lock_key = f"{k}:lock"
            got_lock = await client.set(lock_key, "1", nx=True, ex=10)
            if got_lock:
                try:
                    # double-check：等锁期间可能已被其他请求填充
                    raw = await client.get(k)
                    if raw is not None:
                        return json.loads(raw)
                    rv = await fn(*args, **kwargs)
                    await client.set(k, json.dumps(rv, ensure_ascii=False), ex=ttl)
                    return rv
                finally:
                    await client.delete(lock_key)

            # 未获锁：等待后 double-check，仍无缓存则直接回源
            await asyncio.sleep(0.1)
            raw = await client.get(k)
            if raw is not None:
                return json.loads(raw)
            return await fn(*args, **kwargs)
        return wrapper
    return deco


def _ak(args): return [str(a) for a in args]
def _kk(kwargs): return {k: str(v) for k, v in kwargs.items()}

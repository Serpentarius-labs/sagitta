# sagitta/Cache/cached.py
import functools
import json
from typing import Callable
from .redis import RedisRegistry


def cached(key_tpl: str, ttl: int = 300, client_name: str = "default", prefix: str = "cache"):
    def deco(fn: Callable):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            k = f"{prefix}:{key_tpl}".format(*_ak(args), **_kk(kwargs))
            raw = await RedisRegistry.get(client_name).get(k)
            if raw is not None:
                return json.loads(raw)
            rv = await fn(*args, **kwargs)
            await RedisRegistry.get(client_name).set(k, json.dumps(rv, ensure_ascii=False), ex=ttl)
            return rv
        return wrapper
    return deco

def _ak(args): return [str(a) for a in args]
def _kk(kwargs): return {k: str(v) for k, v in kwargs.items()}
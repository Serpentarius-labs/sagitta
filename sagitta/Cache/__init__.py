# sagitta/Cache/__init__.py
"""Cache 子包：Redis 连接池、分布式锁、装饰器缓存、Qdrant 向量库。

导入示例::

    from sagittapi.Cache import RedisRegistry, redis_dep, cached, DistributedLock

Qdrant 是可选依赖（pip install sagitta[qdrant]），未安装时
QdrantRegistry/qdrant_dep 不可用但不影响 Redis 等核心功能。
"""
from .redis import RedisRegistry, redis_dep
from .lock import DistributedLock
from .cached import cached

__all__ = [
    # redis
    "RedisRegistry", "redis_dep",
    # lock
    "DistributedLock",
    # cached
    "cached",
]

# Qdrant 是可选依赖，未安装时跳过 re-export
try:
    from .qdrant import QdrantRegistry, qdrant_dep
    __all__ += ["QdrantRegistry", "qdrant_dep"]
except ImportError:
    pass

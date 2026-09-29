# sagitta/Cache/__init__.py
"""Cache 子包：Redis 连接池、分布式锁、装饰器缓存、Qdrant 向量库。

导入示例::

    from sagitta.Cache import RedisRegistry, redis_dep, cached, DistributedLock
"""
from .redis import RedisRegistry, redis_dep
from .lock import DistributedLock
from .cached import cached
from .qdrant import QdrantRegistry, qdrant_dep

__all__ = [
    # redis
    "RedisRegistry", "redis_dep",
    # lock
    "DistributedLock",
    # cached
    "cached",
    # qdrant
    "QdrantRegistry", "qdrant_dep",
]

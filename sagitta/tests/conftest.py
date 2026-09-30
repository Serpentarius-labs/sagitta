# tests/conftest.py
"""pytest 全局 fixtures。

提供：
  * test_settings：最小可用配置对象（不读 yaml，纯内存）
  * mock_redis：FakeRedis 异步客户端
  * assert_resp：断言响应壳 {code, message, data} 的 helper
  * reset_registries：每个测试自动重置框架全局状态
"""
import asyncio
import os
import sys
from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

# 确保 sagitta 包可导入
# conftest 在 sagitta/sagitta/tests/ 下，需要加 sagitta/（grandparent）到 path
_pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)


@pytest.fixture
def test_settings():
    """构造最小可用的 SgSettings（不读 yaml 文件）。"""
    from sagittapi.Config.setting import SgSettings

    raw = {
        "app": {"title": "test", "version": "0.0.0", "env": "test", "debug": True},
        "server": {"host": "127.0.0.1", "port": 8000, "workers": 1,
                   "cors_origins": [], "max_body_bytes": 0},
        "db": {},
        "redis": {},
        "rate_limit": {"enabled": False},
    }
    return SgSettings(raw)


@pytest.fixture
def mock_redis():
    """Fake Redis 异步客户端，模拟 aioredis 行为。

    支持：get/set/setex/set(nx)/delete/exists/incr/expire/pipeline/ttl
    用于 cached 装饰器、限流、JWT 吊销等测试。
    """
    store: Dict[str, Any] = {}
    expires: Dict[str, float] = {}

    class _Pipe:
        def __init__(self):
            self._cmds = []
        def zremrangebyscore(self, *a, **kw): self._cmds.append(("zremrangebyscore", a, kw)); return self
        def zadd(self, *a, **kw): self._cmds.append(("zadd", a, kw)); return self
        def zcard(self, *a, **kw): self._cmds.append(("zcard", a, kw)); return self
        def expire(self, *a, **kw): self._cmds.append(("expire", a, kw)); return self
        async def execute(self):
            results = []
            for cmd, args, kw in self._cmds:
                if cmd == "zremrangebyscore":
                    key = args[0]
                    if key in store:
                        store[key] = {k: v for k, v in store[key].items()
                                      if v >= args[1]}
                    results.append(0)
                elif cmd == "zadd":
                    key = args[0]
                    if key not in store: store[key] = {}
                    mapping = args[1] if len(args) > 1 else kw.get("mapping", {})
                    for k, v in mapping.items():
                        store[key][k] = v
                    results.append(1)
                elif cmd == "zcard":
                    key = args[0]
                    results.append(len(store.get(key, {})))
                elif cmd == "expire":
                    results.append(1)
            self._cmds.clear()
            return results

    client = MagicMock()
    client._store = store
    client._expires = expires

    async def _get(k): return store.get(k)
    async def _set(k, v, ex=None, nx=False, **kw):
        if nx and k in store: return False
        store[k] = v
        return True
    async def _setex(k, ttl, v): store[k] = v; return True
    async def _delete(*keys):
        n = 0
        for k in keys:
            if k in store: del store[k]; n += 1
        return n
    async def _exists(k): return 1 if k in store else 0
    async def _incr(k):
        store[k] = int(store.get(k, 0)) + 1
        return store[k]
    async def _expire(k, ttl): return True
    async def _ttl(k): return 300
    async def _ping(): return True
    def _pipeline(): return _Pipe()

    client.get = _get
    client.set = _set
    client.setex = _setex
    client.delete = _delete
    client.exists = _exists
    client.incr = _incr
    client.expire = _expire
    client.ttl = _ttl
    client.ping = AsyncMock(return_value=True)
    client.pipeline = _pipeline
    client.eval = AsyncMock(return_value=0)

    return client


@pytest.fixture
def assert_resp():
    """断言响应壳 helper。"""
    def _check(resp_body: dict, *, code: Any = 0, message: Optional[str] = None,
               data: Any = None) -> bool:
        assert resp_body is not None, "响应体为 None"
        assert resp_body.get("code") == code, (
            f"code 不匹配: expected={code!r}, actual={resp_body.get('code')!r}, "
            f"message={resp_body.get('message')!r}"
        )
        if message is not None:
            assert message in resp_body.get("message", ""), (
                f"message 不含 {message!r}: {resp_body.get('message')!r}"
            )
        if data is not None:
            assert resp_body.get("data") == data, (
                f"data 不匹配: expected={data!r}, actual={resp_body.get('data')!r}"
            )
        return True
    return _check


@pytest.fixture(autouse=True)
def reset_registries():
    """每个测试自动重置框架全局状态。"""
    # 重置控制器登记表
    try:
        from sagittapi.Flow.registry import reset_for_test
        reset_for_test()
    except Exception:
        pass
    # 重置配置单例
    try:
        from sagittapi.Config.setting import _SETTINGS
        import sagittapi.Config.setting as mod
        mod._SETTINGS = None
    except Exception:
        pass
    # 重置 ConfigLoader 单例
    try:
        from sagittapi.Config.loader import ConfigLoader
        ConfigLoader.reset()
    except Exception:
        pass
    # 重置权限解析器为默认 RolePermissionResolver
    try:
        from sagittapi.Security.permissions import set_permission_resolver, RolePermissionResolver
        set_permission_resolver(RolePermissionResolver())
    except Exception:
        pass
    # 重置 MetricsCollector 单例
    try:
        from sagittapi.Core.metrics import MetricsCollector
        MetricsCollector._instance = None
    except Exception:
        pass
    yield

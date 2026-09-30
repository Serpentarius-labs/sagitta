# sagitta/Security/rate_limit.py
"""全局限流中间件：per-IP / per-user / per-route 滑动窗口限流。

策略：
  * 基于 Redis ZSET 的滑动窗口算法，精确控制时间窗内的请求数。
  * 支持多维度限流：IP 维度（防爬虫）、用户维度（防滥用）、路由维度（热点保护）。
  * 限流触发时抛 RateLimitError（429），走框架统一异常处理。
  * Redis 不可用时降级为不限流（不阻塞业务），但记录告警。

配置（conf/config.yaml）::

    rate_limit:
      enabled: true              # 总开关
      redis: "default"           # 使用的 Redis 实例
      default:
        requests: 100            # 窗口内最大请求数
        window: 60               # 窗口大小（秒）
      per_ip:
        requests: 60
        window: 60
      per_user:
        requests: 200
        window: 60
      skip_paths:                # 不限流的路径
        - /health
        - /ready
        - /metrics
        - /docs
        - /openapi.json
"""
import time
from typing import Awaitable, Callable, List, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..Core.errors import RateLimitError
from ..Log.logger import get_trace


def _client_ip(request: Request) -> str:
    """提取客户端 IP，优先取 X-Forwarded-For（经反代/网关时）。"""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """滑动窗口限流中间件。

    三层维度同时检查，任一超限即拒绝：
      1. per-IP：防止单 IP 滥用
      2. per-user：防止已登录用户滥用（需 JwtAuthMiddleware 先执行）
      3. per-route-default：全局默认配额
    """

    def __init__(
        self,
        app,
        *,
        enabled: bool = True,
        redis_client_name: str = "default",
        default_requests: int = 100,
        default_window: int = 60,
        ip_requests: int = 60,
        ip_window: int = 60,
        user_requests: int = 200,
        user_window: int = 60,
        skip_paths: Optional[List[str]] = None,
    ):
        super().__init__(app)
        self.enabled = enabled
        self.redis_name = redis_client_name
        self.default_limit = (default_requests, default_window)
        self.ip_limit = (ip_requests, ip_window)
        self.user_limit = (user_requests, user_window)
        self.skip = set(skip_paths or ["/health", "/ready", "/metrics",
                                       "/docs", "/openapi.json"])

    async def dispatch(self, request: Request,
                       call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if not self.enabled:
            return await call_next(request)

        path = request.url.path
        if path in self.skip:
            return await call_next(request)

        # 尝试获取 Redis 客户端，不可用则降级
        try:
            from ..Cache.redis import RedisRegistry
            redis = RedisRegistry.get(self.redis_name)
        except Exception:
            # Redis 不可用时不阻塞业务，但记录告警
            import logging
            logging.getLogger("sagittapi").warning(
                "[rate_limit] Redis 不可用，限流降级为不限流"
            )
            return await call_next(request)

        ip = _client_ip(request)
        user = getattr(request.state, "user", None)
        user_id = str(getattr(user, "sub", "")) if user else ""

        # 三层检查，任一超限即拒绝
        if not await self._check(redis, "ip", ip, *self.ip_limit):
            await self._log_hit(request, "ip", ip)
            return RateLimitError("IP 请求过于频繁，请稍后再试").to_json_response()

        if user_id and not await self._check(redis, "user", user_id, *self.user_limit):
            await self._log_hit(request, "user", user_id)
            return RateLimitError("账户请求过于频繁，请稍后再试").to_json_response()

        if not await self._check(redis, "global", "all", *self.default_limit):
            await self._log_hit(request, "global", "all")
            return RateLimitError("系统繁忙，请稍后再试").to_json_response()

        return await call_next(request)

    async def _check(self, redis, dimension: str, key_id: str,
                     max_requests: int, window: int) -> bool:
        """滑动窗口检查：用 ZSET 记录窗口内请求时间戳。

        通过 pipeline 批量发送 4 条命令（zremrangebyscore + zadd + zcard + expire），
        减少 RTT。pipeline 不等于事务（MULTI/EXEC），Redis 单线程模型下
        各命令间可能插入其他客户端命令，但限流场景下少量误差可接受。
        如需严格原子性，应改用 Lua 脚本。
        """
        now = time.time()
        key = f"rate:{dimension}:{key_id}"
        window_start = now - window

        pipe = redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)   # 移除窗口外的记录
        pipe.zadd(key, {str(now): now})                # 添加当前请求
        pipe.zcard(key)                                 # 统计窗口内请求数
        pipe.expire(key, window + 1)                    # 设置 key 过期
        results = await pipe.execute()
        count = results[2]
        return count <= max_requests

    async def _log_hit(self, request: Request, dimension: str, key_id: str) -> None:
        import logging
        tid, _ = get_trace()
        logging.getLogger("sagittapi").info(
            "[%s] rate_limit HIT dimension=%s key=%s path=%s",
            tid, dimension, key_id, request.url.path
        )


__all__ = ["RateLimitMiddleware"]

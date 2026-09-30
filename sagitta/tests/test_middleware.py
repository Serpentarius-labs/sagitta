# tests/test_middleware.py
"""中间件单元测试：限流 + 请求体大小限制。

覆盖：
  * RateLimitMiddleware：超限返回 429 JSONResponse、未超限放行、Redis 不可用时降级
  * BodySizeLimitMiddleware：超限返回 413、Content-Length 合法时放行、GET 请求跳过
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request


class TestRateLimitMiddleware:
    """限流中间件测试。"""

    def _make_request(self, method="POST", path="/api/data",
                      client_ip="127.0.0.1", headers=None):
        """构造 mock Request。"""
        scope = {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
            "client": ["127.0.0.1", 12345],
            "query_string": b"",
            "app": SimpleNamespace(),
        }
        request = MagicMock(spec=Request)
        request.method = method
        request.url = SimpleNamespace(path=path)
        request.headers = {k: v for k, v in (headers or {}).items()}
        request.client = SimpleNamespace(host=client_ip)
        request.state = SimpleNamespace()
        return request

    def test_disabled_passthrough(self):
        """enabled=False 时直接放行。"""
        from sagittapi.Security.rate_limit import RateLimitMiddleware

        call_count = 0

        async def call_next(request):
            nonlocal call_count
            call_count += 1
            return MagicMock(status_code=200)

        mw = RateLimitMiddleware(MagicMock(), enabled=False)
        request = self._make_request()
        result = asyncio.run(mw.dispatch(request, call_next))
        assert call_count == 1

    def test_skip_paths_passthrough(self):
        """skip_paths 中的路径直接放行。"""
        from sagittapi.Security.rate_limit import RateLimitMiddleware

        call_count = 0

        async def call_next(request):
            nonlocal call_count
            call_count += 1
            return MagicMock(status_code=200)

        mw = RateLimitMiddleware(MagicMock(), enabled=True,
                                 skip_paths=["/health", "/ready"])
        request = self._make_request(path="/health")
        result = asyncio.run(mw.dispatch(request, call_next))
        assert call_count == 1

    def test_redis_unavailable_degrades_gracefully(self):
        """Redis 不可用时不阻塞业务。"""
        from sagittapi.Security.rate_limit import RateLimitMiddleware

        call_count = 0

        async def call_next(request):
            nonlocal call_count
            call_count += 1
            return MagicMock(status_code=200)

        mw = RateLimitMiddleware(MagicMock(), enabled=True)

        # 模拟 RedisRegistry.get 抛异常
        with patch("sagitta.Cache.redis.RedisRegistry.get",
                   side_effect=KeyError("default")):
            request = self._make_request()
            result = asyncio.run(mw.dispatch(request, call_next))

        assert call_count == 1  # 降级为放行

    def test_rate_limited_returns_429(self):
        """超限时返回 429 JSONResponse，不走 call_next。"""
        from sagittapi.Security.rate_limit import RateLimitMiddleware

        call_count = 0

        async def call_next(request):
            nonlocal call_count
            call_count += 1
            return MagicMock(status_code=200)

        # 构造 mock Redis：_check 返回 False（超限）
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.zremrangebyscore.return_value = mock_pipe
        mock_pipe.zadd.return_value = mock_pipe
        mock_pipe.zcard.return_value = mock_pipe
        mock_pipe.expire.return_value = mock_pipe
        mock_pipe.execute = AsyncMock(return_value=[0, 1, 999, 1])  # count=999 > limit
        mock_redis.pipeline.return_value = mock_pipe

        mw = RateLimitMiddleware(
            MagicMock(), enabled=True,
            ip_requests=60, ip_window=60,
            default_requests=100, default_window=60,
        )

        with patch("sagitta.Cache.redis.RedisRegistry.get", return_value=mock_redis):
            request = self._make_request()
            result = asyncio.run(mw.dispatch(request, call_next))

        assert call_count == 0  # 没走 call_next
        assert result.status_code == 429

        # 验证响应体符合统一壳结构 {code, message, data}
        import json
        body = json.loads(result.body)
        assert body["code"] == "TOO_MANY_REQUESTS"
        assert "message" in body
        assert "data" in body

    def test_under_limit_passthrough(self):
        """未超限时正常放行。"""
        from sagittapi.Security.rate_limit import RateLimitMiddleware

        call_count = 0

        async def call_next(request):
            nonlocal call_count
            call_count += 1
            return MagicMock(status_code=200)

        # count=5 < limit=60 → 放行
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.zremrangebyscore.return_value = mock_pipe
        mock_pipe.zadd.return_value = mock_pipe
        mock_pipe.zcard.return_value = mock_pipe
        mock_pipe.expire.return_value = mock_pipe
        mock_pipe.execute = AsyncMock(return_value=[0, 1, 5, 1])
        mock_redis.pipeline.return_value = mock_pipe

        mw = RateLimitMiddleware(
            MagicMock(), enabled=True,
            ip_requests=60, ip_window=60,
            default_requests=100, default_window=60,
        )

        with patch("sagitta.Cache.redis.RedisRegistry.get", return_value=mock_redis):
            request = self._make_request()
            result = asyncio.run(mw.dispatch(request, call_next))

        assert call_count == 1  # 正常放行


class TestBodySizeLimitMiddleware:
    """请求体大小限制中间件测试。"""

    def _make_request(self, method="POST", path="/api/upload",
                      content_length=None):
        """构造 mock Request。"""
        headers = {}
        if content_length is not None:
            headers["Content-Length"] = str(content_length)
        request = MagicMock(spec=Request)
        request.method = method
        request.url = SimpleNamespace(path=path)
        request.headers = headers
        request.client = SimpleNamespace(host="127.0.0.1")
        return request

    def test_disabled_passthrough(self):
        """max_body_bytes=0 时不限制。"""
        from sagittapi.Core.body_limit import BodySizeLimitMiddleware

        call_count = 0

        async def call_next(request):
            nonlocal call_count
            call_count += 1
            return MagicMock(status_code=200)

        mw = BodySizeLimitMiddleware(MagicMock(), max_body_bytes=0)
        request = self._make_request(content_length=999999999)
        result = asyncio.run(mw.dispatch(request, call_next))
        assert call_count == 1

    def test_get_request_skipped(self):
        """GET 请求不检查 body 大小。"""
        from sagittapi.Core.body_limit import BodySizeLimitMiddleware

        call_count = 0

        async def call_next(request):
            nonlocal call_count
            call_count += 1
            return MagicMock(status_code=200)

        mw = BodySizeLimitMiddleware(MagicMock(), max_body_bytes=1024)
        request = self._make_request(method="GET", content_length=999999)
        result = asyncio.run(mw.dispatch(request, call_next))
        assert call_count == 1

    def test_oversized_returns_413(self):
        """Content-Length 超过限制时返回 413。"""
        from sagittapi.Core.body_limit import BodySizeLimitMiddleware

        call_count = 0

        async def call_next(request):
            nonlocal call_count
            call_count += 1
            return MagicMock(status_code=200)

        mw = BodySizeLimitMiddleware(MagicMock(), max_body_bytes=1024)
        request = self._make_request(content_length=2048)
        result = asyncio.run(mw.dispatch(request, call_next))

        assert call_count == 0  # 没走 call_next
        assert result.status_code == 413

    def test_valid_size_passthrough(self):
        """Content-Length 在限制内时正常放行。"""
        from sagittapi.Core.body_limit import BodySizeLimitMiddleware

        call_count = 0

        async def call_next(request):
            nonlocal call_count
            call_count += 1
            return MagicMock(status_code=200)

        mw = BodySizeLimitMiddleware(MagicMock(), max_body_bytes=4096)
        request = self._make_request(content_length=1024)
        result = asyncio.run(mw.dispatch(request, call_next))

        assert call_count == 1  # 正常放行

    def test_invalid_content_length_passthrough(self):
        """Content-Length 非法数字时放行（交给下游 400）。"""
        from sagittapi.Core.body_limit import BodySizeLimitMiddleware

        call_count = 0

        async def call_next(request):
            nonlocal call_count
            call_count += 1
            return MagicMock(status_code=200)

        mw = BodySizeLimitMiddleware(MagicMock(), max_body_bytes=1024)
        request = self._make_request()
        request.headers = {"Content-Length": "not-a-number"}
        result = asyncio.run(mw.dispatch(request, call_next))

        assert call_count == 1  # 交给下游处理

    def test_no_content_length_passthrough(self):
        """无 Content-Length 时放行（流式上传由路由层控制）。"""
        from sagittapi.Core.body_limit import BodySizeLimitMiddleware

        call_count = 0

        async def call_next(request):
            nonlocal call_count
            call_count += 1
            return MagicMock(status_code=200)

        mw = BodySizeLimitMiddleware(MagicMock(), max_body_bytes=1024)
        request = self._make_request(content_length=None)
        result = asyncio.run(mw.dispatch(request, call_next))

        assert call_count == 1  # 放行

    def test_413_response_body_has_unified_shell(self):
        """413 响应体符合统一壳结构 {code, message, data}。"""
        import json

        from sagittapi.Core.body_limit import BodySizeLimitMiddleware

        async def call_next(request):
            return MagicMock(status_code=200)

        mw = BodySizeLimitMiddleware(MagicMock(), max_body_bytes=100)
        request = self._make_request(content_length=200)
        result = asyncio.run(mw.dispatch(request, call_next))

        body = json.loads(result.body)
        assert body["code"] == "PAYLOAD_TOO_LARGE"
        assert "message" in body
        assert "data" in body


class TestMetricsCollector:
    """Prometheus 指标收集器测试。"""

    def test_counter_increment(self):
        from sagittapi.Core.metrics import MetricsCollector

        collector = MetricsCollector()
        collector.http_requests_total.inc(method="GET", route="/test", status="200")
        output = collector.to_prometheus()
        assert "http_requests_total" in output
        assert "GET" in output
        assert "200" in output

    def test_histogram_observe(self):
        from sagittapi.Core.metrics import MetricsCollector

        collector = MetricsCollector()
        collector.http_request_duration_ms.observe(15.0, method="POST", route="/api")
        output = collector.to_prometheus()
        assert "http_request_duration_ms" in output
        assert "_bucket" in output
        assert "_sum" in output
        assert "_count" in output

    def test_gauge_set_and_dec(self):
        from sagittapi.Core.metrics import MetricsCollector

        collector = MetricsCollector()
        collector.http_requests_in_progress.inc(method="GET")
        collector.http_requests_in_progress.inc(method="GET")
        collector.http_requests_in_progress.dec(method="GET")
        output = collector.to_prometheus()
        # 应该有 1 个 in-progress 请求
        lines = [l for l in output.split("\n") if "http_requests_in_progress" in l and "GET" in l]
        assert len(lines) > 0

    def test_prometheus_format_valid(self):
        """输出符合 Prometheus 文本格式：有 # HELP 和 # TYPE 行。"""
        from sagittapi.Core.metrics import MetricsCollector

        collector = MetricsCollector()
        collector.http_requests_total.inc(method="GET", route="/", status="200")
        output = collector.to_prometheus()
        assert "# HELP" in output
        assert "# TYPE" in output
        assert "counter" in output
        assert "histogram" in output
        assert "gauge" in output

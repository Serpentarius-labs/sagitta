# sagitta/Core/metrics.py
"""Prometheus 指标暴露。

指标设计：
  * http_requests_total         — Counter，按 method/route/status 维度
  * http_request_duration_ms    — Histogram，按 method/route 维度
  * http_requests_in_progress   — Gauge，当前处理中的请求数
  * db_pool_checkedout          — Gauge，连接池使用量
  * db_pool_size                — Gauge，连接池总大小
  * redis_commands_total        — Counter，Redis 命令计数
  * redis_command_duration_ms   — Histogram，Redis 命令延迟

端点：GET /metrics（默认不包含在 OpenAPI 中）

不依赖 prometheus_client 库：手写文本格式输出，减少一个依赖。
"""
import time
from collections import defaultdict
from threading import Lock
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class _Counter:
    """简易 Counter。"""
    __slots__ = ("name", "help", "labels", "values", "_lock")

    def __init__(self, name: str, help: str):
        self.name = name
        self.help = help
        self.labels: List[str] = []
        self.values: Dict[Tuple[str, ...], float] = defaultdict(float)
        self._lock = Lock()

    def inc(self, value: float = 1, **labels) -> None:
        key = tuple(labels.get(l, "") for l in self.labels)
        with self._lock:
            self.values[key] += value


class _Histogram:
    """简易 Histogram。bucket 边界固定。"""
    __slots__ = ("name", "help", "labels", "buckets", "_counts", "_sum", "_lock")

    def __init__(self, name: str, help: str, buckets=None):
        self.name = name
        self.help = help
        self.labels: List[str] = []
        self.buckets = buckets or [1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
        self._counts: Dict[Tuple[str, ...], List[int]] = defaultdict(lambda: [0] * (len(self.buckets) + 1))
        self._sum: Dict[Tuple[str, ...], float] = defaultdict(float)
        self._lock = Lock()

    def observe(self, value: float, **labels) -> None:
        key = tuple(labels.get(l, "") for l in self.labels)
        with self._lock:
            for i, b in enumerate(self.buckets):
                if value <= b:
                    self._counts[key][i] += 1
                    break
            else:
                self._counts[key][-1] += 1
            self._sum[key] += value


class _Gauge:
    """简易 Gauge。"""
    __slots__ = ("name", "help", "labels", "values", "_lock")

    def __init__(self, name: str, help: str):
        self.name = name
        self.help = help
        self.labels: List[str] = []
        self.values: Dict[Tuple[str, ...], float] = defaultdict(float)
        self._lock = Lock()

    def set(self, value: float, **labels) -> None:
        key = tuple(labels.get(l, "") for l in self.labels)
        with self._lock:
            self.values[key] = value

    def inc(self, value: float = 1, **labels) -> None:
        key = tuple(labels.get(l, "") for l in self.labels)
        with self._lock:
            self.values[key] += value

    def dec(self, value: float = 1, **labels) -> None:
        key = tuple(labels.get(l, "") for l in self.labels)
        with self._lock:
            self.values[key] -= value


class MetricsCollector:
    """全局指标收集器单例。"""

    _instance: Optional["MetricsCollector"] = None

    def __init__(self):
        self.http_requests_total = _Counter(
            "http_requests_total", "Total HTTP requests"
        )
        self.http_requests_total.labels = ["method", "route", "status"]

        self.http_request_duration_ms = _Histogram(
            "http_request_duration_ms", "HTTP request duration in ms"
        )
        self.http_request_duration_ms.labels = ["method", "route"]

        self.http_requests_in_progress = _Gauge(
            "http_requests_in_progress", "Current in-progress HTTP requests"
        )
        self.http_requests_in_progress.labels = ["method"]

        self.db_pool_checkedout = _Gauge(
            "db_pool_checkedout", "DB connections checked out"
        )
        self.db_pool_checkedout.labels = ["engine"]

        self.db_pool_size = _Gauge(
            "db_pool_size", "DB connection pool size"
        )
        self.db_pool_size.labels = ["engine"]

        self.redis_commands_total = _Counter(
            "redis_commands_total", "Total Redis commands"
        )
        self.redis_commands_total.labels = ["command"]

        self.redis_command_duration_ms = _Histogram(
            "redis_command_duration_ms", "Redis command duration in ms"
        )
        self.redis_command_duration_ms.labels = ["command"]

    @classmethod
    def instance(cls) -> "MetricsCollector":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def to_prometheus(self) -> str:
        """渲染为 Prometheus 文本格式。"""
        lines: List[str] = []

        for counter in [self.http_requests_total, self.redis_commands_total]:
            lines.append(f"# HELP {counter.name} {counter.help}")
            lines.append(f"# TYPE {counter.name} counter")
            with counter._lock:
                for key, val in counter.values.items():
                    label_str = ",".join(
                        f'{l}="{k}"' for l, k in zip(counter.labels, key)
                    )
                    lines.append(f'{counter.name}{{{label_str}}} {val}')

        for hist in [self.http_request_duration_ms, self.redis_command_duration_ms]:
            lines.append(f"# HELP {hist.name} {hist.help}")
            lines.append(f"# TYPE {hist.name} histogram")
            with hist._lock:
                for key in list(hist._counts.keys()):
                    cumulative = 0
                    for i, b in enumerate(hist.buckets):
                        cumulative += hist._counts[key][i]
                        label_str = ",".join(
                            f'{l}="{k}"' for l, k in zip(hist.labels, key)
                        )
                        lines.append(f'{hist.name}_bucket{{{label_str},le="{b}"}} {cumulative}')
                    cumulative += hist._counts[key][-1]
                    label_str = ",".join(
                        f'{l}="{k}"' for l, k in zip(hist.labels, key)
                    )
                    lines.append(f'{hist.name}_bucket{{{label_str},le="+Inf"}} {cumulative}')
                    lines.append(f'{hist.name}_sum{{{label_str}}} {hist._sum[key]}')
                    lines.append(f'{hist.name}_count{{{label_str}}} {cumulative}')

        for gauge in [self.http_requests_in_progress, self.db_pool_checkedout, self.db_pool_size]:
            lines.append(f"# HELP {gauge.name} {gauge.help}")
            lines.append(f"# TYPE {gauge.name} gauge")
            with gauge._lock:
                for key, val in gauge.values.items():
                    label_str = ",".join(
                        f'{l}="{k}"' for l, k in zip(gauge.labels, key)
                    )
                    lines.append(f'{gauge.name}{{{label_str}}} {val}')

        return "\n".join(lines) + "\n"


class MetricsMiddleware(BaseHTTPMiddleware):
    """HTTP 指标采集中间件。"""

    async def dispatch(self, request: Request,
                       call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        collector = MetricsCollector.instance()
        method = request.method
        collector.http_requests_in_progress.inc(method=method)

        t0 = time.perf_counter()
        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - t0) * 1000
            route = request.url.path
            status = response.status_code
            collector.http_requests_total.inc(
                method=method, route=route, status=str(status)
            )
            collector.http_request_duration_ms.observe(
                duration_ms, method=method, route=route
            )
            return response
        except Exception:
            duration_ms = (time.perf_counter() - t0) * 1000
            route = request.url.path
            collector.http_requests_total.inc(
                method=method, route=route, status="500"
            )
            collector.http_request_duration_ms.observe(
                duration_ms, method=method, route=route
            )
            raise
        finally:
            collector.http_requests_in_progress.dec(method=method)


def register_metrics(app: FastAPI, *, prefix: str = "") -> None:
    """注册 /metrics 端点与采集中间件。"""
    collector = MetricsCollector.instance()

    @app.get(f"{prefix}/metrics", include_in_schema=False, tags=["运维"])
    async def metrics() -> Response:
        collect_db_pool_metrics()
        return Response(
            content=collector.to_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )


def collect_db_pool_metrics() -> None:
    """采集 DB 连接池指标。在 /metrics 端点调用时触发。"""
    collector = MetricsCollector.instance()
    try:
        from ..Database.engine import EngineRegistry
        for name, engine in EngineRegistry._engines.items():
            pool = engine.pool
            collector.db_pool_checkedout.set(
                pool.checkedout(), engine=name
            )
            collector.db_pool_size.set(
                pool.size(), engine=name
            )
    except Exception:
        pass


__all__ = ["MetricsCollector", "MetricsMiddleware", "register_metrics",
           "collect_db_pool_metrics"]

# sagitta/Log/__init__.py
"""Log 子包：trace 上下文、TraceMiddleware、日志初始化。

导入示例::

    from sagittapi.Log import get_trace, set_trace, TraceMiddleware, init_root_logger
"""
from .logger import (
    get_trace, set_trace, reset_trace,
    TraceFilter, JsonFormatter, init_root_logger,
)
from .middleware import TraceMiddleware, TRACE_HEADER, REQUEST_TIME_HEADER
from .backend import LogBackend, LogBackendType

__all__ = [
    # logger
    "get_trace", "set_trace", "reset_trace",
    "TraceFilter", "JsonFormatter", "init_root_logger",
    # middleware
    "TraceMiddleware", "TRACE_HEADER", "REQUEST_TIME_HEADER",
    # backend
    "LogBackend", "LogBackendType",
]

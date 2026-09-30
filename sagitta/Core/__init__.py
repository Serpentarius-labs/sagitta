# sagitta/Core/__init__.py
"""Core 子包：依赖注入、错误体系、指标、请求体限制、业务码。

导入示例::

    from sagittapi.Core import SgError, ServiceBase, Inject
"""
from .errors import (
    SUCCESS_CODE, SgError, BizError, NotFoundError, UnauthorizedError,
    ForbiddenError, RateLimitError, ArchViolationError, DOLeakError,
    WrappedResponseError, StorageNotConfiguredError, FrameworkCodes,
)
from .inject import Container, ServiceBase, Inject, InjectionError, autowire
from .metrics import MetricsCollector, MetricsMiddleware, register_metrics, collect_db_pool_metrics
from .body_limit import BodySizeLimitMiddleware, PayloadTooLargeError
from .biz_code import BizCode, BizCodes, BizCodeRegistry, BizCodeConflictError, raises, collect_raises, group_by_status

__all__ = [
    # errors
    "SUCCESS_CODE", "SgError", "BizError", "NotFoundError", "UnauthorizedError",
    "ForbiddenError", "RateLimitError", "ArchViolationError", "DOLeakError",
    "WrappedResponseError", "StorageNotConfiguredError", "FrameworkCodes",
    # inject
    "Container", "ServiceBase", "Inject", "InjectionError", "autowire",
    # metrics
    "MetricsCollector", "MetricsMiddleware", "register_metrics", "collect_db_pool_metrics",
    # body_limit
    "BodySizeLimitMiddleware", "PayloadTooLargeError",
    # biz_code
    "BizCode", "BizCodes", "BizCodeRegistry", "BizCodeConflictError",
    "raises", "collect_raises", "group_by_status",
]

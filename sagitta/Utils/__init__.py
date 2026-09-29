# sagitta/Utils/__init__.py
"""Utils 子包：trace_id 生成、工具函数。

导入示例::

    from sagitta.Utils import gen_trace_id, trace_ts_ms
"""
from .trace import gen_trace_id, trace_ts_ms

__all__ = ["gen_trace_id", "trace_ts_ms"]

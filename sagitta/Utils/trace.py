# sagitta/Utils/trace.py
import time
import uuid
from typing import Tuple

def gen_trace_id() -> str:
    """Python 3.12 原生 uuid7，全局有序，32 hex"""
    return uuid.uuid7().hex

def trace_ts_ms(tid: str) -> int:
    try:
        return int(tid[:12], 16)
    except Exception:
        return 0
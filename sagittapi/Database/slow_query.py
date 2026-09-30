# sagitta/Database/slow_query.py
import random
import re
import time
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from ..Log.backend import LogBackend
from ..Log.logger import get_trace

_sql_logger = LogBackend.get_logger("sg.sql.slow")
_pool_logger = LogBackend.get_logger("sg.pool")


def attach_slow_query_listener(engine: AsyncEngine, *, threshold_ms: float = 500.0,
                               sample_rate: float = 1.0, trim_len: int = 4000) -> None:
    if threshold_ms <= 0:
        return

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # type: ignore
        context._sg_t0 = time.perf_counter()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # type: ignore
        t0 = getattr(context, "_sg_t0", None)
        if t0 is None:
            return
        ms = (time.perf_counter() - t0) * 1000
        if ms < threshold_ms or random.random() > sample_rate:
            return
        tid, _ = get_trace()
        _sql_logger.warning("[SLOW] %.0fms | trace=%s | multi=%s | params=%s | sql=%s",
                            ms, tid or "-", bool(executemany), _mask(parameters), _trim(statement, trim_len))


def attach_pool_queue_monitor(engine: AsyncEngine, *, checkout_warn_ms: float = 1000.0) -> None:
    @event.listens_for(engine.sync_engine, "checkout")
    def _checkout(dbapi_conn, connection_record, connection_proxy):  # type: ignore
        connection_record._sg_co = time.perf_counter()

    @event.listens_for(engine.sync_engine, "checkin")
    def _checkin(dbapi_conn, connection_record):  # type: ignore
        t0 = getattr(connection_record, "_sg_co", None)
        if t0 is None:
            return
        ms = (time.perf_counter() - t0) * 1000
        if ms >= checkout_warn_ms:
            tid, _ = get_trace()
            _pool_logger.warning("[POOL] 连接获取耗时 %.0fms | trace=%s", ms, tid or "-")


def _mask(p: Any) -> Any:
    if p is None:
        return "()"
    if isinstance(p, dict):
        return {k: _mv(v) for k, v in list(p.items())[:8]}
    return tuple(_mv(v) for v in (list(p)[:8] if hasattr(p, "__len__") else p))

def _mv(v: Any) -> Any:
    if v is None:
        return None
    s = str(v)
    return s[:60] + f"...({len(s)})" if len(s) > 60 else s

def _trim(s: str, n: int) -> str:
    return re.sub(r"\s+", " ", s).strip()[:n]
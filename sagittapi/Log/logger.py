# sagitta/Log/logger.py
import logging
import sys
import threading
from contextvars import ContextVar
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional, Tuple, Any
# SgSettingLog 类型别名：接受 SimpleNamespace 或 dict
SgSettingLog = Any

_TRACE_ID: ContextVar[str] = ContextVar("trace_id", default="-")
_SPAN_ID: ContextVar[str] = ContextVar("span_id", default="-")
_initialized = False


def get_trace() -> Tuple[str, str]:
    return _TRACE_ID.get(), _SPAN_ID.get()


def set_trace(tid: str, sid: str = "-") -> Tuple[Any, Any]:
    """设置当前上下文的 trace/span，返回可用于 reset 的 token。

    返回 token 是为了让中间件在请求结束时精确复位，避免线程/任务复用
    时上一个请求的 trace_id 泄漏到下一个（ContextVar 的经典陷阱）。
    """
    t1 = _TRACE_ID.set(tid)
    t2 = _SPAN_ID.set(sid)
    return (t1, t2)


def reset_trace(tokens: Any) -> None:
    """用 set_trace 返回的 token 复位上下文。"""
    if not tokens:
        return
    try:
        t1, t2 = tokens
        _SPAN_ID.reset(t2)
        _TRACE_ID.reset(t1)
    except (ValueError, LookupError):
        # token 跨上下文复用等异常情况下忽略，不影响主流程
        pass


class TraceFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _TRACE_ID.get()
        record.span_id = _SPAN_ID.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        import json
        out = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
               "trace_id": getattr(record, "trace_id", "-"), "span_id": getattr(record, "span_id", "-"),
               "logger": record.name, "line": record.lineno, "msg": record.getMessage()}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


def init_root_logger(cfg: SgSettingLog, app_name: str = "app") -> None:
    global _initialized
    if _initialized:
        return
    _initialized = True
    root = logging.getLogger()
    root.setLevel(cfg.level.upper())
    root.handlers.clear()
    root.filters.clear()
    fmt_txt = "%(asctime)s | %(levelname)-5s | %(trace_id)s | %(name)s:%(lineno)d | %(message)s"
    formatter = JsonFormatter(datefmt="%Y-%m-%d %H:%M:%S") if cfg.format == "json" \
                else logging.Formatter(fmt_txt, datefmt="%Y-%m-%d %H:%M:%S")
    tf = TraceFilter()
    if cfg.console:
        h = logging.StreamHandler(sys.stdout); h.setFormatter(formatter); h.addFilter(tf); root.addHandler(h)
    Path(cfg.root_dir).mkdir(parents=True, exist_ok=True)
    log_path = Path(cfg.root_dir) / f"{app_name}.log"
    if cfg.max_bytes > 0:
        from logging.handlers import RotatingFileHandler
        fh = RotatingFileHandler(log_path, maxBytes=cfg.max_bytes, backupCount=cfg.backup_count, encoding=cfg.encoding)
    else:
        fh = TimedRotatingFileHandler(log_path, when=cfg.when, interval=cfg.interval,
                                      backupCount=cfg.backup_count, encoding=cfg.encoding)
    fh.setFormatter(formatter); fh.addFilter(tf); root.addHandler(fh)
    for name in ("sqlalchemy.engine", "sqlalchemy.pool"):
        lg = logging.getLogger(name); lg.setLevel(logging.WARNING); lg.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
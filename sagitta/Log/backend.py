# sagitta/Log/backend.py
import logging
import sys
from typing import Literal, Any
from .logger import init_root_logger, set_trace, get_trace, SgSettingLog

LogBackendType = Literal["stdlib", "loguru"]


def _require_py311_for_loguru() -> None:
    if sys.version_info < (3, 11):
        raise RuntimeError(
            "当前配置启用 loguru 日志后端，但 loguru 依赖 Python 3.11+ 的 contextvars 传播语义。\n"
            f"当前版本: {'.'.join(map(str, sys.version_info[:3]))}。解决：①升级 Python>=3.11 ②设 SG_LOG_BACKEND=stdlib"
        )


class LogBackend:
    _ready = False
    _kind: LogBackendType = "stdlib"

    @classmethod
    def init(cls, kind: LogBackendType, cfg: SgSettingLog) -> None:
        cls._kind = kind
        if kind == "loguru":
            _require_py311_for_loguru(); cls._init_loguru(cfg)
        else:
            cls._init_stdlib(cfg)
        cls._ready = True

    @classmethod
    def get_logger(cls, name: str):
        if cls._kind == "loguru":
            from loguru import logger
            return logger.bind(logger_name=name)
        return logging.getLogger(name)

    @classmethod
    def patch_current(cls, **kw) -> None:
        """同步 trace 上下文到当前日志后端。

        loguru 后端通过 patcher 自动从 ContextVar 读取 trace_id，无需手动调用；
        stdlib 后端通过 TraceFilter 读取。此方法仅用于需要显式设置
        trace 的场景（如异步任务无中间件上下文时）。
        """
        if kw.get("trace_id"):
            set_trace(kw["trace_id"], kw.get("span_id", "-"))

    @classmethod
    def _init_stdlib(cls, cfg: SgSettingLog) -> None:
        init_root_logger(cfg, app_name=getattr(cfg, "app_name", "app") or "app")

    @classmethod
    def _init_loguru(cls, cfg: SgSettingLog) -> None:
        from loguru import logger
        from pathlib import Path
        import json
        logger.remove()

        # patcher：每条日志记录自动从 ContextVar 注入 trace_id / span_id，
        # 使 loguru 后端与 stdlib 后端的 trace 贯通行为一致。
        # TraceMiddleware 已通过 set_trace() 设置了 ContextVar，
        # 这里只需让 loguru 在格式化时读取即可。
        from .logger import get_trace as _get_trace

        def _trace_patcher(record):
            tid, sid = _get_trace()
            record["extra"].setdefault("trace_id", tid)
            record["extra"].setdefault("span_id", sid)

        logger.configure(patcher=_trace_patcher)

        level = cfg.level.upper()
        Path(cfg.root_dir).mkdir(parents=True, exist_ok=True)
        log_path = Path(cfg.root_dir) / "app.log"

        def _serializer(record) -> str:
            out = {"ts": record["time"].strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3], "level": record["level"].name,
                   "trace_id": record["extra"].get("trace_id", "-"), "span_id": record["extra"].get("span_id", "-"),
                   "logger": record["name"], "line": record["line"], "msg": record["message"]}
            if record["exception"]:
                out["exc"] = record["exception_text"]
            return json.dumps(out, ensure_ascii=False)

        kw = dict(encoding=cfg.encoding, enqueue=True, backtrace=False, diagnose=False, level=level)
        if cfg.max_bytes > 0:
            logger.add(log_path, rotation=f"{cfg.max_bytes} bytes", retention=cfg.backup_count,
                       format=_serializer if cfg.format == "json" else None, **kw)
        else:
            logger.add(log_path, rotation=cfg.when, retention=cfg.backup_count,
                       format=_serializer if cfg.format == "json" else None, **kw)
        if cfg.console:
            logger.add(sys.stdout, enqueue=False, backtrace=False, diagnose=False,
                       format=_serializer if cfg.format == "json" else None, level=level)

        class InterceptHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                try:
                    level = logger.level(record.levelname).name
                except ValueError:
                    level = record.levelno
                logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())

        logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
        for name in ("uvicorn", "uvicorn.access", "gunicorn"):
            logging.getLogger(name).handlers = [InterceptHandler()]
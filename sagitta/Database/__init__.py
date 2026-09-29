# sagitta/Database/__init__.py
"""Database 子包：引擎注册、会话依赖、基类仓库、游标分页、慢 SQL。

导入示例::

    from sagitta.Database import session_dep, EngineRegistry, BaseRepository
"""
from .engine import EngineRegistry
from .session import session_dep, session_scope, probe_session, readonly_session_dep
from .repository import (
    BaseRepository, KeysetPage, encode_cursor, decode_cursor,
    CursorError, MAX_OFFSET,
)
from .base import Base

__all__ = [
    # engine
    "EngineRegistry",
    # session
    "session_dep", "session_scope", "probe_session", "readonly_session_dep",
    # repository
    "BaseRepository", "KeysetPage", "encode_cursor", "decode_cursor",
    "CursorError", "MAX_OFFSET",
    # base
    "Base",
]

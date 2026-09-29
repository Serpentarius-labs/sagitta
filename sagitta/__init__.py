# sagitta/__init__.py
"""Sagitta — FastAPI 工程化骨架。

顶层快捷导入（IDE 自动补全友好）::

    from sagitta import bootstrap, Controller, SgResponse, session_dep
"""
from .app.bootstrap import bootstrap
from .Flow import Controller, SgResponse, Page, PageQuery
from .Core import SgError, ServiceBase, Inject
from .Database import session_dep, EngineRegistry, BaseRepository
from .Config import SgSettings, get_settings

__version__ = "0.1.0"

__all__ = [
    # app
    "bootstrap",
    # flow
    "Controller", "SgResponse", "Page", "PageQuery",
    # core
    "SgError", "ServiceBase", "Inject",
    # database
    "session_dep", "EngineRegistry", "BaseRepository",
    # config
    "SgSettings", "get_settings",
    # version
    "__version__",
]

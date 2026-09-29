# sagitta/Flow/__init__.py
"""Flow 子包：控制器基类、统一响应、分页。

导入示例::

    from sagitta.Flow import Controller, SgResponse, Page, PageQuery
"""
from .controller import Controller, mount_all
from .page import Page, PageQuery, CursorQuery, CursorPage
from .registry import register, registered, report, RegistryError
from .response import SgResponse, Resp, RespError

__all__ = [
    "Controller", "mount_all",
    "Page", "PageQuery", "CursorQuery", "CursorPage",
    "register", "registered", "report", "RegistryError",
    "SgResponse", "Resp", "RespError",
]

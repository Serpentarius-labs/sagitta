# sagitta/app/__init__.py
"""app 子包：应用装配入口、生命周期管理。

导入示例::

    from sagittapi.app import bootstrap, make_lifespan
"""
from .bootstrap import (
    bootstrap, register_exception_handlers, register_health,
    import_module_safe,
)
from .lifespan import (
    make_lifespan, is_shutting_down,
    StartupHook, ShutdownHook,
)

__all__ = [
    # bootstrap
    "bootstrap", "register_exception_handlers", "register_health",
    "import_module_safe",
    # lifespan
    "make_lifespan", "is_shutting_down",
    "StartupHook", "ShutdownHook",
]

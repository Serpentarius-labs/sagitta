# sagitta/Config/__init__.py
"""Config 子包：YAML + env 合并加载、类型校验。

导入示例::

    from sagitta.Config import SgSettings, get_settings, ConfigError
"""
from .setting import (
    SgSettings, DbConfig, ConfigError,
    build_settings, set_settings, init_settings, get_settings, reload_settings,
)
from .loader import ConfigLoader, DEFAULTS, ENV_PREFIX

__all__ = [
    # setting
    "SgSettings", "DbConfig", "ConfigError",
    "build_settings", "set_settings", "init_settings", "get_settings", "reload_settings",
    # loader
    "ConfigLoader", "DEFAULTS", "ENV_PREFIX",
]

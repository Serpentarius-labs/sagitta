# sagitta/__init__.py
# 顶层尽量保持干净，避免循环导入；需要时从子包显式取
from .app.bootstrap import bootstrap

__version__ = "0.1.0"
__all__ = ["bootstrap"]
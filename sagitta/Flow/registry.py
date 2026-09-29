# sagitta/Flow/registry.py
"""控制器中央登记表：所有路由的唯一可信来源。

为什么用登记表而不是反射 `__subclasses__`：
  * **顺序确定**：按注册顺序挂载，启动清单可复现，便于排错。
  * **不依赖包名前缀**：反射版需要 `root_pkg="app.controllers"` 这种
    硬编码过滤。本框架会被下游项目以 submodule 引入，框架层**绝不能**
    假设宿主的业务包叫什么名字。
  * **零反射**：`mount_all` 纯遍历，不看 MRO。

注册是自动的：继承 `Controller` 即登记（见 controller.py 的
`__init_subclass__`），业务侧无需手写装饰器。`register()` 作为显式入口
保留，供动态构造的控制器使用。
"""
import logging
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger("sagitta")


class RegistryError(RuntimeError):
    """注册/挂载阶段的配置错误，必须吵出来，不允许静默少路由。"""


# ---- 单一可信来源 ----
_registry: List[type] = []            # 按注册顺序
_prefix_map: dict[str, type] = {}     # 前缀 -> 首个占用者，用于冲突检测
_mounted: set[type] = set()           # 已挂载者，用于重复挂载检测与遗漏告警


def register(cls: type) -> type:
    """把控制器登记到中央表。可重入，重复注册是安全的 noop。

    无 `prefix` 的类视为抽象中间基类，不登记（业务侧常用
    `class BaseCrudController(Controller)` 做公共逻辑）。
    """
    prefix = getattr(cls, "prefix", "") or ""
    if not prefix:
        return cls
    if cls in _registry:
        return cls
    owner = f"{cls.__module__}.{cls.__name__}"
    if prefix in _prefix_map and _prefix_map[prefix] is not cls:
        other = _prefix_map[prefix]
        raise RegistryError(
            f"路由前缀冲突: {prefix!r} 已被 {other.__module__}.{other.__name__} 占用，"
            f"{owner} 无法注册。请修改其中一个的 prefix。"
        )
    _registry.append(cls)
    _prefix_map[prefix] = cls
    logger.debug("registered %s -> %s", cls.__name__, prefix)
    return cls


def registered() -> List[type]:
    """返回当前已登记的控制器快照（只读）。"""
    return list(_registry)


def reset_for_test() -> None:
    """清空登记表。测试隔离用。"""
    _registry.clear()
    _prefix_map.clear()
    _mounted.clear()


def mount_all(app, root_pkg: Optional[str] = None,
              on_mount: Optional[Callable[[type], None]] = None) -> int:
    """把登记表里的控制器全部挂到 FastAPI 上。纯遍历，零反射。

    router 在此刻才构建（而非 import 期），以保证守卫开关读到的是
    已初始化的配置。构建失败会直接抛出——宁可启动失败，也不要
    悄悄少一条路由。

    root_pkg : 可选过滤，仅挂载 `__module__` 以该前缀开头的控制器。
               **默认不过滤**——框架不应假设宿主业务包名。
    on_mount : 每个控制器挂载后的回调（用于宿主侧记录）。
    """
    from sagitta.Flow.controller import Controller

    count = 0
    for cls in _registry:
        if cls in _mounted:
            continue
        if root_pkg and not getattr(cls, "__module__", "").startswith(root_pkg):
            continue
        if not issubclass(cls, Controller):
            raise RegistryError(
                f"{cls.__module__}.{cls.__name__} 登记为控制器但不是 Controller 子类"
            )
        try:
            router = cls.build_router()
        except Exception as e:
            raise RegistryError(
                f"{cls.__module__}.{cls.__name__} 路由构建失败: "
                f"{type(e).__name__}: {e}"
            ) from e
        prefix = getattr(cls, "prefix", "") or ""
        # API 版本控制：version="v1" → 路由前缀变为 /v1/user
        version = getattr(cls, "version", None)
        if version:
            version = version.strip("/")
            prefix = f"/{version}{prefix}"
        app.include_router(router, prefix=prefix,
                           tags=list(getattr(cls, "tags", []) or []))
        _mounted.add(cls)
        count += 1
        logger.info("mounted %s -> %s", cls.__name__, prefix)
        if on_mount is not None:
            on_mount(cls)

    if not _registry:
        logger.warning(
            "未发现任何 Controller 子类。请确认业务模块已被导入"
            "（控制器只有被 import 才会登记）。"
        )
    return count


def report() -> Tuple[int, int]:
    """启动清单打印：挂了多少、漏了多少。返回 (ok, missing)。"""
    ok = len(_mounted)
    missing = len(_registry) - ok
    names = [f"{c.__name__}({getattr(c, 'prefix', '?')})" for c in _registry]
    logger.info("controllers registered: %s", ", ".join(names) or "(none)")
    if missing:
        unmounted = [c.__name__ for c in _registry if c not in _mounted]
        logger.warning("controllers registered but NOT mounted: %s", ", ".join(unmounted))
    return ok, missing


__all__ = ["RegistryError", "register", "registered", "reset_for_test",
           "mount_all", "report"]

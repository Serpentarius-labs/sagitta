# sagitta/Core/inject.py
"""依赖注入容器。

设计原则：
  * **无状态单例**：Service 继承 ServiceBase 即自动注册（README 硬规则 2）。
    请求级数据绝不允许写成实例属性，请走方法参数或 ContextVar。
  * **失败要吵出来**：依赖解析不到时抛错并说明怎么改，而不是静默构造一个
    空壳实例——那只会把故障推迟到更深的地方、以更难懂的形式爆发。
"""
import types
import typing
from typing import Any, Dict, Optional, TypeVar, Union

T = TypeVar("T")


class InjectionError(KeyError):
    """服务未注册 / 具名服务缺失。消息里带修复指引。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class Inject:
    """标注「按名字注入」的占位符。

    用法：
        class OrderController(Controller):
            svc: OrderService = Inject("primary")
    """

    __slots__ = ("name",)

    def __init__(self, name: Optional[str] = None):
        self.name = name


class Container:
    _singletons: Dict[type, Any] = {}
    _named: Dict[type, Dict[str, Any]] = {}

    @classmethod
    def register(cls, t: type, inst: Any = None, name: Optional[str] = None) -> Any:
        inst = inst if inst is not None else t()
        if name:
            cls._named.setdefault(t, {})[name] = inst
        else:
            cls._singletons[t] = inst
        return inst

    @classmethod
    def resolve(cls, t: type, name: Optional[str] = None) -> Any:
        """解析服务。找不到时抛 InjectionError（不再静默自动构造）。

        自动构造会掩盖「忘记继承 ServiceBase」「类型注解拼错」这类错误，
        与框架「把坑变成运行时错误」的哲学直接冲突，故移除。
        """
        if name:
            pool = cls._named.get(t)
            if pool and name in pool:
                return pool[name]
            available = list(pool) if pool else []
            raise InjectionError(
                f"未找到具名服务 {getattr(t, '__name__', t)}[{name!r}]。"
                f"已注册的名字: {available or '(无)'}。"
                f"请用 Container.register({getattr(t, '__name__', t)}, name={name!r}) 注册。"
            )
        if t in cls._singletons:
            return cls._singletons[t]
        raise InjectionError(
            f"服务未注册: {getattr(t, '__name__', t)}。"
            f"请让它继承 sagitta.Core.inject.ServiceBase（子类会自动注册），"
            f"或显式调用 Container.register({getattr(t, '__name__', t)})。"
        )

    @classmethod
    def try_resolve(cls, t: type, name: Optional[str] = None) -> Optional[Any]:
        """解析失败返回 None，供可空依赖使用。"""
        try:
            return cls.resolve(t, name)
        except InjectionError:
            return None

    @classmethod
    def registered(cls) -> Dict[type, Any]:
        """已注册服务快照（只读）。启动清单打印用。"""
        return dict(cls._singletons)

    @classmethod
    def reset_for_test(cls) -> None:
        cls._singletons.clear()
        cls._named.clear()


class ServiceBase:
    """业务 Service 基类：继承即注册为无状态单例。

    以下划线开头的类名视为抽象中间基类，不注册。
    """

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        if cls.__name__.startswith("_"):
            return
        if getattr(cls, "__sg_no_autoregister__", False):
            return
        Container.register(cls)


_MISSING = object()

# typing.Union 与 PEP 604 的 `X | None`（types.UnionType）是两种不同对象，
# 只判前者会在 Python 3.10+ 漏掉 `svc: UserService | None` 这类写法。
_UNION_ORIGINS = (Union, types.UnionType) if hasattr(types, "UnionType") else (Union,)


def _unwrap(t):
    """剥掉 Optional / PEP 604 联合类型，返回首个非 None 的成员。"""
    origin = getattr(t, "__origin__", None)
    if origin in _UNION_ORIGINS:
        args = [a for a in getattr(t, "__args__", ()) if a is not type(None)]
        return args[0] if args else t
    return t


def _is_optional(t) -> bool:
    origin = getattr(t, "__origin__", None)
    if origin not in _UNION_ORIGINS:
        return False
    return any(a is type(None) for a in getattr(t, "__args__", ()))


def _collect_hints(inst: Any) -> Dict[str, Any]:
    """收集实例所属类的全部类型注解（沿 MRO，含父类）。

    不能用 `getattr(inst, "__annotations__")`：在 Python 3.14（PEP 649
    惰性注解）下，实例上取 `__annotations__` 返回空 dict，注解只挂在类上。
    用 `typing.get_type_hints(cls)` 沿 MRO 收集并解析字符串注解；失败时
    回退到手动遍历 `vars(klass)["__annotations__"]`。
    """
    cls = type(inst)
    try:
        return typing.get_type_hints(cls)
    except Exception:
        hints: Dict[str, Any] = {}
        for klass in reversed(cls.__mro__):
            ann = vars(klass).get("__annotations__")
            if ann:
                hints.update(ann)
        return hints


def autowire(inst: Any, strict: bool = True) -> Any:
    """按类注解把容器里的 Service 注入实例属性。

    注入判定（避免误伤普通类属性注解）：
      * `x: SomeService = Inject("name")` → 显式注入，无论类型。
      * `x: SomeService`（无默认值，属性缺失）且 SomeService 是 ServiceBase
        子类 → 自动注入。
      * `prefix: str = ""` / `tags: List[str] = []` 这类**有默认值**的注解 →
        跳过（不是服务）。
      * 无默认值但类型不是 ServiceBase 子类（如 `foo: int`）→ 跳过，不当服务解析。

    strict=True（默认）：需要注入但解析不到时抛 InjectionError，早失败；
    注解为 Optional 且解析不到时置 None，不报错。
    """
    hints = _collect_hints(inst)
    for name, t in hints.items():
        if name.startswith("_"):
            continue
        cur = getattr(inst, name, _MISSING)
        explicit = isinstance(cur, Inject)
        want_name = cur.name if explicit else None
        if not explicit and cur is not _MISSING:
            continue  # 已有默认值的类属性，不参与注入

        target = _unwrap(t)
        if not isinstance(target, type):
            continue
        # 非显式 Inject 时，只注入 ServiceBase 子类，避免把 str/int 等当服务
        if not explicit and not issubclass(target, ServiceBase):
            continue

        try:
            setattr(inst, name, Container.resolve(target, want_name))
        except InjectionError:
            if strict and not _is_optional(t):
                raise
            if _is_optional(t) and cur is _MISSING:
                setattr(inst, name, None)
    return inst


__all__ = ["Container", "ServiceBase", "Inject", "InjectionError", "autowire"]

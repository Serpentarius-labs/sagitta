# sagitta/Flow/controller.py
"""Controller 基类：路由声明 + 自动登记 + 依赖注入 + 响应守卫接线。

与 registry 的分工：
  * 本模块负责「把类变成 router」，并在实例化后完成服务注入。
  * registry 负责「把 router 挂到 app」，是挂载的唯一可信来源。
  * `mount_all` 在此re-export，仅为兼容既有 import 路径。
"""
import inspect
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from ..Core.inject import autowire
from .registry import register, report, reset_for_test  # noqa: F401  (re-export)
from .registry import mount_all as _registry_mount_all
from .response import resp_model_for, wrap_endpoint

logger = logging.getLogger("sagitta")


def _route_meta(path: str, methods: Sequence[str], **extra: Any) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"path": path, "methods": list(methods)}
    meta.update({k: v for k, v in extra.items() if v is not None})
    return meta


class Controller:
    """业务控制器基类。

    继承即完成两件事：
      1. `__init_subclass__` 构建 router（早失败：路由声明有错在 import 期就炸）
      2. 构建时自动登记进中央 registry，并在实例化后注入 ServiceBase 子类

    用法：
        class UserController(Controller):
            prefix = "/user"
            tags = ["用户"]
            svc: UserService          # 自动注入，无需 __init__

            @Controller.get("/page")
            async def page(self, q: PageQuery = Depends()) -> Page[UserVO]:
                return await self.svc.page(db, q)
    """

    prefix: str = ""
    tags: List[str] = []
    _router: Optional[APIRouter] = None

    # ---- 路由声明 ----

    @staticmethod
    def get(path: str, **kw: Any):
        def deco(fn):
            fn._sg_route = _route_meta(path, ["GET"], **kw)
            return fn
        return deco

    @staticmethod
    def post(path: str, **kw: Any):
        def deco(fn):
            fn._sg_route = _route_meta(path, ["POST"], **kw)
            return fn
        return deco

    @staticmethod
    def put(path: str, **kw: Any):
        def deco(fn):
            fn._sg_route = _route_meta(path, ["PUT"], **kw)
            return fn
        return deco

    @staticmethod
    def patch(path: str, **kw: Any):
        def deco(fn):
            fn._sg_route = _route_meta(path, ["PATCH"], **kw)
            return fn
        return deco

    @staticmethod
    def delete(path: str, **kw: Any):
        def deco(fn):
            fn._sg_route = _route_meta(path, ["DELETE"], **kw)
            return fn
        return deco

    # ---- 构建 ----

    @classmethod
    def _collect_routes(cls) -> List[tuple]:
        """收集 (name, fn, meta) 并按**确定性顺序**排列。

        两条排序规则，都不是审美选择：

        1. **子类覆盖优先、每个方法只登记一次**。
           直接遍历 `cls.__mro__` 的 `__dict__` 会把「子类覆盖的方法」与
           「父类同名方法」各注册一次，导致同路径重复路由。这里按 MRO 顺序
           （子类在前）取首个定义，覆盖语义才正确。

        2. **字面量路径排在参数化路径之前**。
           Starlette 按注册顺序匹配路由。若 `/{user_id}` 声明在 `/page` 之前，
           请求 `/user/page` 会先命中 `/{user_id}`，然后因为 "page" 无法解析成
           UUID 而返回 422 —— 这个错误极难排查，因为路由表看起来完全正常。
           框架自动挂载路由，就有责任保证顺序不依赖开发者的书写次序。
        """
        seen: set = set()
        out: List[tuple] = []
        for base in cls.__mro__:
            for name, fn in vars(base).items():
                if name in seen or name.startswith("__"):
                    continue
                seen.add(name)
                meta = getattr(fn, "_sg_route", None)
                if meta is None:
                    continue
                if not callable(fn):
                    continue
                out.append((name, fn, meta))

        def _order(item: tuple) -> tuple:
            path = item[2].get("path", "")
            # 参数占位符少的先注册；同数量时按路径字典序，保证确定性
            return (path.count("{"), path)

        out.sort(key=_order)
        return out

    @classmethod
    def _guard_flags(cls) -> tuple:
        """读取守卫开关。配置未就绪时按 README 默认值（都开）。"""
        try:
            from ..Config.setting import get_settings
            s = get_settings()
            return bool(s.app.check_do_leak), bool(s.app.wrap_response)
        except Exception:
            return True, True

    @classmethod
    def build_router(cls, *, check_do_leak: Optional[bool] = None,
                     wrap: Optional[bool] = None, instance: Optional[Any] = None) -> APIRouter:
        """构建本类的 router。

        守卫开关默认**延迟到挂载时**由 `mount_all` 传入。原因：子类在
        import 期就会被 `__init_subclass__` 触发构建，而宿主项目通常是
        「先 import 业务模块、后初始化配置」，此时读配置只会拿到默认值。
        延迟到挂载时统一读取，才能保证 `SG_CHECK_DO_LEAK=0` 真的生效。
        """
        # 必须查 cls.__dict__ 而非属性查找：否则子类会沿 MRO 取到父类
        # 已构建的 router，把自己的路由整个丢掉。
        existing = cls.__dict__.get("_router")
        if existing is not None:
            return existing

        router = APIRouter()
        inst = cls() if instance is None else instance

        # 依赖注入：ServiceBase 子类通过类型注解自动装配（README 硬规则 2）
        autowire(inst, strict=True)

        if check_do_leak is None or wrap is None:
            cfg_leak, cfg_wrap = cls._guard_flags()
            check_do_leak = cfg_leak if check_do_leak is None else check_do_leak
            wrap = cfg_wrap if wrap is None else wrap

        for name, fn, meta in cls._collect_routes():
            bound = fn.__get__(inst, cls)
            route_path = meta["path"]
            methods = meta["methods"]

            # OpenAPI：把返回注解包成 Resp[T]，让 /docs 显示完整壳结构。
            # 只在会套壳时声明，否则 schema 与实际响应不符。
            response_model = meta.get("response_model")
            if response_model is None and wrap:
                sig = inspect.signature(fn)
                response_model = resp_model_for(sig.return_annotation)

            wrapped = wrap_endpoint(
                bound, route_path=f"{cls.prefix}{route_path}",
                check_do_leak=check_do_leak, wrap=wrap,
            )

            add_kw: Dict[str, Any] = {
                "methods": methods,
                "tags": list(cls.tags or []),
                "name": meta.get("name") or name,
            }
            for key in ("status_code", "summary", "description", "response_description",
                        "deprecated", "operation_id", "dependencies", "response_class",
                        "responses", "include_in_schema", "openapi_extra"):
                if key in meta:
                    add_kw[key] = meta[key]
            if response_model is not None:
                add_kw["response_model"] = response_model
            # 未显式声明时补上框架的标准错误码语义
            add_kw.setdefault("responses", {})
            base_resp = {401: {"description": "未认证"},
                         403: {"description": "无权限"},
                         429: {"description": "限流"},
                         500: {"description": "架构违规或内部错误"}}
            merged = dict(base_resp)
            merged.update(add_kw["responses"] or {})
            add_kw["responses"] = merged

            router.add_api_route(route_path, wrapped, **add_kw)

        cls._router = router
        return router

    @classmethod
    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        if cls is Controller:
            return
        # 抽象中间基类（无 prefix）不登记，业务侧可用它放公共逻辑
        if not (getattr(cls, "prefix", "") or ""):
            return
        if getattr(cls, "__sg_abstract__", False):
            return
        # 只登记，不在 import 期构建 router。
        # 构建延迟到 mount_all：那时配置已就绪，守卫开关
        # （check_do_leak / wrap_response）才是用户真正设的值。
        # 仍然是启动期失败（早于任何请求），不会退化成线上 500。
        register(cls)


def mount_all(app: FastAPI, root_pkg: Optional[str] = None) -> int:
    """挂载全部已登记控制器（委托 registry，保持单一可信来源）。

    `root_pkg` 保留仅为兼容旧调用；**默认不过滤**——框架层不应假设宿主
    业务包的名字（本框架会被下游以 submodule 引入）。
    """
    return _registry_mount_all(app, root_pkg=root_pkg)


__all__ = ["Controller", "mount_all", "register", "registered", "report",
           "reset_for_test"]

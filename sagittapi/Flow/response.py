# sagitta/Flow/response.py
"""统一响应壳 + README 硬规则 1 的两道守卫。

守卫一：endpoint 只返回裸数据。返回手写响应壳 → WrappedResponseError。
守卫二：DO 不得流出 Service 层。返回对象里出现 ORM 映射实例 → DOLeakError，
        并给出字段路径（如 `[0].name`），便于直接定位。

实现方式：在 `Controller.build_router` 里把 endpoint 包一层，拦截**序列化之前**
的原始 Python 返回值。这样既能递归检测 ORM 实例，又能决定是否套壳。

不依赖 FastAPI 内部结构（不改 `dependant`），只用公开的 `add_api_route`
与 `inspect.signature` 语义，跨版本更稳。
"""
import dataclasses
import functools
import inspect
import typing
from typing import Any, Dict, Generic, List, Optional, Set, Tuple, TypeVar, Union

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from starlette.responses import Response

from ..Core.errors import DOLeakError, SUCCESS_CODE, WrappedResponseError
from ..Log.logger import get_trace

T = TypeVar("T")

# 递归扫描的防护参数：payload 再大也不该把请求线程拖死。
_MAX_DEPTH = 12
_MAX_NODES = 20000

_SHELL_KEYS = frozenset({"code", "message", "data"})


class SgResponse(Response, Generic[T]):
    """统一响应壳。`_sg_final` 标记它已完成包装，守卫不再二次处理。"""

    _sg_final = True
    media_type = "application/json"

    def __init__(self, data: Any = None, code: Any = SUCCESS_CODE,
                 message: str = "success",
                 status_code: int = 200, headers: Optional[Dict[str, str]] = None):
        content: Dict[str, Any] = {"code": code, "message": message, "data": data}
        tid, _ = get_trace()
        final_headers = dict(headers or {})
        if tid and tid != "-":
            final_headers["X-Trace-Id"] = tid
            try:
                from ..Config.setting import get_settings
                if get_settings().app.echo_trace_in_body:
                    content["trace_id"] = tid
            except Exception:
                pass  # 配置未加载时不影响响应
        self._sg_content = content
        self._sg_status = status_code
        # 不调 Response.__init__（它要求 body/content），由 __call__ 走 JSONResponse 语义
        body = jsonable_encoder(content)
        import json
        raw = json.dumps(body, ensure_ascii=False, allow_nan=False,
                         indent=None, separators=(",", ":")).encode("utf-8")
        super().__init__(content=raw, status_code=status_code, headers=final_headers,
                         media_type=self.media_type)

    def __repr__(self) -> str:
        return f"SgResponse(code={self._sg_content.get('code')}, status={self._sg_status})"


class Resp(BaseModel, Generic[T]):
    """OpenAPI 用的响应 schema：`Resp[UserVO]` → 文档里显示完整壳结构。

    code 类型为 int|str：成功恒 0，错误为业务码字符串。
    """

    code: Union[int, str] = SUCCESS_CODE
    message: str = "success"
    data: T


class RespError(BaseModel):
    """OpenAPI 错误响应壳：与成功壳同构，但 data 可空、code 必为字符串。"""

    code: Union[int, str]
    message: str
    data: Any = None


def resp_model_for(annotation: Any) -> Optional[Any]:
    """把 endpoint 的返回注解包成 `Resp[T]`，供 OpenAPI 使用。

    失败一律返回 None（退回 FastAPI 默认行为），绝不因为文档生成而让启动崩掉。
    """
    if annotation is None or annotation is inspect.Parameter.empty:
        return None
    try:
        if annotation is type(None):
            return Resp[None]  # type: ignore[valid-type]
        if isinstance(annotation, type) and issubclass(annotation, Response):
            return None  # 自己管响应，不套壳
        return Resp[annotation]  # type: ignore[valid-type]
    except Exception:
        return None


# ---------- 守卫一：手写响应壳检测 ----------

def looks_like_wrapped(value: Any) -> Optional[List[str]]:
    """判断返回值是否形似已包装的响应壳。

    判据收紧到「同时具备 code + (data 或 message) 且 code 是 int」，
    避免把 `{"code": "CN", "message": "..."}` 这类正常业务字段误伤。
    命中时返回涉及的壳字段名（用于报错信息）。
    """
    if not isinstance(value, dict):
        return None
    keys = set(value.keys())
    if "code" not in keys:
        return None
    if not (keys & {"data", "message"}):
        return None
    code = value.get("code")
    if isinstance(code, bool) or not isinstance(code, int):
        return None
    return sorted(keys & _SHELL_KEYS)


# ---------- 守卫二：DO 泄漏检测 ----------

def _is_orm_instance(obj: Any) -> bool:
    """SQLAlchemy 映射实例的可靠标记：带 `_sa_instance_state`。"""
    return hasattr(obj, "_sa_instance_state")


def _should_skip(obj: Any) -> bool:
    """不深入扫描的对象：请求/响应、模块、函数、类型、标量等。"""
    if isinstance(obj, Response):
        return True
    if isinstance(obj, (str, bytes, bytearray, int, float, bool, complex, type(None))):
        return True
    if inspect.ismodule(obj) or inspect.isroutine(obj) or isinstance(obj, type):
        return True
    # FastAPI/Starlette 的 Request、WebSocket
    cls_name = type(obj).__name__
    if cls_name in ("Request", "WebSocket", "HTTPConnection"):
        return True
    return False


def find_do_leak(value: Any, *, path: str = "", depth: int = 0,
                 _seen: Optional[Set[int]] = None,
                 _budget: Optional[List[int]] = None) -> Optional[str]:
    """递归查找 ORM 映射实例，返回其字段路径；没有则返回 None。

    路径形如 `[0].orders[2]`，与 README 承诺的报错格式一致。
    遇到 ORM 实例立即上报，**不再深入其属性**——那会触发懒加载，
    在异步上下文里会直接抛错。
    """
    if _seen is None:
        _seen = set()
    if _budget is None:
        _budget = [_MAX_NODES]

    if depth > _MAX_DEPTH or _budget[0] <= 0:
        return None
    if _should_skip(value):
        return None

    oid = id(value)
    if oid in _seen:
        return None
    _seen.add(oid)
    _budget[0] -= 1

    if _is_orm_instance(value):
        return path or "<root>"

    # dict
    if isinstance(value, dict):
        for k, v in value.items():
            sub = f"{path}[{k!r}]"
            hit = find_do_leak(v, path=sub, depth=depth + 1, _seen=_seen, _budget=_budget)
            if hit:
                return hit
        return None

    # list / tuple / set / frozenset
    if isinstance(value, (list, tuple, set, frozenset)):
        for i, v in enumerate(value):
            sub = f"{path}[{i}]" if isinstance(value, (list, tuple)) else f"{path}{{}}"
            hit = find_do_leak(v, path=sub, depth=depth + 1, _seen=_seen, _budget=_budget)
            if hit:
                return hit
        return None

    # pydantic BaseModel
    if isinstance(value, BaseModel):
        fields = getattr(type(value), "model_fields", None) or {}
        for name in fields:
            try:
                v = getattr(value, name, None)
            except Exception:
                continue
            hit = find_do_leak(v, path=f"{path}.{name}", depth=depth + 1,
                               _seen=_seen, _budget=_budget)
            if hit:
                return hit
        return None

    # dataclass
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for f in dataclasses.fields(value):
            v = getattr(value, f.name, None)
            hit = find_do_leak(v, path=f"{path}.{f.name}", depth=depth + 1,
                               _seen=_seen, _budget=_budget)
            if hit:
                return hit
        return None

    # Page[T] 之类的 Generic 容器已在上一步 BaseModel 覆盖；
    # 其余普通对象不主动扫描其 __dict__，避免误伤业务 VO 的性能与语义。
    return None


# ---------- 端点包装 ----------

def _resolved_signature(fn: Any) -> inspect.Signature:
    """返回「注解已全部解析为真实对象」的签名。

    必须这样做的原因：包装函数的 `__globals__` 是本模块，若原函数用了
    `from __future__ import annotations`（字符串注解），FastAPI 的
    `get_typed_signature` 会在错误的命名空间里 eval 而 NameError。
    提前用 get_type_hints 解析成真实类型即可绕开。
    """
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}
    sig = inspect.signature(fn)
    params = []
    for name, p in sig.parameters.items():
        ann = hints.get(name, p.annotation)
        params.append(p.replace(annotation=ann))
    ret = hints.get("return", sig.return_annotation)
    return sig.replace(parameters=params, return_annotation=ret)


def wrap_endpoint(fn: Any, *, route_path: str, check_do_leak: bool, wrap: bool,
                  status_code: int = 200) -> Any:
    """包装 endpoint：先跑守卫，再决定是否套 SgResponse。

    `status_code` 必须显式传入，不能指望 FastAPI。原因：FastAPI 只有在
    endpoint 返回**裸数据**时才应用路由声明的 status_code；一旦返回
    Response 实例，它就原样透传该实例的状态码。而本包装器的职责正是把裸数据
    转成 SgResponse，因此声明的 201/202 之类若不传进来就会被静默降级成 200。

    异步与同步分别包装，保证 FastAPI 的 `is_coroutine_callable` 判断正确——
    同步 endpoint 仍需跑在线程池里，不能被拖进事件循环阻塞。
    """

    def finalize(result: Any, call_kwargs: Optional[dict] = None) -> Any:
        # 已经是 Response（SgResponse / SgFileResponse / 自定义 JSONResponse）→ 原样放行
        if isinstance(result, Response):
            return result

        if check_do_leak:
            leak = find_do_leak(result)
            if leak:
                raise DOLeakError(
                    f"【架构违规】DO 对象流出 Service 层！位置: {route_path} -> {leak}\n"
                    f"修法：在 Service 里把 DO 转成 VO 再返回"
                    f"（UserVO.model_validate(row)），或返回 dict。",
                    path=leak,
                )

        if wrap:
            shell = looks_like_wrapped(result)
            if shell:
                raise WrappedResponseError(
                    f"【架构违规】路由 {route_path} 返回了疑似已包装的数据 {shell}\n"
                    f"修法：只返回裸数据（dict / VO / Page），框架会自动套 "
                    f"{{code, message, data}}。需要自定义状态码时用 "
                    f"SgResponse(data=..., code=201)。",
                    data={"suspect_keys": shell},
                )
            # 两层语义在此汇合：
            #   HTTP status = 路由声明值（如 POST 的 201），供网关/监控识别；
            #   body.code   = 0（成功业务码），供前端统一判断。
            # 路由 status_code 必须显式传入：endpoint 一旦返回 Response 实例，
            # FastAPI 不再应用路由声明值（默认 200）→ 201 会被静默降级。
            sg = SgResponse(data=jsonable_encoder(result),
                            code=SUCCESS_CODE, status_code=status_code)
            # 合并注入 Response 上的 header（典型：登录 Set-Cookie）。
            # FastAPI 只有在 endpoint 返回裸数据时才把 sub_response 的
            # headers 合并进最终响应；我们这里改成了「返回 SgResponse」，
            # 不手动合并则业务代码 response.set_cookie(...) 会静默丢失。
            sub = (call_kwargs or {}).get("response")
            if isinstance(sub, Response):
                for k, v in sub.headers.items():  # items() 保留重复头（多个 Set-Cookie）
                    sg.headers.append(k, v)
            return sg

        return result

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            return finalize(await fn(*args, **kwargs), kwargs)
        target = async_wrapper
    elif inspect.isasyncgenfunction(fn):
        # 生成器 endpoint（StreamingResponse 之类）不包装，原样交给 FastAPI
        return fn
    else:
        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            return finalize(fn(*args, **kwargs), kwargs)
        target = sync_wrapper

    try:
        target.__signature__ = _resolved_signature(fn)
    except (ValueError, TypeError):
        pass  # 拿不到签名时退回 functools.wraps 的 __wrapped__ 机制
    return target


__all__ = ["SgResponse", "Resp", "RespError", "resp_model_for", "wrap_endpoint",
           "find_do_leak", "looks_like_wrapped"]

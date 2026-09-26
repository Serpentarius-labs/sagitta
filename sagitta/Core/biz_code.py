# sagitta/Core/biz_code.py
"""业务码契约：HTTP status 与 body.code 解耦的注册中心。

两层语义（对应国内主流工程实践）：
  * **HTTP status** —— 协议层大类（400/401/403/404/409/429/5xx），
    给网关重试、熔断、错误率监控与通用拦截器使用。
  * **body.code** —— 业务层细粒度状态：成功恒为 ``0``；失败为大写下划线
    字符串（如 ``USER_NOT_FOUND``），给业务分支与精细化提示使用。
  * 每个业务码**必须归属一个 HTTP 大类**，两层各司其职，不要求数字相等。

宿主项目的唯一职责是维护一本码册（通常是 ``app/biz_code.py``）：

    from sagitta.Core.biz_code import BizCode, BizCodes

    class UserCodes(BizCodes):
        USER_NOT_FOUND  = BizCode(404, "用户不存在")
        USERNAME_TAKEN  = BizCode(409, "用户名已被占用")

抛出：

    raise BizError(UserCodes.USERNAME_TAKEN)

声明接口可能抛出的业务码（自动进入 OpenAPI）：

    @Controller.post("/")
    @raises(UserCodes.USERNAME_TAKEN)
    async def create(...): ...

设计边界：
  * 注册表是**进程内、启动期**设施：只约束本服务内的 raise 与 typo，
    不承担跨服务码传播——跨服务时 body 里就是自描述的字符串。
  * 本模块不依赖 errors / response / config，依赖方向只允许
    errors -> biz_code，保证无环、可独立测试。
"""
import re
from typing import Any, Dict, List, Optional, Tuple

_CODE_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class BizCodeConflictError(RuntimeError):
    """同名业务码被重复声明且 HTTP 大类/文案不一致——启动期直接失败。"""


class BizCode:
    """单个业务码声明。

    ======== ===========
    字段     含义
    ======== ===========
    name     码名字符串，序列化进 body.code；由码册元类回填
    status   归属的 HTTP 状态码（协议大类）
    message  默认文案，可在 raise 时覆盖
    ======== ===========
    """

    __slots__ = ("name", "status", "message")

    def __init__(self, status: int, message: str):
        if not isinstance(status, int) or not (400 <= status <= 599):
            raise ValueError(f"BizCode 的 HTTP 大类必须是 400-599 的整数，得到 {status!r}")
        if not message or not isinstance(message, str):
            raise ValueError("BizCode 必须携带非空默认 message")
        self.name: str = ""
        self.status: int = status
        self.message: str = message

    # 让 BizCode 在任何需要「码字符串」的上下文里直接可用
    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"<BizCode {self.name or '?'} {self.status}>"


class BizCodeRegistry:
    """进程内码表：name -> BizCode。私有构造，全部走类方法。"""

    _codes: Dict[str, BizCode] = {}

    @classmethod
    def register(cls, code: BizCode, *, book: str) -> None:
        """登记一个码。

        冲突策略是刻意的「同则幂等、异则报错」：
        多码册声明同一字符串时，语义一致是好事（全局可复用）；
        但同名不同 status/message 会让前端无所适从，必须在启动期吵出来。
        """
        old = cls._codes.get(code.name)
        if old is not None:
            if old.status == code.status and old.message == code.message:
                return
            raise BizCodeConflictError(
                f"业务码 {code.name} 冲突：\n"
                f"  已声明于 {book}: HTTP {old.status} / {old.message}\n"
                f"  再声明: HTTP {code.status} / {code.message}"
            )
        cls._codes[code.name] = code

    @classmethod
    def resolve(cls, ref: Any) -> BizCode:
        """把 BizCode / 码名字符串解析成已注册的 BizCode。

        未注册即报错：raise 一个 typo 码（``USER_NTO_FOUND``）若静默放行，
        会以前端收到陌生字符串的形式在更晚的时候爆发。
        """
        if isinstance(ref, BizCode):
            code = ref
        elif isinstance(ref, str):
            code = cls._codes.get(ref)
            if code is None:
                raise BizCodeConflictError(f"业务码 {ref!r} 未在任何 BizCodes 码册中注册")
        else:
            raise TypeError(f"BizError 只接受 BizCode 或码名字符串，得到 {type(ref).__name__}")
        # 即使传入 BizCode 实例，也以注册表为准（防绕过注册的游离实例）
        registered = cls._codes.get(code.name)
        if registered is None:
            raise BizCodeConflictError(f"业务码 {code.name!r} 未注册")
        return registered

    @classmethod
    def all(cls) -> Dict[str, BizCode]:
        return dict(cls._codes)

    @classmethod
    def reset_for_test(cls) -> None:
        cls._codes.clear()


class _BizCodeBookMeta(type):
    """码册元类：子类创建时收集 BizCode 属性 → 回填码名 → 注册。

    属性保留 BizCode 实例（而非替换成字符串），IDE 补全/类型推断/
    携带 status 与 message 三者兼得。
    """

    def __new__(mcs, clsname: str, bases: tuple, namespace: dict):
        book = super().__new__(mcs, clsname, bases, namespace)
        for attr, value in list(namespace.items()):
            if not isinstance(value, BizCode):
                continue
            if not _CODE_NAME_RE.match(attr):
                raise BizCodeConflictError(
                    f"业务码名 {attr!r} 不合法：需全大写、下划线分隔 "
                    f"（正则 {_CODE_NAME_RE.pattern}），位于码册 {clsname}"
                )
            value.name = attr
            BizCodeRegistry.register(value, book=clsname)
        return book


class BizCodes(metaclass=_BizCodeBookMeta):
    """业务码册基类。宿主继承它并逐行声明 ``NAME = BizCode(status, message)``。"""


# ---------- endpoint 业务码声明（OpenAPI） ----------

def raises(*codes: Any) -> Any:
    """声明本接口可能抛出的业务码，构建路由时汇入 OpenAPI responses。

    纯标记、零运行时行为；可叠加多次（基类方法已声明时子类补充也能聚合）。
    """
    normalized: List[BizCode] = []
    for c in codes:
        if isinstance(c, BizCode):
            normalized.append(c)
        elif isinstance(c, str):
            normalized.append(BizCodeRegistry.resolve(c))
        else:
            raise TypeError(f"raises() 只接受 BizCode 或码名字符串，得到 {c!r}")

    def deco(fn: Any) -> Any:
        existing: List[BizCode] = list(getattr(fn, "_sg_raises", []))
        fn._sg_raises = normalized + existing
        return fn

    return deco


def collect_raises(fn: Any) -> List[BizCode]:
    """读取 endpoint 上声明的业务码（去重、保持声明顺序）。"""
    out: List[BizCode] = []
    seen: set = set()
    for c in getattr(fn, "_sg_raises", []):
        if c.name not in seen:
            seen.add(c.name)
            out.append(c)
    return out


def group_by_status(codes: List[BizCode]) -> List[Tuple[int, List[BizCode]]]:
    """按 HTTP 大类分组，供 OpenAPI 聚合响应描述。"""
    groups: Dict[int, List[BizCode]] = {}
    for c in codes:
        groups.setdefault(c.status, []).append(c)
    return sorted(groups.items())


__all__ = [
    "BizCode", "BizCodes", "BizCodeRegistry", "BizCodeConflictError",
    "raises", "collect_raises", "group_by_status",
]

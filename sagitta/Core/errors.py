# sagitta/Core/errors.py
"""框架异常契约：协议异常（固定 HTTP 大类）与业务异常（业务码驱动）。

两层状态模型（详见 sagitta/Core/biz_code.py）：
  * 成功     → HTTP 200/201，body.code 恒为 0
  * 协议错误 → 下面的 NotFound/Unauthorized/... 类，code 为固定字符串，
               HTTP 大类由异常类型决定
  * 业务错误 → BizError(业务码)，HTTP 大类与文案均来自码册注册声明

硬规则（对应 README 第六节）：
  * **客户端错误 → 4xx**：参数校验失败、未认证、无权限、资源不存在。
  * **架构违规 / 未预期错误 → 500**：这是服务端编程错误，返回 200 会让
    监控、网关重试策略与前端错误处理全部失效。

body 统一为 `{"code": 0|str, "message": str, "data": any, "trace_id"?: str}`。
"""
from typing import Any, Optional, Union

from .biz_code import BizCode, BizCodeRegistry, BizCodes

# 成功业务码：全局唯一约定，前端判 code === 0 即成功。
SUCCESS_CODE: int = 0


class SgError(Exception):
    """所有框架异常的基类。

    code        : 业务/协议码，写入 body.code
    status_code : HTTP 状态码
    data        : 附加结构化信息（如违规字段路径）
    """

    code: Union[int, str] = "INTERNAL_ERROR"
    status_code: int = 500
    default_message: str = "内部错误"

    def __init__(self, message: Optional[str] = None, *, code: Optional[Any] = None,
                 status_code: Optional[int] = None, data: Any = None):
        self.message = message or self.default_message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.data = data
        super().__init__(self.message)

    def to_json_response(self, *, echo_trace: bool = False):
        """构造统一的 JSONResponse。

        中间件层（BaseHTTPMiddleware.dispatch）抛出的异常不会经过
        FastAPI 的 ExceptionMiddleware（它在中间件栈内层），因此中间件
        不能 raise SgError——必须直接返回 Response。此方法提供与
        bootstrap.py 异常处理器一致的响应壳构造逻辑。
        """
        from fastapi.responses import JSONResponse

        from ..Log.logger import get_trace

        body: dict = {"code": self.code, "message": self.message, "data": self.data}
        tid, _ = get_trace()
        if echo_trace and tid and tid != "-":
            body["trace_id"] = tid
        headers = {"X-Trace-Id": tid} if tid and tid != "-" else {}
        return JSONResponse(
            status_code=self.status_code,
            content=body,
            headers=headers,
        )


# ---------- 协议级异常：HTTP 大类由类型固定 ----------

class NotFoundError(SgError):
    code = "NOT_FOUND"
    status_code = 404
    default_message = "资源不存在"


class UnauthorizedError(SgError):
    code = "UNAUTHORIZED"
    status_code = 401
    default_message = "未认证"


class ForbiddenError(SgError):
    code = "FORBIDDEN"
    status_code = 403
    default_message = "权限不足"


class RateLimitError(SgError):
    code = "TOO_MANY_REQUESTS"
    status_code = 429
    default_message = "请求过于频繁"


class ArchViolationError(SgError):
    """架构违规：分层约定被破坏。属于编程错误，必须吵出来。"""

    code = "ARCH_VIOLATION"
    status_code = 500
    default_message = "架构违规"


class DOLeakError(ArchViolationError):
    """DO 对象流出 Service 层（README 硬规则 1）。

    `path` 给出泄漏对象的字段路径，便于直接定位。
    """

    default_message = "【架构违规】DO 对象流出 Service 层"

    def __init__(self, message: Optional[str] = None, *, path: str = "",
                 code: Optional[Any] = None, status_code: Optional[int] = None,
                 data: Any = None):
        self.path = path
        super().__init__(message, code=code, status_code=status_code,
                         data=data if data is not None else {"path": path})


class WrappedResponseError(ArchViolationError):
    """endpoint 手写了响应壳（README 硬规则 1 的另一半）。"""

    default_message = "【架构违规】endpoint 返回了疑似已包装的数据"


class StorageNotConfiguredError(SgError):
    """存储后端未配置（README 规则 3：故意设计的不友好）。"""

    code = "STORAGE_NOT_CONFIGURED"
    status_code = 500
    default_message = "文件存储后端未配置"


# ---------- 框架内置业务码 ----------
# 框架自身触发的业务级拒绝（而非协议错误）同样走统一注册机制：
# 宿主与框架的码在同一张表里，风格、文档、冲突检测全部一致。

class FrameworkCodes(BizCodes):
    PAGE_TOO_DEEP = BizCode(
        400, "分页过深：OFFSET 代价随页码线性增长，请改用游标分页或收窄查询条件"
    )


class BizError(SgError):
    """业务异常，由码册中声明的业务码驱动。

    用法::

        raise BizError(UserCodes.USERNAME_TAKEN)
        raise BizError(UserCodes.USERNAME_TAKEN, "本次文案覆盖默认")
        raise BizError(UserCodes.XXX, data={"field": "..."})

    HTTP 大类、body.code、默认文案三者在码册里一次性声明，
    业务点不允许再各写一套——这是码值不漂移的关键。
    """

    def __init__(self, ref: Any, message: Optional[str] = None, *, data: Any = None):
        bc = BizCodeRegistry.resolve(ref)
        self.biz: BizCode = bc
        super().__init__(message or bc.message, code=bc.name,
                         status_code=bc.status, data=data)


__all__ = [
    "SUCCESS_CODE", "SgError", "BizError", "NotFoundError", "UnauthorizedError",
    "ForbiddenError", "RateLimitError", "ArchViolationError",
    "DOLeakError", "WrappedResponseError", "StorageNotConfiguredError",
    "FrameworkCodes",
]

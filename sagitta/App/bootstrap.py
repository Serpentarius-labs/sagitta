# sagitta/App/bootstrap.py
"""应用装配的唯一入口。

设计原则（对应「作为 submodule 被下游引入」这一用途）：

  * **可组合**：CORS、中间件、生命周期钩子、路由挂载全部可注入。
    此前 CORS 白名单硬编码成 localhost:5173，宿主想改就只能弃用 bootstrap()
    自己重写一遍装配逻辑——那框架就失去意义了。
  * **单一生命周期**：统一走 `App/lifespan.py`，不再另写内联版本。
    这是此前所有 DB 接口 500（`KeyError: 'default'`）的根因。
  * **异常契约集中注册**：业务异常 / 架构违规 / 参数校验 / 未预期错误
    各有确定的 HTTP 语义，见 `Core/errors.py`。
"""
import logging
import traceback
from typing import Any, Callable, Dict, List, Optional, Sequence

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..Config.setting import SgSettings, get_settings
from ..Core.errors import SgError
from ..Log.logger import get_trace
from .lifespan import ShutdownHook, StartupHook, is_shutting_down, make_lifespan

logger = logging.getLogger("sagitta")

# 中间件按「添加顺序与执行顺序相反」的规则装配。
# 这里给出的是**执行顺序**（外层在前）：trace 最外层，才能覆盖全部耗时。
MiddlewareSpec = Callable[..., Any]


# Starlette HTTPException（非框架抛出）的状态码 → 协议码映射。
# 未命中的 4xx 归 BAD_REQUEST，5xx 归 INTERNAL_ERROR。
_HTTP_CODE_NAMES: Dict[int, str] = {
    400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN",
    404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE", 415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR", 429: "TOO_MANY_REQUESTS",
    503: "SERVICE_UNAVAILABLE", 504: "GATEWAY_TIMEOUT",
}

_cached_app = None # 单例


def _http_code_name(status: int) -> str:
    return _HTTP_CODE_NAMES.get(status) or (
        "BAD_REQUEST" if 400 <= status < 500 else "INTERNAL_ERROR"
    )


def _err_body(code: Any, message: str, data: Any = None,
                include_trace: bool = False) -> Dict[str, Any]:
    body: Dict[str, Any] = {"code": code, "message": message, "data": data}
    tid, _ = get_trace()
    if include_trace and tid and tid != "-":
        body["trace_id"] = tid
    return body


def _err_headers() -> Dict[str, str]:
    """错误响应也带 trace header，便于日志与前端串联。"""
    tid, _ = get_trace()
    return {"X-Trace-Id": tid} if tid and tid != "-" else {}


def register_exception_handlers(app: FastAPI, *, echo_trace_in_body: bool = False,
                                expose_internal: bool = False) -> None:
    """注册全部异常处理器。

    expose_internal : 是否在 500 响应里暴露异常类型与消息。
                      **仅开发环境开**；生产环境只记日志、对外统一文案，
                      避免把内部结构泄漏给客户端。
    """

    @app.exception_handler(SgError)
    async def _sg_error(request: Request, exc: SgError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("[%s] %s: %s", getattr(request.state, "trace_id", "-"),
                         type(exc).__name__, exc.message)
        else:
            logger.info("[%s] %s: %s", getattr(request.state, "trace_id", "-"),
                        type(exc).__name__, exc.message)
        msg = exc.message if (expose_internal or exc.status_code < 500) else "内部错误"
        return JSONResponse(
            status_code=exc.status_code,
            content=_err_body(exc.code, msg, exc.data, echo_trace_in_body),
            headers=_err_headers(),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        """参数校验失败 → **422**，不再被兜底 handler 吞成 500。

        这是客户端错误。吞成 500 会污染服务端错误率监控，也让前端
        无法区分「我传错了」和「服务端挂了」。
        """
        errors = []
        for e in exc.errors():
            loc = [str(x) for x in e.get("loc", ()) if x not in ("body", "query", "path")]
            errors.append({
                "field": ".".join(loc) or "(root)",
                "msg": e.get("msg", ""),
                "type": e.get("type", ""),
            })
        summary = "; ".join(f"{e['field']}: {e['msg']}" for e in errors[:5])
        logger.info("[%s] 参数校验失败: %s", getattr(request.state, "trace_id", "-"), summary)
        return JSONResponse(
            status_code=422,
            content=_err_body("VALIDATION_ERROR", f"参数校验失败: {summary}",
                              errors, echo_trace_in_body),
            headers=_err_headers(),
        )

    # 游标无法解析属于客户端传参错误（400），不该冒泡成 500。
    # CursorError 是 ValueError 子类，因此按具体类型注册而非 ValueError。
    from ..Database.repository import CursorError

    @app.exception_handler(CursorError)
    async def _cursor_error(request: Request, exc: CursorError) -> JSONResponse:
        logger.info("[%s] 游标解析失败: %s",
                    getattr(request.state, "trace_id", "-"), exc)
        return JSONResponse(
            status_code=400,
            content=_err_body("INVALID_CURSOR", str(exc), None, echo_trace_in_body),
            headers=_err_headers(),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """FastAPI/Starlette 的 HTTPException 也套统一壳。

        不处理的话它会返回 `{"detail": ...}`，与框架的
        `{code,message,data}` 并存，正是 README 要消灭的「三种响应壳」。
        """
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=_err_body(_http_code_name(exc.status_code), detail,
                              None, echo_trace_in_body),
            headers=dict(_err_headers(), **(exc.headers or {})),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        tid = getattr(request.state, "trace_id", "-")
        logger.error("[%s] 未处理异常 %s\n%s", tid, type(exc).__name__,
                     traceback.format_exc())
        msg = f"{type(exc).__name__}: {exc}" if expose_internal else "内部错误"
        return JSONResponse(
            status_code=500,
            content=_err_body("INTERNAL_ERROR", msg, None, echo_trace_in_body),
            headers=_err_headers(),
        )


def register_health(app: FastAPI, *, db_names: Optional[Sequence[str]] = None,
                    redis_names: Optional[Sequence[str]] = None,
                    prefix: str = "") -> None:
    """健康检查与就绪探针。

    /health : 存活探针，不碰依赖，永远 200（进程活着就行）。
    /ready  : 就绪探针，真实探测 DB 与 Redis，任一失败返回 503 让 K8s 摘流量。
    """
    from ..Cache.redis import RedisRegistry
    from ..Database.session import probe_session

    @app.get(f"{prefix}/health", include_in_schema=False, tags=["运维"])
    async def health() -> Dict[str, Any]:
        s = get_settings()
        # 只暴露非敏感的版本/环境元数据，绝不回显配置值
        return {
            "status": "ok",
            "env": s.env,
            "profiles": s.meta.profiles,
            "build_commit": s.build_commit,
        }

    @app.get(f"{prefix}/ready", include_in_schema=False, tags=["运维"])
    async def ready() -> JSONResponse:
        # 优雅关闭中：返回 503 让 K8s 立即摘流量
        if is_shutting_down():
            return JSONResponse(
                status_code=503,
                content={"status": "shutting_down", "probes": {}},
                headers=_err_headers(),
            )
        probes: Dict[str, str] = {}
        status = 200

        targets_db = list(db_names) if db_names else (
            list(get_settings().db) if get_settings().db else [])
        for name in targets_db:
            try:
                # 用 probe_session 而非手动驱动生成器，避免连接不归还
                await probe_session(name)
                probes[f"db.{name}"] = "ok"
            except Exception as e:
                probes[f"db.{name}"] = f"fail: {type(e).__name__}"
                status = 503

        targets_redis = list(redis_names) if redis_names else (
            list(get_settings().redis) if get_settings().redis else [])
        for name in targets_redis:
            try:
                r = RedisRegistry.get(name)
                await r.ping()
                probes[f"redis.{name}"] = "ok"
            except Exception as e:
                probes[f"redis.{name}"] = f"fail: {type(e).__name__}"
                status = 503

        return JSONResponse(
            status_code=status,
            content={"status": "ok" if status == 200 else "degraded",
                     "probes": probes},
            headers=_err_headers(),
        )


def bootstrap(
    *,
    title: Optional[str] = None,
    version: Optional[str] = None,
    settings: Optional[SgSettings] = None,
    middlewares: Optional[Sequence[Any]] = None,
    cors_origins: Optional[Sequence[str]] = None,
    on_startup: Optional[Sequence[StartupHook]] = None,
    on_shutdown: Optional[Sequence[ShutdownHook]] = None,
    mount_controllers: bool = True,
    controller_packages: Optional[Sequence[str]] = None,
    health_prefix: str = "",
    expose_internal_errors: Optional[bool] = None,
    **fastapi_kwargs: Any,
) -> FastAPI:
    """装配 FastAPI 应用。

    所有参数都可选，零配置可用；宿主项目按需覆盖。

    middlewares          : 额外中间件，形如 [(Cls, {kw}), ...] 或 [Cls, ...]。
                           按传入顺序添加，即**列表越靠前越内层**。
    cors_origins         : 覆盖配置里的 CORS 白名单。传 `[]` 表示不加 CORS。
    controller_packages  : 需要 import 才能登记的控制器模块。框架不会去猜宿主
                           的包名，宿主在此显式列出（或用 `mount_controllers=False`
                           自己控制）。
    expose_internal_errors : 500 是否暴露异常细节。默认按 env != prod 判定。
    """

    global _cached_app
    if _cached_app is not None:
        return _cached_app

    cfg = settings or get_settings()

    app_title = title or cfg.app.title
    app_version = version or cfg.app.version
    if expose_internal_errors is None:
        expose_internal_errors = cfg.app.env.lower() != "prod"

    app = FastAPI(
        title=app_title,
        version=app_version,
        debug=cfg.app.debug,
        lifespan=make_lifespan(on_startup=on_startup, on_shutdown=on_shutdown,
                               settings_provider=lambda: cfg),
        **fastapi_kwargs,
    )

    # ---- CORS ----
    origins = list(cors_origins) if cors_origins is not None else list(cfg.server.cors_origins)
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=cfg.server.cors_allow_credentials,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Trace-Id", "X-Request-Time"],
        )
    else:
        logger.info("[boot] CORS 未配置来源白名单，已跳过 CORSMiddleware")

    # ---- 宿主自定义中间件 ----
    for spec in (middlewares or []):
        if isinstance(spec, (tuple, list)):
            cls, kw = spec[0], (spec[1] if len(spec) > 1 else {})
            app.add_middleware(cls, **(kw or {}))
        else:
            app.add_middleware(spec)

    # ---- 请求体大小限制 ----
    if cfg.server.max_body_bytes > 0:
        from ..Core.body_limit import BodySizeLimitMiddleware
        app.add_middleware(BodySizeLimitMiddleware,
                           max_body_bytes=cfg.server.max_body_bytes)

    # ---- 全局限流 ----
    if cfg.rate_limit.enabled:
        from ..Security.rate_limit import RateLimitMiddleware
        app.add_middleware(RateLimitMiddleware,
                           enabled=cfg.rate_limit.enabled,
                           redis_client_name=cfg.rate_limit.redis,
                           default_requests=cfg.rate_limit.default_requests,
                           default_window=cfg.rate_limit.default_window,
                           ip_requests=cfg.rate_limit.ip_requests,
                           ip_window=cfg.rate_limit.ip_window,
                           user_requests=cfg.rate_limit.user_requests,
                           user_window=cfg.rate_limit.user_window,
                           skip_paths=cfg.rate_limit.skip_paths)

    # ---- Prometheus 指标采集 ----
    from ..Core.metrics import MetricsMiddleware, register_metrics
    app.add_middleware(MetricsMiddleware)
    register_metrics(app, prefix=health_prefix)

    # ---- Trace：最后添加 = 最外层执行，覆盖全部耗时 ----
    from ..Log.middleware import TraceMiddleware
    app.add_middleware(TraceMiddleware)

    register_exception_handlers(
        app,
        echo_trace_in_body=cfg.app.echo_trace_in_body,
        expose_internal=expose_internal_errors,
    )
    register_health(app, prefix=health_prefix)

    # ---- 控制器登记与挂载 ----
    if mount_controllers:
        from ..Flow.controller import mount_all
        from ..Flow.registry import report

        for pkg in (controller_packages or []):
            import_module_safe(pkg)

        mounted = mount_all(app)
        ok, missing = report()
        logger.info("[boot] 控制器挂载完成: %d 个（登记 %d，遗漏 %d）", mounted, ok, missing)

    _cached_app = app

    return app


def import_module_safe(module_path: str) -> bool:
    """import 一个模块，失败时吵出来但不中断启动。

    控制器只有被 import 才会登记。拼错模块名如果静默忽略，结果就是
    「少了几条路由但服务照常起」——这是最难排查的一类故障。
    """
    import importlib
    try:
        importlib.import_module(module_path)
        return True
    except Exception:
        logger.exception("[boot] 模块 %r 导入失败，其控制器不会登记", module_path)
        return False


__all__ = ["bootstrap", "register_exception_handlers", "register_health",
           "import_module_safe"]

# sagitta/App/lifespan.py
"""完整生命周期：Log → DB（含慢 SQL 监听）→ Redis → Qdrant。

这是**唯一**的 lifespan 实现。此前 bootstrap() 里另有一个只初始化 Redis 的
内联版本，导致 `EngineRegistry.init` 从未执行、所有带 DB 的接口 500
（`KeyError: 'default'`）。两套并存是重写未清理的残留，已收敛到本模块。

宿主项目如需在自己的启动/关闭逻辑里插一脚，用 `on_startup` / `on_shutdown`
回调，不要另写一个 lifespan —— 那会绕过基础设施初始化与优雅关闭。
"""
import asyncio
import logging
import signal
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, List, Optional, Sequence

from fastapi import FastAPI

from ..Cache.redis import RedisRegistry
from ..Config.setting import SgSettings, get_settings
from ..Database.engine import EngineRegistry

logger = logging.getLogger("sagittapi")

StartupHook = Callable[[FastAPI, SgSettings], Any]
ShutdownHook = Callable[[FastAPI, SgSettings], Any]

# 优雅关闭状态
_shutting_down = False


def is_shutting_down() -> bool:
    """健康检查端点用：关闭中返回 True，让 /ready 返回 503 摘流量。"""
    return _shutting_down


def _init_log(settings: SgSettings) -> None:
    """初始化日志后端。失败不阻塞启动，但必须吵出来。"""
    try:
        from ..Log.backend import LogBackend
        LogBackend.init(settings.log.backend, settings.log)
    except Exception:
        logging.getLogger("sagittapi").exception(
            "日志后端 %r 初始化失败，退回 stdlib 默认行为", settings.log.backend
        )


def _init_db(settings: SgSettings) -> None:
    """初始化全部数据源，并接上慢 SQL / 连接池监听。"""
    if not settings.db:
        return
    EngineRegistry.init(settings.db)


async def _init_redis(settings: SgSettings) -> None:
    if not settings.redis:
        return
    RedisRegistry.init(settings.redis)


def _init_qdrant(settings: SgSettings) -> None:
    """Qdrant 接线。默认关闭（enabled=false），开启才初始化并探活。

    README 第 266 行已说明：qdrant-client 是同步客户端，会阻塞事件循环。
    框架只负责初始化与探活，业务侧调用请用 anyio.to_thread.run_sync 包一层。
    """
    if not settings.qdrant.enabled:
        return
    try:
        from ..Cache.qdrant import QdrantRegistry
        QdrantRegistry.init(settings.qdrant)
    except Exception:
        logger.exception("Qdrant 初始化失败，向量检索功能不可用")


def _close_qdrant() -> None:
    try:
        from ..Cache.qdrant import QdrantRegistry
        QdrantRegistry.close_all()
    except Exception:
        logger.debug("Qdrant 未初始化或已关闭", exc_info=True)


async def _drain_inflight(timeout: float = 10.0) -> None:
    """等待 in-flight 请求完成。

    通过检查 ASGI 中间件层的活跃请求计数实现。如果框架无法获取
    精确计数，则退化为固定等待（给 K8s 摘流量时间）。
    """
    # 尝试从 MetricsCollector 获取 in-progress 计数
    try:
        from ..Core.metrics import MetricsCollector
        collector = MetricsCollector.instance()
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            # 检查所有 method 的 in-progress 总和
            total = sum(
                v for (method,), v in collector.http_requests_in_progress.values.items()
            )
            if total <= 0:
                logger.info("[shutdown] 所有 in-flight 请求已完成")
                return
            if asyncio.get_running_loop().time() > deadline:
                logger.warning(
                    "[shutdown] 等待 in-flight 请求超时（剩余 %d），强制关闭", total
                )
                return
            await asyncio.sleep(0.1)
    except Exception:
        # 无 MetricsCollector 时退化为固定等待
        logger.info("[shutdown] 固定等待 %.1fs 摘流量", min(timeout, 3.0))
        await asyncio.sleep(min(timeout, 3.0))


def make_lifespan(*, on_startup: Optional[Sequence[StartupHook]] = None,
                  on_shutdown: Optional[Sequence[ShutdownHook]] = None,
                  settings_provider: Optional[Callable[[], SgSettings]] = None):
    """构造 lifespan 上下文管理器。

    settings_provider : 取配置的回调，默认 get_settings()。测试可注入。
    """
    startup_hooks: List[StartupHook] = list(on_startup or [])
    shutdown_hooks: List[ShutdownHook] = list(on_shutdown or [])
    get_cfg = settings_provider or get_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = get_cfg()
        logger.info("[boot] env=%s debug=%s", settings.app.env, settings.app.debug)

        _init_log(settings)
        _init_db(settings)
        await _init_redis(settings)
        _init_qdrant(settings)

        # 探活：失败只告警不阻断启动。K8s 的 readiness 交给 /ready 端点，
        # 由它决定是否摘流量；启动即崩会让滚动更新直接失败。
        try:
            await EngineRegistry.probe_all()
        except Exception:
            logger.warning("[boot] 数据库探测失败，相关功能在恢复前不可用")

        for hook in startup_hooks:
            try:
                res = hook(app, settings)
                if hasattr(res, "__await__"):
                    await res
            except Exception:
                logger.exception("[boot] 启动钩子 %s 执行失败", getattr(hook, "__name__", hook))

        logger.info("[boot] 启动完成 routes=%d", len(app.routes))
        try:
            yield
        finally:
            # ---- 优雅关闭 ----
            global _shutting_down
            _shutting_down = True
            logger.info("[shutdown] 收到关闭信号，开始优雅关闭...")

            # 1) 等待 in-flight 请求完成（超时保护）
            await _drain_inflight(timeout=settings.app.shutdown_timeout
                                   if hasattr(settings.app, "shutdown_timeout") else 10.0)

            # 2) 执行宿主关闭钩子
            for hook in reversed(shutdown_hooks):
                try:
                    res = hook(app, settings)
                    if hasattr(res, "__await__"):
                        await res
                except Exception:
                    logger.exception("[shutdown] 关闭钩子 %s 执行失败",
                                     getattr(hook, "__name__", hook))
            _close_qdrant()
            try:
                await RedisRegistry.close_all()
            except Exception:
                logger.exception("[shutdown] Redis 关闭异常")
            try:
                await EngineRegistry.close_all()
            except Exception:
                logger.exception("[shutdown] DB 关闭异常")
            logger.info("[boot] 已释放所有连接池")

    return lifespan


# 向后兼容：`from sagittapi.App.lifespan import lifespan`
lifespan = make_lifespan()

__all__ = ["lifespan", "make_lifespan"]

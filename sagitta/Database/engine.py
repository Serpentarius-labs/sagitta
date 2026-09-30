# sagitta/Database/engine.py
"""引擎与会话工厂注册表：多数据源、连接池、慢 SQL 与连接池观测接线。

此前 `attach_slow_query_listener` / `attach_pool_queue_monitor` 全库零调用点，
README 承诺的「慢 SQL、连接池排队零配置出日志」并不成立。本模块在创建引擎时
无条件挂上这两个监听器（阈值 <=0 表示关闭该项）。
"""
import logging
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (AsyncEngine, async_sessionmaker,
                                    create_async_engine)

from .slow_query import attach_pool_queue_monitor, attach_slow_query_listener

logger = logging.getLogger("sagittapi")


def _cfg_get(cfg: Any, key: str, default: Any = None) -> Any:
    """同时支持 dict 配置与 DbConfig(SimpleNamespace) 配置。"""
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


class EngineRegistry:
    _engines: Dict[str, AsyncEngine] = {}
    _factories: Dict[str, async_sessionmaker] = {}

    @classmethod
    def init(cls, dbs: Any, *, replace: bool = False) -> None:
        """初始化数据源。

        接受三种形态：
          * `{"default": DbConfig(...)}`   —— SgSettings.db（推荐）
          * `{"default": {"url": ...}}`    —— 原始 dict
          * `"postgresql+asyncpg://..."`   —— 单库字符串简写
        """
        if isinstance(dbs, str):
            dbs = {"default": {"url": dbs}}
        elif isinstance(dbs, dict) and "url" in dbs:
            # 形如 {"url": ..., "pool_size": ...} 的单库扁平配置
            dbs = {"default": dbs}

        if not isinstance(dbs, dict):
            raise TypeError(f"db 配置必须是 dict 或 DSN 字符串，收到 {type(dbs).__name__}")

        for name, cfg in dbs.items():
            if name in cls._engines and not replace:
                continue

            url = str(_cfg_get(cfg, "url", "") or _cfg_get(cfg, "host", ""))
            if not url:
                logger.warning("[db] 数据源 %r 未提供 url，已跳过", name)
                continue

            pool_size = int(_cfg_get(cfg, "pool_size", 5) or 5)
            max_overflow = int(_cfg_get(cfg, "max_overflow", 5) or 5)
            pool_recycle = int(_cfg_get(cfg, "pool_recycle", 300) or 300)
            pool_pre_ping = bool(_cfg_get(cfg, "pool_pre_ping", True))
            echo = bool(_cfg_get(cfg, "echo", False))

            init_sql = _cfg_get(cfg, "init_sql", []) or []
            if isinstance(init_sql, str):
                init_sql = [init_sql]

            kwargs: Dict[str, Any] = {"echo": echo}
            # SQLite / aiosqlite 不接受连接池参数
            if not url.startswith("sqlite"):
                kwargs.update(pool_size=pool_size, max_overflow=max_overflow,
                              pool_recycle=pool_recycle, pool_pre_ping=pool_pre_ping)

            eng = create_async_engine(url, **kwargs)

            if init_sql:
                sqls: List[str] = [str(s) for s in init_sql]

                @event.listens_for(eng.sync_engine, "connect")
                def _on_connect(dbapi_conn, _rec, _sqls=sqls):
                    cur = dbapi_conn.cursor()
                    try:
                        for sql in _sqls:
                            # 注意：这里是 DBAPI 原生连接，必须传**字符串**。
                            # 原实现传 `text(sql)`（SQLAlchemy 表达式对象）会抛异常，
                            # 导致 init_sql 分支一旦被使用就崩。
                            cur.execute(sql)
                    finally:
                        cur.close()

            # 观测接线：README 承诺的「零配置出日志」在此生效
            slow_ms = float(_cfg_get(cfg, "slow_query_ms", 500.0) or 0)
            sample_rate = float(_cfg_get(cfg, "slow_query_sample_rate", 1.0) or 1.0)
            checkout_ms = float(_cfg_get(cfg, "pool_checkout_warn_ms", 1000.0) or 0)
            attach_slow_query_listener(eng, threshold_ms=slow_ms, sample_rate=sample_rate)
            attach_pool_queue_monitor(eng, checkout_warn_ms=checkout_ms)

            cls._engines[name] = eng
            cls._factories[name] = async_sessionmaker(
                eng, expire_on_commit=False, autoflush=False)
            logger.info("[db] 数据源 %r 已初始化 (%s, pool=%d+%d, slow>%.0fms)",
                        name, _cfg_get(cfg, "type", "?"), pool_size, max_overflow, slow_ms)

    @classmethod
    async def close_all(cls) -> None:
        for name, e in cls._engines.items():
            try:
                await e.dispose()
            except Exception:
                logger.exception("[db] 数据源 %r 释放失败", name)
        cls._engines.clear()
        cls._factories.clear()

    @classmethod
    def names(cls) -> List[str]:
        return list(cls._engines)

    @classmethod
    def get_engine(cls, name: str = "default") -> AsyncEngine:
        eng = cls._engines.get(name)
        if eng is None:
            raise KeyError(
                f"数据源 {name!r} 未初始化。已初始化: {list(cls._engines) or '(无)'}。"
                f"请确认 conf/config.yaml 的 db.{name} 已配置，且应用通过 "
                f"sagitta.App.lifespan 启动（bootstrap() 已默认接线）。"
            )
        return eng

    @classmethod
    def get_factory(cls, name: str = "default") -> async_sessionmaker:
        factory = cls._factories.get(name)
        if factory is None:
            raise KeyError(
                f"数据源 {name!r} 的会话工厂未初始化。已初始化: "
                f"{list(cls._factories) or '(无)'}。"
                f"请确认 conf/config.yaml 的 db.{name} 已配置，且应用通过 "
                f"sagitta.App.lifespan 启动（bootstrap() 已默认接线）。"
            )
        return factory

    @classmethod
    async def probe_all(cls, names: Optional[Iterable[str]] = None) -> None:
        targets = list(names) if names else list(cls._engines)
        for name in targets:
            eng = cls._engines[name]
            async with eng.connect() as conn:
                await conn.execute(text("SELECT 1"))


__all__ = ["EngineRegistry"]

# sagitta/Database/session.py
"""会话依赖与事务边界。

## 事务边界在此定死（README 硬规则）

`session_dep` 的语义是「**一个请求一个事务**」：

  * 请求正常结束 → 自动 `commit()`
  * 请求抛出异常 → 自动 `rollback()`

因此 **Service 层不要再写 `await db.commit()`**。业务代码只管改对象状态，
事务由框架负责。这样做的原因不是简洁，而是正确性：当 commit 散落在各个
业务方法里时，一个请求里跨两个 Service 的操作无法构成同一事务，中途失败
会留下半提交状态——这类 bug 在测试环境测不出来，只在生产并发下偶发。

需要「方法级事务」（比如 Service 内部循环、每轮独立提交）时，用
`session_scope()` 显式开一个嵌套作用域，而不是手动 commit。
"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from .engine import EngineRegistry


def session_dep(name: str = "default", *, autocommit: bool = True):
    """FastAPI 依赖：产出 AsyncSession，请求结束时按结果提交或回滚。

    autocommit=False 时只做 rollback 不做 commit，用于「只读接口」——
    避免为纯查询白付一次 commit 的往返开销。
    """

    async def _dep() -> AsyncGenerator[AsyncSession, None]:
        factory = EngineRegistry.get_factory(name)
        async with factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            else:
                if autocommit:
                    await session.commit()

    return _dep


@asynccontextmanager
async def session_scope(name: str = "default", *, autocommit: bool = True) -> AsyncGenerator[AsyncSession, None]:
    """方法级事务作用域。

    用于 Service 内部需要独立提交的场景（批处理每轮一提交、
    或一个请求里需要多个互不影响的事务）。退出时同样按结果提交或回滚。
    """
    factory = EngineRegistry.get_factory(name)
    async with factory() as session:
        try:
            yield session
            if autocommit:
                await session.commit()
        except Exception:
            await session.rollback()
            raise


async def probe_session(name: str = "default", *, timeout: Optional[float] = None) -> bool:
    """健康探测用的最小会话检查。

    刻意**不用** `async for s in session_dep(): ... break` 的写法。那种手动
    驱动生成器 + break 的方式不会同步执行 `async with` 的退出逻辑，连接要等
    GC 触发 aclose() 才归还。/ready 是 K8s 高频调用路径，这会真实耗尽连接池。
    """
    from sqlalchemy import text

    factory = EngineRegistry.get_factory(name)
    async with factory() as session:
        await session.execute(text("SELECT 1"))
    return True


# 只读依赖：不 commit，省一次往返
readonly_session_dep = session_dep(autocommit=False)


__all__ = ["session_dep", "session_scope", "probe_session",
           "readonly_session_dep"]

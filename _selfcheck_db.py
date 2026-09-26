"""端到端验证：守卫 / 注入 / 建表 / keyset 深分页。
连真实 PG 容器（127.0.0.1:15432），跑完即删表，不留痕。
"""
import asyncio
import os
import uuid
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

# 指向开发容器 PG（15432，避开本机默认 5432）
os.environ["SG_DB_DEFAULT_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:15432/sagitta"
os.environ["SG_APP_ENV"] = "test"
os.environ["SG_LOG_CONSOLE"] = "0"

from sqlalchemy import Column, Integer, String, select, func, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from sagitta.Database.base import Base, BaseDO, SoftDeleteDO, uuid7
from sagitta.Database.engine import EngineRegistry
from sagitta.Database.repository import BaseRepository, MAX_OFFSET
from sagitta.Database.session import session_scope
from sagitta.Config.setting import get_settings

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f" :: {detail}" if detail else ""))


# ---- demo 模型：用新 BaseDO / SoftDeleteDO ----
class WidgetDO(BaseDO):
    __tablename__ = "sg_widget"
    name: str = Column(String(64), nullable=False)
    score: int = Column(Integer, nullable=False, default=0)


class DocDO(SoftDeleteDO):
    __tablename__ = "sg_doc"
    title: str = Column(String(64), nullable=False)


class WidgetRepo(BaseRepository[WidgetDO]):
    model = WidgetDO


class DocRepo(BaseRepository[DocDO]):
    model = DocDO


async def main():
    settings = get_settings()
    print("== 配置 ==")
    check("env override 生效", settings.app.env == "test", settings.app.env)
    check("db url 指向测试库", "15432/sagitta" in settings.get_db("default").url)

    # ---- 建表 ----
    print("\n== uuid7 主键 + 命名约定建表 (真实 PG) ==")
    eng = create_async_engine(settings.get_db("default").url, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(lambda sc: Base.metadata.drop_all(sc, tables=[WidgetDO.__table__, DocDO.__table__]))
        await conn.run_sync(lambda sc: Base.metadata.create_all(sc, tables=[WidgetDO.__table__, DocDO.__table__]))

    # 验证 uuid7 有序性
    print("\n== uuid7 时间有序性 ==")
    ids = [uuid7() for _ in range(1000)]
    hexs = [i.hex for i in ids]
    check("uuid7 单调递增（字典序==时间序）", hexs == sorted(hexs))
    check("uuid7 是合法 UUID", isinstance(ids[0], uuid.UUID))

    # 验证命名约定生效（PK 有确定名字）
    pk_name = WidgetDO.__table__.primary_key.name
    check("主键有确定名字（命名约定生效）", pk_name == "pk_sg_widget", str(pk_name))

    # 验证 PG 列类型是原生 UUID
    from sqlalchemy.dialects import postgresql
    id_type_pg = WidgetDO.__table__.c.id.type.compile(dialect=postgresql.dialect())
    check("PG 主键为原生 UUID 类型", str(id_type_pg) == "UUID", str(id_type_pg))

    # ---- 写入 + keyset 分页 ----
    print("\n== keyset 游标分页（深分页 O(k)）==")
    factory = async_sessionmaker(eng, expire_on_commit=False, autoflush=False)
    N = 500
    async with factory() as s:
        repo = WidgetRepo(s)
        for i in range(N):
            await repo.add(WidgetDO(id=uuid7(), name=f"w{i}", score=i), flush=False)
        await s.commit()

    # 翻到很深的位置（模拟无限滚动第 25 页），验证代价恒定、能正确翻页
    async with factory() as s:
        repo = WidgetRepo(s)
        cursor = None
        seen = []
        pages = 0
        while True:
            kp = await repo.paginate_keyset(cursor=cursor, page_size=20, descending=False)
            seen.extend([w.name for w in kp.items])
            pages += 1
            if not kp.has_more:
                break
            cursor = kp.next_cursor
        check("keyset 翻完全部数据无遗漏", len(seen) == N, f"seen={len(seen)}")
        check("keyset 无重复", len(set(seen)) == N, f"unique={len(set(seen))}")
        check("keyset 顺序正确（旧->新）", seen == [f"w{i}" for i in range(N)])
        check("keyset 深翻页可达", pages == 25, f"pages={pages}")

    # ---- OFFSET 深分页闸门 ----
    print("\n== OFFSET 深分页硬闸门 ==")
    async with factory() as s:
        repo = WidgetRepo(s)
        try:
            await repo.paginate(page=1, page_size=200, max_offset=1000)
            # page=1 offset=0，不该触发
            check("浅页 OFFSET 正常", True)
        except Exception as e:
            check("浅页 OFFSET 正常", False, str(e)[:80])
        try:
            await repo.paginate(page=100, page_size=200, max_offset=1000)  # offset=19800
            check("超深 OFFSET 被拒绝", False, "未抛错")
        except Exception as e:
            check("超深 OFFSET 被拒绝（BizError）", "分页过深" in str(e), type(e).__name__)

    # ---- 软删除默认过滤 ----
    print("\n== 软删除默认过滤（Repository 层）==")
    async with factory() as s:
        drepo = DocRepo(s)
        d1 = await drepo.add(DocDO(id=uuid7(), title="alive"))
        d2 = DocDO(id=uuid7(), title="gone")
        await drepo.add(d2)
        await drepo.soft_delete_by_pk(d2.id)
        await s.commit()
    async with factory() as s:
        drepo = DocRepo(s)
        alive = await drepo.list_by()
        check("默认查询过滤已删除", len(alive) == 1 and alive[0].title == "alive", f"n={len(alive)}")
        allinc = await drepo.list_by(include_deleted=True)
        check("显式 include_deleted 可见全部", len(allinc) == 2, f"n={len(allinc)}")
        cnt = await drepo.count()
        check("count 也过滤已删除", cnt == 1, f"count={cnt}")
        restored = await drepo.restore(d2.id)
        check("可恢复软删除", restored is True)

    # ---- 事务边界：异常自动回滚 ----
    print("\n== 事务边界（session_scope 异常回滚）==")
    EngineRegistry.init({"default": settings.get_db("default")})
    try:
        async with session_scope() as s:
            s.add(WidgetDO(id=uuid7(), name="will_rollback", score=-1))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    async with factory() as s:
        n = (await s.execute(select(func.count()).select_from(WidgetDO).where(WidgetDO.name == "will_rollback"))).scalar()
        check("异常时自动回滚", n == 0, f"n={n}")

    # 清理
    async with eng.begin() as conn:
        await conn.run_sync(lambda sc: Base.metadata.drop_all(sc, tables=[WidgetDO.__table__, DocDO.__table__]))
    await eng.dispose()
    await EngineRegistry.close_all()

    print(f"\n==== 结果：{len(PASS)} PASS / {len(FAIL)} FAIL ====")
    if FAIL:
        print("失败项：", FAIL)
        raise SystemExit(1)


asyncio.run(main())

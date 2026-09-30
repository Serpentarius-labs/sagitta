# sagitta/Database/repository.py
"""Repository 基类：把「默认过滤软删除」和「深分页」在框架层定死。

## 为什么深分页不能靠 OFFSET

`OFFSET n LIMIT k` 的代价是 **O(n)**：数据库必须真实扫描并丢弃前 n 行。
翻到第 10 万页时，DB 要扫过 200 万行再扔掉，只返回 20 行。这不是索引问题，
是 OFFSET 的语义决定的，加索引救不了。表现是「列表页越往后越慢」，
且慢 SQL 日志里会看到大量耗时查询但 EXPLAIN 显示走了索引——很容易误判。

## 框架给的解法：keyset（游标）分页

利用 uuid7 主键「字典序 == 时间序」这一性质：

    WHERE id > :cursor ORDER BY id ASC LIMIT :page_size

代价是 **O(k)**，与翻到第几页无关，且稳定命中主键索引。

代价换来的约束必须讲清楚（README 会同步说明）：
  * **不能跳页**。只能「下一页」，不能直接跳到第 500 页。
  * 排序键必须**唯一且稳定**，否则游标会漏行或重复行。uuid7 主键天然满足；
    若按 `created_at` 排序，必须把 `id` 作为 tiebreaker 补进 ORDER BY。

因此框架两种分页都提供，并明确各自的适用场景：
  * `paginate()`        —— OFFSET 分页。用于需要跳页的后台管理页，
                           并对深页码设硬上限，避免被前端传值拖死。
  * `paginate_keyset()` —— 游标分页。用于 feed 流、无限滚动、大表遍历、
                           数据导出。这是默认推荐。
"""
from __future__ import annotations

import base64
import binascii
import json
import uuid as _uuid
from datetime import datetime
from typing import (Any, Callable, Dict, Generic, List, Optional, Sequence,
                    Tuple, Type, TypeVar, Union)

from sqlalchemy import Select, asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .base import Base, SoftDeleteMixin

M = TypeVar("M", bound=Base)

# OFFSET 分页的深页码硬上限。超过就拒绝，而不是让 DB 慢慢扫。
# 理由见模块 docstring：OFFSET 代价 O(n)，必须有个闸门。
MAX_OFFSET = 100_000


class CursorError(ValueError):
    """游标无法解析。属于客户端传参错误，应映射为 4xx。"""


def encode_cursor(value: Any) -> str:
    """把游标值编码成 URL 安全的短字符串。

    用 JSON + base64url 而不是直接 str()：这样能无歧义还原类型
    （uuid / datetime / int / str），复合游标（多排序键）也能表达。
    """
    payload = _serialize_cursor_value(value)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> Any:
    """解析游标。格式非法时抛 CursorError，绝不静默当成首页。"""
    if not cursor:
        raise CursorError("游标为空")
    pad = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + pad)
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise CursorError(f"游标无法解析: {cursor!r}") from e
    return _deserialize_cursor_value(payload)


def _serialize_cursor_value(value: Any) -> Any:
    if isinstance(value, _uuid.UUID):
        return {"t": "uuid", "v": value.hex}
    if isinstance(value, datetime):
        return {"t": "dt", "v": value.isoformat()}
    if isinstance(value, (list, tuple)):
        return {"t": "list", "v": [_serialize_cursor_value(x) for x in value]}
    if isinstance(value, (int, float, str, bool)) or value is None:
        return {"t": "raw", "v": value}
    raise CursorError(f"不支持的游标类型: {type(value).__name__}")


def _deserialize_cursor_value(payload: Any) -> Any:
    if not isinstance(payload, dict) or "t" not in payload:
        raise CursorError("游标结构非法")
    t, v = payload["t"], payload.get("v")
    if t == "uuid":
        try:
            return _uuid.UUID(hex=v)
        except (ValueError, AttributeError, TypeError) as e:
            raise CursorError(f"游标中的 UUID 非法: {v!r}") from e
    if t == "dt":
        try:
            return datetime.fromisoformat(v)
        except (ValueError, TypeError) as e:
            raise CursorError(f"游标中的时间非法: {v!r}") from e
    if t == "list":
        if not isinstance(v, list):
            raise CursorError("游标中的列表结构非法")
        return [_deserialize_cursor_value(x) for x in v]
    if t == "raw":
        return v
    raise CursorError(f"未知游标类型: {t!r}")


class KeysetPage(Generic[M]):
    """游标分页结果。

    刻意**不提供 total 与 page 字段**：keyset 分页的语义就是「不知道总量、
    不知道第几页」。硬塞一个 count(*) 会把 O(k) 的查询退化成 O(n)，
    正是这个分页方式要避免的东西。需要总数就单独调 `count()`。
    """

    __slots__ = ("items", "next_cursor", "has_more", "size")

    def __init__(self, items: List[M], next_cursor: Optional[str], has_more: bool):
        self.items = items
        self.next_cursor = next_cursor
        self.has_more = has_more
        self.size = len(items)

    def __repr__(self) -> str:
        return (f"KeysetPage(size={self.size}, has_more={self.has_more}, "
                f"next_cursor={self.next_cursor!r})")

    def to_dict(self, mapper: Optional[Callable[[Any], Any]] = None) -> Dict[str, Any]:
        """转成可序列化 dict。mapper 用于 DO → VO 转换。"""
        conv = mapper or (lambda x: x)
        return {
            "items": [conv(i) for i in self.items],
            "next_cursor": self.next_cursor,
            "has_more": self.has_more,
            "size": self.size,
        }


class BaseRepository(Generic[M]):
    """Repository 基类。

    子类只需给出 model：

        class UserRepository(BaseRepository[UserDO]):
            model = UserDO

    默认行为（业务侧不需要重复写，也不该重复写）：
      * **软删除自动过滤**：model 混入了 SoftDeleteMixin 时，所有查询默认
        附加 `deleted_at IS NULL`。要看已删除数据必须显式 `include_deleted=True`。
      * 查询入口统一返回 `Select`，业务侧仍可自由拼条件，不锁死表达力。
    """

    model: Type[M]

    def __init__(self, session: AsyncSession):
        self.session = session

    # ---- 软删除 ----

    @property
    def soft_delete(self) -> bool:
        return issubclass(self.model, SoftDeleteMixin)

    def base_query(self, *, include_deleted: bool = False) -> Select[Tuple[M]]:
        """基础查询：默认已过滤软删除。"""
        stmt = select(self.model)
        if self.soft_delete and not include_deleted:
            stmt = stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        return stmt

    # ---- 单条 / 多条 ----

    async def get(self, pk: Any, *, include_deleted: bool = False,
                  with_for_update: bool = False) -> Optional[M]:
        stmt = self.base_query(include_deleted=include_deleted).where(self.model.id == pk)
        if with_for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_or_raise(self, pk: Any, **kw: Any) -> M:
        from ..Core.errors import NotFoundError
        obj = await self.get(pk, **kw)
        if obj is None:
            raise NotFoundError(f"{self.model.__name__} {pk!r} 不存在")
        return obj

    async def list_by(self, include_deleted: bool = False,
                      order_by: Optional[Any] = None, limit: Optional[int] = None,
                      **conditions: Any) -> List[M]:
        stmt = self.base_query(include_deleted=include_deleted)
        for k, v in conditions.items():
            stmt = stmt.where(getattr(self.model, k) == v)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def count(self, *, include_deleted: bool = False, **conditions: Any) -> int:
        stmt = select(func.count()).select_from(self.model)
        if self.soft_delete and not include_deleted:
            stmt = stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        for k, v in conditions.items():
            stmt = stmt.where(getattr(self.model, k) == v)
        return int((await self.session.execute(stmt)).scalar() or 0)

    async def exists(self, **conditions: Any) -> bool:
        return (await self.count(**conditions)) > 0

    # ---- 写入 ----

    async def add(self, obj: M, *, flush: bool = True) -> M:
        self.session.add(obj)
        if flush:
            await self.session.flush()
        return obj

    async def bulk_add(self, objs: Sequence[M]) -> List[M]:
        self.session.add_all(list(objs))
        await self.session.flush()
        return list(objs)

    async def soft_delete_by_pk(self, pk: Any) -> bool:
        """软删除。model 未混入 SoftDeleteMixin 时抛错，不退化成物理删除。"""
        if not self.soft_delete:
            raise TypeError(
                f"{self.model.__name__} 未混入 SoftDeleteMixin，无法软删除。"
                f"请让它继承 SoftDeleteDO，或改用 delete_hard()。"
            )
        obj = await self.get(pk)
        if obj is None:
            return False
        obj.deleted_at = datetime.now(tz=None)  # type: ignore[attr-defined]
        await self.session.flush()
        return True

    async def delete_hard(self, pk: Any) -> bool:
        """物理删除。名字刻意带 hard，避免与软删除混淆。"""
        obj = await self.session.get(self.model, pk)
        if obj is None:
            return False
        await self.session.delete(obj)
        await self.session.flush()
        return True

    async def restore(self, pk: Any) -> bool:
        if not self.soft_delete:
            raise TypeError(f"{self.model.__name__} 未混入 SoftDeleteMixin")
        obj = await self.get(pk, include_deleted=True)
        if obj is None:
            return False
        obj.deleted_at = None  # type: ignore[attr-defined]
        await self.session.flush()
        return True

    # ---- 分页 ----

    async def paginate_keyset(
        self,
        *,
        cursor: Optional[str] = None,
        page_size: int = 20,
        order_by: Optional[Union[Any, Sequence[Any]]] = None,
        cursor_column: Optional[Any] = None,
        descending: bool = True,
        include_deleted: bool = False,
        stmt: Optional[Select] = None,
    ) -> KeysetPage[M]:
        """keyset（游标）分页。**深分页场景请用这个，不要用 paginate()。**

        代价 O(page_size)，与游标位置无关，稳定命中索引。

        order_by      : 业务排序键（如 created_at）。缺省用主键。
        cursor_column : 排序键对应的列，用于构造 WHERE。缺省取 order_by 里的列；
                        若 order_by 是表达式（如 func.xxx）则必须显式传。
        descending    : True 为「新的在前」（feed 流），False 为「旧的在前」。

        **排序键不唯一时必须补 tiebreaker**，否则游标会漏行或重复行。
        本方法自动把主键 `id` 追加为最后一个排序键来兜底；若业务排序键本身
        就是 id，则不会重复追加。
        """
        if page_size <= 0:
            page_size = 20
        page_size = min(page_size, 200)

        base = self.base_query(include_deleted=include_deleted) if stmt is None else stmt

        id_col = self.model.id
        sort_col = cursor_column
        if sort_col is None and order_by is not None:
            first = order_by[0] if isinstance(order_by, (list, tuple)) else order_by
            sort_col = getattr(first, "element", first)  # 剥掉 asc()/desc() 包装

        if sort_col is None:
            sort_col = id_col

        direction = desc if descending else asc
        order_terms = [direction(sort_col)]

        # tiebreaker：排序键非唯一时用 id 兜底，保证游标严格单调
        same_as_id = sort_col is id_col or getattr(sort_col, "key", None) == "id"
        if not same_as_id:
            order_terms.append(direction(id_col))

        if cursor:
            cursor_val = decode_cursor(cursor)
            if isinstance(cursor_val, list) and len(cursor_val) == 2 and not same_as_id:
                # 复合游标：(排序键值, id)
                sv, iv = cursor_val
                if descending:
                    cond = ((sort_col < sv) | ((sort_col == sv) & (id_col < iv)))
                else:
                    cond = ((sort_col > sv) | ((sort_col == sv) & (id_col > iv)))
            else:
                cv = cursor_val[0] if isinstance(cursor_val, list) else cursor_val
                cond = (id_col < cv) if descending else (id_col > cv)
            base = base.where(cond)

        base = base.order_by(*order_terms).limit(page_size + 1)
        rows = list((await self.session.execute(base)).scalars().all())

        has_more = len(rows) > page_size
        if has_more:
            rows = rows[:page_size]

        next_cursor: Optional[str] = None
        if has_more and rows:
            last = rows[-1]
            if not same_as_id and sort_col is not id_col:
                next_cursor = encode_cursor([getattr(last, sort_col.key), last.id])
            else:
                next_cursor = encode_cursor(last.id)

        return KeysetPage(rows, next_cursor, has_more)

    async def paginate(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        order_by: Optional[Union[Any, Sequence[Any]]] = None,
        include_deleted: bool = False,
        stmt: Optional[Select] = None,
        max_offset: int = MAX_OFFSET,
    ) -> Dict[str, Any]:
        """OFFSET 分页。**仅用于需要跳页的后台管理页。**

        代价 O(offset)：翻得越深越慢，这是 SQL 语义决定的，加索引救不了。
        因此设 `max_offset` 硬闸门——超过直接抛错，而不是让 DB 慢慢扫完再
        返回一个谁都不看的第 10 万页。

        需要遍历大表 / feed 流 / 导出时，请改用 `paginate_keyset()`。
        """
        if page < 1:
            page = 1
        if page_size <= 0:
            page_size = 20
        page_size = min(page_size, 200)
        offset = (page - 1) * page_size

        if offset > max_offset:
            from ..Core.errors import BizError, FrameworkCodes
            raise BizError(
                FrameworkCodes.PAGE_TOO_DEEP,
                f"分页过深：offset={offset} 超过上限 {max_offset}。"
                f"OFFSET 分页的代价随页码线性增长，请改用游标分页"
                f"（paginate_keyset）或收窄查询条件（如加时间范围）。",
                data={"offset": offset, "max_offset": max_offset},
            )

        base = self.base_query(include_deleted=include_deleted) if stmt is None else stmt

        count_stmt = select(func.count()).select_from(base.subquery())
        total = int((await self.session.execute(count_stmt)).scalar() or 0)

        query = base
        if order_by is not None:
            if isinstance(order_by, (list, tuple)):
                query = query.order_by(*order_by)
            else:
                query = query.order_by(order_by)
        else:
            query = query.order_by(desc(self.model.id))
        query = query.offset(offset).limit(page_size)

        rows = list((await self.session.execute(query)).scalars().all())
        return {
            "items": rows,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": (total + page_size - 1) // page_size if page_size else 0,
        }


__all__ = ["BaseRepository", "KeysetPage", "encode_cursor", "decode_cursor",
           "CursorError", "MAX_OFFSET"]

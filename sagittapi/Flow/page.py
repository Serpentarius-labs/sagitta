# sagitta/Flow/page.py
"""分页的入参/出参约定。

两种分页方式，适用场景不同（详见 `Database/repository.py` 模块 docstring）：

  * `PageQuery` + `Page`     —— OFFSET 分页，支持跳页，用于后台管理列表。
  * `CursorQuery` + `Page`   —— keyset 分页，不支持跳页但代价恒定，用于
                                feed 流 / 无限滚动 / 大表遍历。

## 关于深分页

`PageQuery` 对 `page_size` 与 `offset` **都**做了校验。此前的实现只在
`limit` 属性里 `min(page_size, 200)` 截断，而 `offset` 仍按未截断的
`page_size` 计算——前端传 `page_size=1000000` 时 offset 按一百万算、
limit 是 200，分页彻底错位。现在改在校验层直接拒绝非法值（返回 422），
因为「静默纠正用户的错误输入」比报错更难排查。

offset 上限由 `MAX_OFFSET` 把关：OFFSET 的代价是 O(offset)，翻到第
10 万页时 DB 要真实扫过 200 万行再丢弃。这个闸门挡住的是「把 DB 拖死」，
不是「不让用户翻页」——需要深遍历请改用 keyset。
"""
from typing import Callable, Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field, field_validator

from ..Database.repository import MAX_OFFSET, CursorError, decode_cursor, encode_cursor

T = TypeVar("T")

PAGE_SIZE_MAX = 200


class PageQuery(BaseModel):
    """OFFSET 分页入参。"""

    page: int = Field(1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(20, ge=1, le=PAGE_SIZE_MAX,
                           description=f"每页条数，上限 {PAGE_SIZE_MAX}")

    @field_validator("page")
    @classmethod
    def _check_depth(cls, v: int, info) -> int:
        # page_size 可能还没校验完，用字段默认值兜底估算 offset
        ps = (info.data or {}).get("page_size", 20) or 20
        if (v - 1) * ps > MAX_OFFSET:
            raise ValueError(
                f"分页过深：page={v} 对应 offset={(v - 1) * ps} 超过上限 "
                f"{MAX_OFFSET}。OFFSET 分页代价随页码线性增长，请改用游标分页"
                f"（CursorQuery）或收窄查询条件。"
            )
        return v

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


class CursorQuery(BaseModel):
    """keyset（游标）分页入参。

    客户端只需把上一次响应里的 `next_cursor` 原样回传。首页传空。
    """

    cursor: Optional[str] = Field(None, max_length=512,
                                  description="上一页返回的 next_cursor；首页留空")
    page_size: int = Field(20, ge=1, le=PAGE_SIZE_MAX,
                           description=f"每页条数，上限 {PAGE_SIZE_MAX}")
    descending: bool = Field(True, description="True 为新的在前（feed 流）")

    @property
    def cursor_value(self):
        """解析后的游标值。格式非法时抛 CursorError（映射为 4xx）。"""
        if not self.cursor:
            return None
        return decode_cursor(self.cursor)


class Page(BaseModel, Generic[T]):
    """OFFSET 分页出参。带 total / pages，因为这种分页语义上知道总量。"""

    page: int
    page_size: int
    total: int
    items: List[T]

    @property
    def pages(self) -> int:
        return (self.total + self.page_size - 1) // self.page_size if self.page_size else 0

    @classmethod
    def of(cls, items: List[T], page: int, page_size: int, total: int) -> "Page[T]":
        """构造分页结果。

        `total` 是必填的。此前这里有个 `total_fn` 参数，但 count 查询本身是
        async 的，调用方必须先 await 拿到结果再用 lambda 包起来传进来——
        那还不如直接传 `total`。冗余间接层只增加理解成本，已移除。
        """
        return cls(page=page, page_size=page_size, total=int(total or 0), items=list(items))


class CursorPage(BaseModel, Generic[T]):
    """keyset 分页出参。

    刻意**不含 total / page**：keyset 分页的语义就是「不知道总量、不知道第
    几页」。硬塞一个 `count(*)` 会把 O(page_size) 的查询退化成 O(n)，正是
    这种分页要避免的。需要总数就单独发一次 count 请求。
    """

    items: List[T]
    next_cursor: Optional[str] = None
    has_more: bool = False
    page_size: int = 20

    @classmethod
    def of(cls, items: List[T], next_cursor: Optional[str], has_more: bool,
           page_size: int = 20) -> "CursorPage[T]":
        return cls(items=list(items), next_cursor=next_cursor,
                   has_more=bool(has_more), page_size=page_size)


__all__ = ["PageQuery", "CursorQuery", "Page", "CursorPage",
           "PAGE_SIZE_MAX", "encode_cursor", "decode_cursor", "CursorError"]

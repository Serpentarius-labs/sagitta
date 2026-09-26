# app/controllers/user_controller.py
"""用户 Controller —— 路由层的**正确示范**。

约定（README 硬规则）：
  * Controller 只收 DTO、只做参数校验，业务逻辑全部在 Service。
  * **返回裸数据**（dict / VO / Page），框架自动包装成 {code,message,data}。
    手写响应壳会被守卫拦下报 500。
  * Service 通过类型注解自动注入，无需 `__init__`。
  * 路由挂载是自动的：继承 Controller 并写 prefix 即被登记，
    由 main.py 的 mount_all 挂载。
"""
import uuid
from typing import List, Optional

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from sagitta.Core.biz_code import raises
from sagitta.Database.session import session_dep
from sagitta.Flow.controller import Controller
from sagitta.Flow.page import CursorPage, CursorQuery, Page, PageQuery

from app.biz_code import UserCodes
from app.dto.user_dto import UserInsertDTO, UserUpdateDTO
from app.services.user_service import UserService
from app.vo.user_vo import UserVO

# 复用同一个会话依赖。注意 session_dep() 返回的是 Depends 可调用的生成器工厂，
# 每个数据源建一次即可，不必每个方法重复构造。
_db = Depends(session_dep())


class UserController(Controller):
    prefix = "/user"
    tags = ["用户"]

    svc: UserService  # 自动注入，无需 __init__

    @Controller.post("/", summary="创建用户", status_code=201)
    @raises(UserCodes.USERNAME_TAKEN)   # 进入 OpenAPI 409 响应码表
    async def create(self, dto: UserInsertDTO, db: AsyncSession = _db) -> UserVO:
        return await self.svc.create(db, dto)

    @Controller.get("/{user_id}", summary="查询单个用户")
    async def detail(self, user_id: uuid.UUID, db: AsyncSession = _db) -> UserVO:
        return await self.svc.get(db, user_id)

    @Controller.put("/{user_id}", summary="更新用户")
    async def update(self, user_id: uuid.UUID, dto: UserUpdateDTO,
                     db: AsyncSession = _db) -> UserVO:
        return await self.svc.update(db, user_id, dto)

    @Controller.delete("/{user_id}", summary="删除用户（软删除）")
    async def delete(self, user_id: uuid.UUID, db: AsyncSession = _db):
        await self.svc.delete(db, user_id)
        return {"deleted": True}

    @Controller.get("/page", summary="分页查询（OFFSET，可跳页）")
    async def page(self, q: PageQuery = Depends(), db: AsyncSession = _db) -> Page[UserVO]:
        return await self.svc.page(db, q)

    @Controller.get("/feed", summary="游标分页（keyset，抗深分页，用于无限滚动）")
    async def feed(self, q: CursorQuery = Depends(), db: AsyncSession = _db) -> CursorPage[UserVO]:
        return await self.svc.feed(db, q.cursor, q.page_size, q.descending)

    @Controller.get("/search", summary="关键字搜索")
    async def search(self, keyword: str = Query(..., min_length=1, max_length=64),
                     limit: int = Query(50, ge=1, le=200),
                     db: AsyncSession = _db) -> List[UserVO]:
        return await self.svc.search(db, keyword, limit)


__all__ = ["UserController"]

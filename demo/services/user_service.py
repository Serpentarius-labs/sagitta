# app/services/user_service.py
"""用户 Service —— 业务逻辑层的**正确示范**。

三条约定，业务代码请照此办理：

1. **不写 `await db.commit()`**。事务边界由 `session_dep` 定死：
   请求正常结束自动 commit，抛异常自动 rollback（见 Database/session.py）。
   Service 只管改对象状态。原来每个方法自己 commit 的写法，会让
   「一个请求里跨两个 Service 的操作」无法构成同一事务，中途失败留下
   半提交状态——这类 bug 测试环境测不出来，只在生产并发下偶发。
   Service 里用 `db.flush()` 让 ORM 分配主键/刷新状态，但不提交。

2. **SQL 与分页交给 Repository**。Service 里不出现 select/where。

3. **DO 不流出**。返回前一律转成 VO，否则框架的 DO 泄漏守卫会报错。
"""
from typing import List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from sagitta.Core.errors import NotFoundError
from sagitta.Core.inject import ServiceBase
from sagitta.Flow.page import CursorPage, Page, PageQuery
from sagitta.Security.hash import PasswordHasher

from demo.do.user_do import UserDO
from demo.do.user_repo import UserRepository
from demo.dto.user_dto import UserInsertDTO, UserUpdateDTO
from demo.vo.user_vo import UserVO


class UserService(ServiceBase):
    """无状态单例（README 硬规则 2）。

    请求级数据（db、当前用户）一律走方法参数，绝不写成 self.xxx。
    写成实例属性的话，第一个并发请求就会污染第二个。
    """

    @staticmethod
    def _to_vo(row: UserDO) -> UserVO:
        return UserVO.model_validate(row)

    async def create(self, db: AsyncSession, dto: UserInsertDTO) -> UserVO:
        repo = UserRepository(db)
        if await repo.get_by_username(dto.username):
            # 业务拒绝：码值（HTTP 大类 + 文案）集中声明在 app/biz_code.py，
            # 此处只引用，不重写文案/状态码。
            from sagitta.Core.errors import BizError
            from demo.biz_code import UserCodes
            raise BizError(UserCodes.USERNAME_TAKEN)
        row = UserDO(
            username=dto.username,
            password_hash=PasswordHasher.hash(dto.password),  # 入参即哈希，明文不保留
            nickname=dto.nickname or dto.username,
            email=dto.email,
        )
        await repo.add(row)          # 内部 flush，不 commit
        await db.refresh(row)        # 拿到 DB 生成的 created_at / uuid7 主键
        return self._to_vo(row)      # 事务由 session_dep 在请求结束时提交

    async def get(self, db: AsyncSession, user_id: UUID) -> UserVO:
        row = await UserRepository(db).get(user_id)
        if row is None:
            raise NotFoundError(f"用户 {user_id} 不存在")
        return self._to_vo(row)

    async def update(self, db: AsyncSession, user_id: UUID, dto: UserUpdateDTO) -> UserVO:
        repo = UserRepository(db)
        row = await repo.get(user_id)
        if row is None:
            raise NotFoundError(f"用户 {user_id} 不存在")
        # 只更新显式传了的字段（部分更新语义）
        data = dto.model_dump(exclude_unset=True)
        # 密码必须单独处理：明文写入 password_hash 列之外的字段名（password 列不存在），
        # 这里转成哈希后落到 password_hash。
        new_password = data.pop("password", None)
        if new_password is not None:
            row.password_hash = PasswordHasher.hash(new_password)
        for k, v in data.items():
            setattr(row, k, v)
        await db.flush()
        await db.refresh(row)
        return self._to_vo(row)

    async def delete(self, db: AsyncSession, user_id: UUID) -> None:
        """软删除。返回 None，controller 会包装成 {code,message,data:null}。"""
        ok = await UserRepository(db).soft_delete_by_pk(user_id)
        if not ok:
            raise NotFoundError(f"用户 {user_id} 不存在")

    async def page(self, db: AsyncSession, q: PageQuery) -> Page[UserVO]:
        """OFFSET 分页（可跳页，适合后台管理列表）。"""
        repo = UserRepository(db)
        result = await repo.paginate(page=q.page, page_size=q.page_size)
        return Page.of(
            [self._to_vo(r) for r in result["items"]],
            result["page"], result["page_size"], result["total"],
        )

    async def feed(self, db: AsyncSession, cursor: Optional[str], page_size: int,
                   descending: bool = True) -> CursorPage[UserVO]:
        """keyset 分页（抗深分页，适合 feed 流 / 无限滚动）。

        代价 O(page_size)，与游标位置无关。不支持跳页——这是刻意的，
        详见 Database/repository.py 模块 docstring。
        """
        repo = UserRepository(db)
        kp = await repo.paginate_keyset(cursor=cursor, page_size=page_size,
                                        descending=descending)
        return CursorPage.of(
            [self._to_vo(r) for r in kp.items], kp.next_cursor,
            kp.has_more, page_size=len(kp.items),
        )

    async def search(self, db: AsyncSession, keyword: str, limit: int = 50) -> List[UserVO]:
        rows = await UserRepository(db).search(keyword, limit=limit)
        return [self._to_vo(r) for r in rows]


__all__ = ["UserService"]

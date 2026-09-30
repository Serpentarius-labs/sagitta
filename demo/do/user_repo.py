# app/do/user_repo.py
"""UserRepository —— 数据访问层的**正确示范**。

README 第 41 行：DO / Repository 是「唯一允许出现 SQL 的地方」。
业务逻辑写在 Service，SQL 与分页策略收敛在 Repository。

继承 `BaseRepository` 即免费获得：
  * 软删除自动过滤（UserDO 混入了 SoftDeleteMixin）
  * get / list_by / count / exists / add / bulk_add
  * 两种分页：paginate（OFFSET，可跳页）/ paginate_keyset（游标，抗深分页）
  * soft_delete_by_pk / delete_hard / restore

只需要给出 model，其余不用写。需要特定查询时再加方法。
"""
from typing import List, Optional

from sqlalchemy import or_, select

from sagittapi.Database.repository import BaseRepository

from .user_do import UserDO


class UserRepository(BaseRepository[UserDO]):
    model = UserDO

    async def get_by_username(self, username: str, *, include_deleted: bool = False) -> Optional[UserDO]:
        """按登录名查单条。软删除过滤由 base_query 自动处理。"""
        stmt = self.base_query(include_deleted=include_deleted).where(
            UserDO.username == username
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def search(self, keyword: str, *, limit: int = 50) -> List[UserDO]:
        """关键字模糊搜索昵称/登录名。

        注意：这里用 `ilike` 是 PG 方言（MySQL 的 LIKE 默认不区分大小写，
        OceanBase MySQL 模式同理）。跨库项目要么统一用 `like` + 应用层小写化，
        要么按 settings.get_db().type 分支。demo 用 ilike 演示 PG 写法。
        """
        kw = f"%{keyword}%"
        stmt = self.base_query().where(
            or_(UserDO.username.ilike(kw), UserDO.nickname.ilike(kw))
        ).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())


__all__ = ["UserRepository"]

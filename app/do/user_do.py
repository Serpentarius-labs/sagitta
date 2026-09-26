# app/do/user_do.py
"""用户 DO —— 建表的**正确示范**。

三个刻意的设计选择，业务表请照此办理：

1. **继承 `SoftDeleteDO`**，而不是直接继承 `Base`。
   它一次性带来 uuid7 主键 + created_at/updated_at + deleted_at，
   业务侧不需要再手写这些字段。不需要软删除的表用 `BaseDO`。

2. **用 SQLAlchemy 2.0 风格**（`Mapped[...]` / `mapped_column`），
   不要用 1.x 的 `Column(Integer, ...)`。差别不只是写法好看：
   `Mapped[str]` 让类型检查器与 Agent 都能拿到准确的字段类型，
   而 `Column(...)` 在静态分析下是 `Any`。框架要给 Agent 用，
   类型可见性直接决定生成代码的正确率。

3. **表名不用 SQL 保留字**。
   原来的 `__tablename__ = "user"` 在 PostgreSQL 是保留字，DDL 会失败
   或被迫加引号，后续所有裸 SQL 都要跟着小心。这里用 `app_user`。
   常见保留字：user / order / group / table / select / desc。
"""
from typing import Optional

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from sagitta.Database.base import SoftDeleteDO


class UserDO(SoftDeleteDO):
    __tablename__ = "app_user"
    __table_args__ = {"comment": "用户表（demo）"}

    # 主键 id、created_at、updated_at、deleted_at 均由 SoftDeleteDO 提供。
    # id 是 uuid7（时间有序），跨方言：PG → 原生 UUID，MySQL/OceanBase → CHAR(32)。

    # ⚠️ 软删除 + 唯一约束的冲突（demo 未解决，真实项目必须选一种）：
    # username 上建普通唯一约束时，一条被软删除的用户仍物理存在、仍占着
    # 这个名字，之后无法再注册同名用户。三种解法：
    #   a) 部分唯一索引（PG 支持；MySQL 不支持，需用 b/c）
    #      Index("uq_app_user_username", "username", unique=True,
    #            postgresql_where=text("deleted_at IS NULL"))
    #   b) 唯一键包含 deleted_at，如 UniqueConstraint("username", "deleted_at")
    #   c) 软删除时改写 username，加 `_deleted_<uuid>` 后缀
    #
    # 下面刻意保留**普通唯一约束**，让这个坑在 demo 里可见、可讨论；
    # 不要把它当成推荐写法照抄。
    username: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True,
        comment="登录名（唯一，注意软删除冲突）",
    )
    # argon2id 哈希串约 97 字符（$argon2id$v=19$m=65536,t=3,p=4$...），
    # 留 128 长度。**只存哈希，永远不存明文**；列名带 _hash 后缀也是在
    # 代码审查时明确这一契约。
    password_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="argon2id 密码哈希",
    )
    nickname: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="昵称",
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", comment="active/disabled",
    )
    email: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # 扩展字段用 SQLAlchemy 通用 JSON：PG 映射为 JSON，MySQL 映射为 JSON。
    # 刻意**不用** `postgresql.JSONB`——那会让这张表无法在 MySQL 上建出来，
    # 与框架的双数据库支持相冲突。确实需要 JSONB 的索引能力时，
    # 请把方言类型收敛到一个共享 TypeDecorator，而不是散落在各个 DO 里。
    extra: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


__all__ = ["UserDO"]

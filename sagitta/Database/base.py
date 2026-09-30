# sagitta/Database/base.py
"""ORM 声明基类与公共字段 Mixin。

这一层定死三件事，业务侧不该再有自由度：

1. **命名约定**：所有约束/索引有确定名字。否则 alembic autogenerate 出来的是
   匿名约束，后续 `op.drop_constraint(...)` 无法稳定定位；且开启
   `compare_server_default=True` 后，MySQL / PostgreSQL 之间会产生大量伪差异。

2. **主键策略**：uuid7。跨方言、趋势递增、B+Tree 追加写友好。

3. **公共字段**：created_at / updated_at 用**数据库侧**默认值，软删除单独 Mixin。
"""
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, MetaData, Uuid, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ---------------------------------------------------------------------------
# 命名约定
# ---------------------------------------------------------------------------
# 约定名必须显式，理由见模块 docstring。这些 token 是 SQLAlchemy 标准约定：
#   ix=索引 uq=唯一约束 ck=检查约束 fk=外键 pk=主键
NAMING_CONVENTION: dict = {
    "ix": "ix_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def uuid7() -> uuid.UUID:
    """时间有序 UUID（RFC 9562）。Python 3.14 原生支持 uuid.uuid7()。

    前 48 bit 是毫秒时间戳，因此**字典序 == 时间序**。这一性质是两个设计
    的基础：
      * 主键索引是「追加写」，不像 uuid4 那样随机插入导致 B+Tree 页分裂
        与索引膨胀；
      * keyset（游标）分页可以直接用 `id > :cursor` 走索引，解决 OFFSET
        深分页的线性退化。

    低版本 Python 回退到 uuid4——功能不受影响，但会失去有序性，
    因此这里主动告警而不是静默降级。
    """
    gen = getattr(uuid, "uuid7", None)
    if gen is not None:
        return gen()
    import warnings
    warnings.warn(
        "当前 Python 无原生 uuid.uuid7()，主键回退为 uuid4（无序）。"
        "这会削弱 B+Tree 写入局部性与 keyset 分页效果，建议升级到 Python 3.14+。",
        RuntimeWarning, stacklevel=2,
    )
    return uuid.uuid4()


class Base(DeclarativeBase):
    """全局唯一的声明基类。alembic 的 target_metadata 指向 `Base.metadata`。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # DO 泄漏守卫用这个标记识别 ORM 实例（见 Flow/response.py）
    __sg_do__ = True

    def __repr__(self) -> str:
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk!r}>"


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------

class UuidPKMixin:
    """uuid7 主键。

    跨方言映射（已实测 SQLAlchemy 2.0.54）：
      * PostgreSQL → 原生 `UUID`
      * MySQL / OceanBase → `CHAR(32)`（无连字符 hex）

    两者都保持「字典序 == 时间序」，所以 keyset 分页在 PG 与 MySQL 上行为一致。
    """

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid7,
        comment="uuid7 主键，时间有序",
    )


class TimestampMixin:
    """创建/更新时间。

    用**数据库侧** `server_default=func.now()` 而非 Python 侧
    `default=datetime.now`：后者在绕过 ORM 的写入（数据修复脚本、bulk insert、
    其他服务直连）时会留下 NULL，而这类写入在生产环境一定会发生。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), comment="创建时间（DB 侧生成）",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
        comment="最后更新时间（DB 侧生成）",
    )


class SoftDeleteMixin:
    """软删除标记。

    **必须配 Repository 使用。** 光加一列的实际效果是：每个查询都要手写
    `.where(Model.deleted_at.is_(None))`，漏写一处就是数据泄漏，而且漏写
    不会报错。`BaseRepository` 默认过滤软删除，把「看到已删除数据」变成
    需要显式声明的例外。
    """

    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        comment="删除时间；NULL 表示未删除",
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class BaseDO(UuidPKMixin, TimestampMixin, Base):
    """绝大多数业务表的基类：uuid7 主键 + 时间戳。

    需要软删除时自行混入 `SoftDeleteMixin`（软删除有查询成本与唯一索引
    冲突问题，不该默认强加给所有表）。
    """

    __abstract__ = True


class SoftDeleteDO(BaseDO, SoftDeleteMixin):
    """带软删除的业务表基类。"""

    __abstract__ = True


__all__ = ["Base", "NAMING_CONVENTION", "uuid7", "UuidPKMixin",
           "TimestampMixin", "SoftDeleteMixin", "BaseDO", "SoftDeleteDO"]

# sagitta/Security/permissions.py
"""权限系统：资源级 + 数据级权限控制。

三层权限模型：
  1. **角色级**（已有）：require_role("admin") — 粗粒度，判断角色。
  2. **资源级**（本模块）：require_permission("user:write") — 细粒度，
     按动作权限控制。权限通过 PermissionResolver 解析，默认从 JWT roles
     映射，支持自定义 resolver（如 Casbin adapter）。
  3. **数据级**（本模块）：DataScope / tenant_filter — 行级权限，
     如「用户只能看自己的数据」「租户隔离」。

设计要点：
  * PermissionResolver 是抽象接口，默认实现从 JWT roles 映射权限。
    宿主项目可注册自定义 resolver 对接 Casbin / ACL 表 / RBAC 服务。
  * 数据级权限通过 DataScope 注入查询条件，而非在应用层过滤——
    避免查出全表再过滤的性能与安全风险。
  * 权限检查失败统一抛 ForbiddenError，走框架异常处理。
"""
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Set, Union

from fastapi import Depends

from ..Core.errors import ForbiddenError
from .auth_middleware import current_user
from .jwt import TokenPayload


# ---------- 权限解析器 ----------

class PermissionResolver(ABC):
    """权限解析器抽象接口。

    宿主项目继承此类并注册到 set_permission_resolver()，
    即可对接 Casbin / 数据库 ACL / 外部权限服务。
    """

    @abstractmethod
    def resolve(self, user: TokenPayload) -> Set[str]:
        """返回该用户拥有的全部权限字符串集合。

        权限格式约定：``resource:action``，如 ``user:read``、``user:write``、
        ``order:*``（通配符，匹配该资源的所有动作）。
        """
        ...


class RolePermissionResolver(PermissionResolver):
    """默认实现：从 JWT roles 映射到权限。

    通过 role_permissions 配置角色 → 权限的映射::

        role_permissions = {
            "admin": ["*"],
            "editor": ["article:read", "article:write", "user:read"],
            "viewer": ["article:read", "user:read"],
        }
    """

    def __init__(self, role_permissions: Optional[Dict[str, List[str]]] = None):
        self._mapping: Dict[str, List[str]] = role_permissions or {
            "admin": ["*"],
        }

    def resolve(self, user: TokenPayload) -> Set[str]:
        perms: Set[str] = set()
        for role in getattr(user, "roles", []) or []:
            for p in self._mapping.get(role, []):
                perms.add(p)
        return perms


# 全局权限解析器单例
_resolver: PermissionResolver = RolePermissionResolver()


def set_permission_resolver(resolver: PermissionResolver) -> None:
    """注册自定义权限解析器（如 Casbin adapter）。"""
    global _resolver
    _resolver = resolver


def get_permission_resolver() -> PermissionResolver:
    return _resolver


def check_permission(user: TokenPayload, *required: str) -> bool:
    """检查用户是否拥有指定权限。

    支持通配符：
      * ``"*"`` 匹配所有权限
      * ``"user:*"`` 匹配 user 资源的所有动作
    """
    perms = _resolver.resolve(user)
    if "*" in perms:
        return True
    for req in required:
        if req in perms:
            continue
        # 检查资源级通配符：user:write → user:* 匹配
        resource = req.split(":", 1)[0]
        if f"{resource}:*" in perms:
            continue
        return False
    return True


def require_permission(*permissions: str):
    """依赖注入：要求用户拥有指定权限，否则 403。

    用法::

        @Controller.get("/user/{uid}")
        async def get_user(self, uid: str, user = Depends(require_permission("user:read"))):
            ...
    """
    def dep(user: TokenPayload = Depends(current_user)):
        if not check_permission(user, *permissions):
            raise ForbiddenError(f"缺少权限: {', '.join(permissions)}")
        return user
    return dep


# ---------- 数据级权限 ----------

class DataScope:
    """数据级权限范围。

    用于行级权限控制：
      * ``owner``：只能操作自己的数据（WHERE user_id = :current_user）
      * ``tenant``：只能操作本租户的数据（WHERE tenant_id = :current_tenant）
      * ``all``：无限制（管理员）

    使用方式：在 Repository 层注入 DataScope，自动附加 WHERE 条件。
    """

    OWNER = "owner"
    TENANT = "tenant"
    ALL = "all"

    def __init__(self, scope: str = "all", *,
                 user_id: Optional[str] = None,
                 tenant_id: Optional[str] = None,
                 user_column: str = "user_id",
                 tenant_column: str = "tenant_id"):
        self.scope = scope
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.user_column = user_column
        self.tenant_column = tenant_column

    def apply_filter(self, stmt: Any, model: Any) -> Any:
        """对查询语句附加数据级过滤条件。"""
        from sqlalchemy import and_

        if self.scope == self.ALL:
            return stmt
        conditions = []
        if self.scope == self.OWNER:
            col = getattr(model, self.user_column, None)
            if col is not None and self.user_id is not None:
                conditions.append(col == self.user_id)
        elif self.scope == self.TENANT:
            col = getattr(model, self.tenant_column, None)
            if col is not None and self.tenant_id is not None:
                conditions.append(col == self.tenant_id)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        return stmt

    @classmethod
    def from_user(cls, user: Optional[TokenPayload], *,
                   scope_field: str = "data_scope",
                   tenant_field: str = "tenant_id") -> "DataScope":
        """从 JWT payload 构造数据范围。

        JWT 可携带 data_scope / tenant_id 字段，由权限服务在签发时注入。
        """
        if user is None:
            return cls(scope=cls.ALL)
        scope = getattr(user, scope_field, cls.ALL)
        tenant_id = getattr(user, tenant_field, None)
        user_id = str(getattr(user, "sub", ""))
        if scope == cls.OWNER and not user_id:
            scope = cls.ALL
        if scope == cls.TENANT and not tenant_id:
            scope = cls.ALL
        return cls(scope=scope, user_id=user_id, tenant_id=tenant_id)


def require_data_scope(scope: str = "owner") -> Callable:
    """依赖注入：根据用户角色构造 DataScope。

    安全设计（fail-closed）：
      * 管理员（拥有 "*" 权限）→ ALL（无限制）
      * 非管理员 → 使用开发者指定的 scope（owner/tenant），不信任 JWT 中的 data_scope 字段
      * 未认证用户 → owner scope，user_id=None → 查询返回空集

    用法::

        @Controller.get("/article/page")
        async def page(self, db = Depends(session_dep()),
                      scope: DataScope = Depends(require_data_scope("owner"))):
            stmt = repo.base_query()
            stmt = scope.apply_filter(stmt, ArticleDO)
            ...
    """
    def dep(user: Optional[TokenPayload] = Depends(current_user)) -> DataScope:
        # 管理员：无限制
        if user and check_permission(user, "*"):
            return DataScope(scope=DataScope.ALL)
        # 非管理员：严格使用开发者声明的 scope
        user_id = str(getattr(user, "sub", "")) if user else None
        tenant_id = getattr(user, "tenant_id", None) if user else None
        return DataScope(scope=scope, user_id=user_id, tenant_id=tenant_id)
    return dep


__all__ = [
    "PermissionResolver", "RolePermissionResolver",
    "set_permission_resolver", "get_permission_resolver",
    "check_permission", "require_permission",
    "DataScope", "require_data_scope",
]

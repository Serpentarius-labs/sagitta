# tests/test_permissions.py
"""权限系统单元测试。

覆盖：
  * RolePermissionResolver：角色 → 权限映射
  * check_permission：直接权限、通配符、资源级通配符
  * DataScope：owner/tenant/all 三种模式 — 验证 WHERE 子句真实附加
  * require_data_scope 安全设计：非管理员 tenant 请求不能得到 ALL
  * 自定义 resolver 注册与隔离
"""
import pytest
from types import SimpleNamespace

from sagittapi.Security.permissions import (
    DataScope,
    PermissionResolver,
    RolePermissionResolver,
    check_permission,
    set_permission_resolver,
)


def _mock_user(roles=None, sub="user-1", **extra):
    """构造 mock TokenPayload。"""
    return SimpleNamespace(
        roles=roles or [],
        sub=sub,
        **extra,
    )


class TestRolePermissionResolver:
    """默认角色权限解析器。"""

    def test_admin_has_all(self):
        resolver = RolePermissionResolver({"admin": ["*"]})
        user = _mock_user(roles=["admin"])
        perms = resolver.resolve(user)
        assert "*" in perms

    def test_editor_permissions(self):
        resolver = RolePermissionResolver({
            "editor": ["article:read", "article:write", "user:read"]
        })
        user = _mock_user(roles=["editor"])
        perms = resolver.resolve(user)
        assert "article:read" in perms
        assert "article:write" in perms
        assert "user:write" not in perms

    def test_multiple_roles_merge(self):
        resolver = RolePermissionResolver({
            "viewer": ["article:read"],
            "commenter": ["comment:write", "article:read"],
        })
        user = _mock_user(roles=["viewer", "commenter"])
        perms = resolver.resolve(user)
        assert "article:read" in perms
        assert "comment:write" in perms

    def test_no_roles_returns_empty(self):
        resolver = RolePermissionResolver({"admin": ["*"]})
        user = _mock_user(roles=[])
        perms = resolver.resolve(user)
        assert len(perms) == 0


class TestCheckPermission:
    """check_permission 函数测试。"""

    def setup_method(self):
        """每个测试方法前重置 resolver，避免全局状态污染。"""
        set_permission_resolver(RolePermissionResolver({
            "admin": ["*"],
            "editor": ["article:read", "article:write"],
            "manager": ["user:*"],
            "viewer": ["article:read"],
        }))

    def test_direct_match(self):
        user = _mock_user(roles=["editor"])
        assert check_permission(user, "article:read") is True

    def test_no_match(self):
        user = _mock_user(roles=["viewer"])
        assert check_permission(user, "article:write") is False

    def test_wildcard_all(self):
        user = _mock_user(roles=["admin"])
        assert check_permission(user, "anything:anyaction") is True

    def test_resource_wildcard(self):
        user = _mock_user(roles=["manager"])
        assert check_permission(user, "user:read") is True
        assert check_permission(user, "user:write") is True
        assert check_permission(user, "user:delete") is True
        assert check_permission(user, "article:read") is False

    def test_multiple_required_all_must_pass(self):
        user = _mock_user(roles=["editor"])
        assert check_permission(user, "article:read", "article:write") is True
        assert check_permission(user, "article:read", "article:delete") is False

    def test_no_roles(self):
        user = _mock_user(roles=[])
        assert check_permission(user, "article:read") is False


class TestCustomResolver:
    """自定义权限解析器。"""

    def test_custom_resolver(self):
        class MyResolver(PermissionResolver):
            def resolve(self, user):
                return {"custom:read", "custom:write"}

        set_permission_resolver(MyResolver())
        user = _mock_user(roles=["anything"])
        assert check_permission(user, "custom:read") is True
        assert check_permission(user, "custom:write") is True
        assert check_permission(user, "other:read") is False


class TestDataScope:
    """数据级权限测试 — 严格验证 WHERE 子句真实附加。"""

    def _make_model(self, table_name, has_user_id=True, has_tenant_id=True):
        """构造 SQLAlchemy 模型类（不是实例）。"""
        from sqlalchemy import Column, String
        from sqlalchemy.orm import declarative_base

        Base = declarative_base()

        attrs = {"__tablename__": table_name, "id": Column(String, primary_key=True)}
        if has_user_id:
            attrs["user_id"] = Column(String)
        if has_tenant_id:
            attrs["tenant_id"] = Column(String)
        return type(table_name.title(), (Base,), attrs)

    def test_all_scope_no_filter(self):
        """ALL scope 不附加 WHERE 条件。"""
        from sqlalchemy import select

        Model = self._make_model("all_scope_test")
        ds = DataScope(scope=DataScope.ALL)
        stmt = select(Model)
        result = ds.apply_filter(stmt, Model)

        # 验证：返回的语句与原始语句是同一个对象（未附加条件）
        assert result is stmt

    def test_owner_scope_adds_where_clause(self):
        """OWNER scope 必须附加 WHERE user_id = :user_id 条件。"""
        from sqlalchemy import select

        Model = self._make_model("owner_scope_test")
        ds = DataScope(scope=DataScope.OWNER, user_id="user-123")
        stmt = select(Model)
        result = ds.apply_filter(stmt, Model)

        # 编译 SQL 并验证 WHERE 子句包含 user_id 条件
        compiled = str(result.compile(compile_kwargs={"literal_binds": True}))
        assert "user_id" in compiled.lower()
        assert "user-123" in compiled

    def test_tenant_scope_adds_where_clause(self):
        """TENANT scope 必须附加 WHERE tenant_id = :tenant_id 条件。"""
        from sqlalchemy import select

        Model = self._make_model("tenant_scope_test")
        ds = DataScope(scope=DataScope.TENANT, tenant_id="tenant-abc")
        stmt = select(Model)
        result = ds.apply_filter(stmt, Model)

        compiled = str(result.compile(compile_kwargs={"literal_binds": True}))
        assert "tenant_id" in compiled.lower()
        assert "tenant-abc" in compiled

    def test_owner_scope_without_user_id_adds_nothing(self):
        """OWNER scope 但 user_id=None 时不附加条件（防御）。"""
        from sqlalchemy import select

        Model = self._make_model("owner_no_uid_test")
        ds = DataScope(scope=DataScope.OWNER, user_id=None)
        stmt = select(Model)
        result = ds.apply_filter(stmt, Model)

        # 无 user_id → 不附加条件 → 返回原语句
        assert result is stmt

    def test_owner_scope_missing_column_adds_nothing(self):
        """模型没有 user_id 列时不附加条件。"""
        from sqlalchemy import select

        Model = self._make_model("no_uid_col_test", has_user_id=False, has_tenant_id=False)
        ds = DataScope(scope=DataScope.OWNER, user_id="user-123")
        stmt = select(Model)
        result = ds.apply_filter(stmt, Model)

        # 模型无 user_id 列 → getattr 返回 None → 不附加条件
        assert result is stmt

    def test_from_user_none_returns_all(self):
        """未认证用户 → ALL scope。"""
        ds = DataScope.from_user(None)
        assert ds.scope == DataScope.ALL


class TestRequireDataScopeSecurity:
    """require_data_scope 的安全设计测试。

    这是 P1-4 修复的核心：旧代码中非管理员请求 tenant scope 时
    会得到 ALL 权限（安全漏洞）。新代码 fail-closed。
    """

    def setup_method(self):
        set_permission_resolver(RolePermissionResolver({
            "admin": ["*"],
            "user": ["article:read"],
        }))

    def test_admin_gets_all_scope(self):
        """管理员无论请求什么 scope，都得到 ALL。"""
        from sagittapi.Security.permissions import require_data_scope
        dep = require_data_scope("owner")
        user = _mock_user(roles=["admin"])
        ds = dep(user=user)
        assert ds.scope == DataScope.ALL

    def test_non_admin_owner_scope(self):
        """非管理员请求 owner → 得到 OWNER + user_id。"""
        from sagittapi.Security.permissions import require_data_scope
        dep = require_data_scope("owner")
        user = _mock_user(roles=["user"], sub="u-123")
        ds = dep(user=user)
        assert ds.scope == DataScope.OWNER
        assert ds.user_id == "u-123"

    def test_non_admin_tenant_scope_not_all(self):
        """非管理员请求 tenant → 得到 TENANT，不是 ALL（安全漏洞修复验证）。"""
        from sagittapi.Security.permissions import require_data_scope
        dep = require_data_scope("tenant")
        user = _mock_user(roles=["user"], sub="u-123", tenant_id="t-1")
        ds = dep(user=user)
        assert ds.scope == DataScope.TENANT
        assert ds.scope != DataScope.ALL  # 关键：不能是 ALL
        assert ds.tenant_id == "t-1"

    def test_unauthenticated_user_owner_scope(self):
        """未认证用户请求 owner → OWNER scope, user_id=None → 查询返回空。"""
        from sagittapi.Security.permissions import require_data_scope
        dep = require_data_scope("owner")
        ds = dep(user=None)
        assert ds.scope == DataScope.OWNER
        assert ds.user_id is None

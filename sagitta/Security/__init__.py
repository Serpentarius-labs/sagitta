# sagitta/Security/__init__.py
"""Security 子包：JWT 认证、权限系统、限流、哈希、签名、登录守卫。

导入示例::

    from sagitta.Security import JWT, require_data_scope, RateLimitMiddleware
"""
from .jwt import JWT, TokenError, TokenPayload
from .permissions import (
    PermissionResolver, RolePermissionResolver,
    set_permission_resolver, get_permission_resolver,
    check_permission, require_permission,
    DataScope, require_data_scope,
)
from .rate_limit import RateLimitMiddleware
from .hash import PasswordHasher, NeedsRehashError
from .login_guard import LoginGuard, LoginBlocked
from .vault import Vault
from .auth_middleware import (
    JwtAuthMiddleware, current_user, require_role,
    set_auth_cookies, clear_auth_cookies,
)
from .signer import Signer

__all__ = [
    # jwt
    "JWT", "TokenError", "TokenPayload",
    # permissions
    "PermissionResolver", "RolePermissionResolver",
    "set_permission_resolver", "get_permission_resolver",
    "check_permission", "require_permission",
    "DataScope", "require_data_scope",
    # rate_limit
    "RateLimitMiddleware",
    # hash
    "PasswordHasher", "NeedsRehashError",
    # login_guard
    "LoginGuard", "LoginBlocked",
    # vault
    "Vault",
    # auth_middleware
    "JwtAuthMiddleware", "current_user", "require_role",
    "set_auth_cookies", "clear_auth_cookies",
    # signer
    "Signer",
]

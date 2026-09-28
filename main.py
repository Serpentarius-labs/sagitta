# main.py
"""应用入口 —— 宿主项目该怎么装配框架的**正确示范**。

装配顺序很关键，注释里说明了为什么这么排。
"""
import uvicorn
from dotenv import load_dotenv

load_dotenv()  # 先加载 .env，Vault 与配置都依赖它

from sagitta.app.bootstrap import bootstrap
from sagitta.Config.setting import get_settings

# ⚠️ 安全：Vault.init() 必须在任何签发/校验令牌的代码之前。
# 未设置 SG_MASTER_KEY 时它会直接抛错并给出生成命令——这是刻意的，
# 静默用一个空密钥比启动失败危险得多。
from sagitta.Security.vault import Vault
Vault.init()

from sagitta.Security.auth_middleware import JwtAuthMiddleware

settings = get_settings()

# ---- 文件功能开关 ----
# 开启后必须保证存储容器在线（MinIO/S3）：setup 会在建桶失败时直接报错。
# 不用文件功能时，把这里改成 False（控制器与装配逻辑会一起关闭）。
FILE_ENABLED = True

if FILE_ENABLED:
    # 时序关键：必须在 bootstrap() **之前**装配——控制器挂载时就要解析
    # FileService，详见 demo/storage_setup.py 模块说明。
    from demo.storage_setup import setup_file_storage
    setup_file_storage(settings)

# 控制器只有被 import 才会登记。框架不猜宿主的包名（本框架会以 submodule
# 形式被下游引入），所以在这里显式列出需要挂载的控制器模块。
CONTROLLER_MODULES = [
    "demo.controllers.user_controller",
    "demo.controllers.auth_controller",
]
if FILE_ENABLED:
    CONTROLLER_MODULES.append("demo.controllers.file_controller")

# 文件下载共享的 aiobotocore client 要在 shutdown 时显式释放
# （aiohttp 连接池靠 GC 不可靠，热重载场景会泄漏）。
_shutdown_hooks = []
if FILE_ENABLED:
    from sagitta.File.response import close_shared_oss_clients
    _shutdown_hooks.append(lambda app, settings: close_shared_oss_clients())

app = bootstrap(
    settings=settings,
    on_shutdown=_shutdown_hooks,
    # 中间件按「添加顺序 = 由内到外」传入。
    # 这里 JWT 认证放在 bootstrap 内部 Trace 之内，因此：
    #   请求 → Trace(设置 trace_id) → JwtAuth → 路由
    # 认证失败时 trace_id 已存在，日志能关联到这次请求。
    # 反过来（在 bootstrap 之后再 add_middleware）会让认证跑到 Trace 外层，
    # 认证失败就没有 trace_id，排查时无从下手。
    middlewares=[
        (JwtAuthMiddleware, {
            "skip_prefixes": ["/health", "/ready", "/docs", "/redoc",
                              "/openapi", "/openapi.json", "/auth/login"],
            "secure": settings.security.cookie_secure,
            "same_site": settings.security.cookie_samesite,
            "domain": settings.security.cookie_domain,
        }),
    ],
    # bootstrap 会 import 这些模块并挂载全部已登记的控制器，
    # 不要再手动调 mount_all —— 重复调用虽然是安全的 noop，但会让人
    # 误以为挂载发生了两次。
    controller_packages=CONTROLLER_MODULES,
)


def print_all_routes(app):
    """递归打印 FastAPI 所有路由（包括 include_router 挂载的子路由）"""
    from fastapi.routing import APIRoute, Mount

    def _walk(routes, prefix=""):
        for route in routes:
            if isinstance(route, APIRoute):
                methods = ",".join(sorted(route.methods))
                print(f"  {methods:>10} {prefix}{route.path}")
            elif isinstance(route, Mount):
                sub_prefix = prefix + route.path
                print(f"  {'MOUNT':>10} {sub_prefix}/")
                if hasattr(route, "routes"):
                    _walk(route.routes, sub_prefix)

    print("\n=== 🛣️ 完整路由表（含子路由） ===")
    _walk(app.routes)
    print("===============================\n")


# 在 bootstrap 之后调用
print_all_routes(app)
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=False,
    )

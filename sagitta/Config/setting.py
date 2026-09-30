# sagitta/Config/setting.py
"""配置对象：把 ConfigLoader 产出的原始 dict 映射成带类型的属性视图。

设计约定：
  * **嵌套优先**：`settings.db["default"].url`、`settings.log.level`、
    `settings.file.default_strategy`。这是唯一推荐写法。
  * **扁平别名**：`settings.db_url` 等保留给 `default` 数据源，仅为兼容
    历史代码，新代码不要用。
  * 所有字段都有默认值，缺配置不崩；但**存储后端未配置时按 README 规则 3
    显式报错**，不静默退化。
"""
import os
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from .loader import ConfigLoader, DEFAULTS

# 数据库类型 → (异步驱动前缀, 同步驱动前缀)
# README 第 262 行承诺的「按 type 分支选择 asyncpg 或 asyncmy」在此实现。
# OceanBase CE 走 MySQL 协议，复用 mysql 分支。
_DB_DRIVERS = {
    "postgresql": ("postgresql+asyncpg", "postgresql+psycopg"),
    "postgres": ("postgresql+asyncpg", "postgresql+psycopg"),
    "pg": ("postgresql+asyncpg", "postgresql+psycopg"),
    "mysql": ("mysql+asyncmy", "mysql+pymysql"),
    "oceanbase": ("mysql+asyncmy", "mysql+pymysql"),
    "sqlite": ("sqlite+aiosqlite", "sqlite"),
}

_DEFAULT_PORTS = {"postgresql": 5432, "postgres": 5432, "pg": 5432,
                  "mysql": 3306, "oceanbase": 2881}


class ConfigError(RuntimeError):
    """配置缺失或非法。启动期必须吵出来，不要留到运行期 500。"""


def _typed_int(value: Any, field_path: str, *, default: int = 0) -> int:
    """把配置值安全转为 int。非法时抛 ConfigError 并给出字段路径。"""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise ConfigError(
            f"配置项 {field_path!r} 的值 {value!r} 不是合法整数: {e}。"
            f"请检查 conf/config.yaml 中该字段的类型。"
        ) from e


def _typed_float(value: Any, field_path: str, *, default: float = 0.0) -> float:
    """把配置值安全转为 float。非法时抛 ConfigError 并给出字段路径。"""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise ConfigError(
            f"配置项 {field_path!r} 的值 {value!r} 不是合法浮点数: {e}。"
        ) from e


def _typed_bool(value: Any, field_path: str, *, default: bool = False) -> bool:
    """把配置值安全转为 bool。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(value)


def _normalize_db_type(raw_type: str, url: str) -> str:
    """从显式 type 或 DSN scheme 推断数据库类型。"""
    t = (raw_type or "").strip().lower()
    if t:
        return "postgresql" if t in ("postgres", "pg") else t
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    for key, (async_dsn, _sync) in _DB_DRIVERS.items():
        if scheme == async_dsn or scheme.startswith(async_dsn.split("+", 1)[0] + "+"):
            return "postgresql" if key in ("postgres", "pg") else key
    if scheme.startswith("postgresql"):
        return "postgresql"
    if scheme.startswith("mysql"):
        return "mysql"
    if scheme.startswith("sqlite"):
        return "sqlite"
    return "postgresql"


def _rewrite_scheme(url: str, target_prefix: str) -> str:
    """把 DSN 的 scheme 换成目标驱动前缀，保留 user:pass@host/db 部分。"""
    if "://" not in url:
        return url
    _scheme, rest = url.split("://", 1)
    return f"{target_prefix}://{rest}"


class DbConfig(SimpleNamespace):
    """单个数据源配置。`url` 为异步 DSN，`url_sync` 供 Alembic 使用。"""

    @property
    def async_prefix(self) -> str:
        return _DB_DRIVERS.get(self.type, _DB_DRIVERS["postgresql"])[0]

    @property
    def sync_prefix(self) -> str:
        return _DB_DRIVERS.get(self.type, _DB_DRIVERS["postgresql"])[1]

    @property
    def url_sync(self) -> str:
        """Alembic 等同步场景使用的 DSN。"""
        return _rewrite_scheme(self.url, self.sync_prefix)

    def __repr__(self) -> str:  # 不泄漏密码
        safe = "***" if self.password else ""
        return (f"DbConfig(type={self.type!r}, host={self.host!r}, "
                f"database={self.database!r}, password={safe}, "
                f"pool_size={self.pool_size})")


def _build_db(name: str, raw: Dict[str, Any]) -> DbConfig:
    if not isinstance(raw, dict):
        # 允许 `db.default: "postgresql://..."` 简写
        raw = {"url": str(raw)}

    db_type = _normalize_db_type(str(raw.get("type", "")), str(raw.get("url", "")))
    url = str(raw.get("url", "") or "").strip()

    host = str(raw.get("host", "127.0.0.1"))
    user = str(raw.get("user", "postgres"))
    password = str(raw.get("password", "") or "")
    database = str(raw.get("database", "sagitta"))
    port = int(raw.get("port") or _DEFAULT_PORTS.get(db_type, 5432))

    if not url:
        async_prefix = _DB_DRIVERS.get(db_type, _DB_DRIVERS["postgresql"])[0]
        if db_type == "sqlite":
            url = f"sqlite+aiosqlite:///{database}"
        else:
            cred = f"{quote_plus(user)}:{quote_plus(password)}@" if user else ""
            url = f"{async_prefix}://{cred}{host}:{port}/{database}"
    else:
        # 用户给了完整 DSN 但没写驱动（如 postgresql://...），补上异步驱动
        if "://" in url:
            scheme = url.split("://", 1)[0].lower()
            if "+" not in scheme:
                async_prefix = _DB_DRIVERS.get(db_type, _DB_DRIVERS["postgresql"])[0]
                base = async_prefix.split("+", 1)[0]
                if scheme == base or scheme.startswith(base):
                    url = _rewrite_scheme(url, async_prefix)

    init_sql = raw.get("init_sql", []) or []
    if isinstance(init_sql, str):
        init_sql = [init_sql]

    return DbConfig(
        name=name,
        type=db_type,
        url=url,
        host=host, port=port, user=user, password=password, database=database,
        pool_size=int(raw.get("pool_size", 5)),
        max_overflow=int(raw.get("max_overflow", 5)),
        pool_recycle=int(raw.get("pool_recycle", 300)),
        pool_pre_ping=bool(raw.get("pool_pre_ping", True)),
        echo=bool(raw.get("echo", False)),
        init_sql=list(init_sql),
        slow_query_ms=float(raw.get("slow_query_ms", 500.0)),
        pool_checkout_warn_ms=float(raw.get("pool_checkout_warn_ms", 1000.0)),
        slow_query_sample_rate=float(raw.get("slow_query_sample_rate", 1.0)),
    )


def _normalize_redis(raw: Any) -> Dict[str, Dict[str, Any]]:
    """支持 dict 与 list 两种写法。"""
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, dict):
                url = str(v.get("url") or v.get("host") or "")
                out[k] = {"url": url, "max_connections": int(v.get("max_connections", 10)),
                          "decode_responses": bool(v.get("decode_responses", True))}
            else:
                out[k] = {"url": str(v), "max_connections": 10, "decode_responses": True}
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                name = str(item.get("name", "default"))
                out[name] = {"url": str(item["url"]),
                             "max_connections": int(item.get("max_connections", 10)),
                             "decode_responses": bool(item.get("decode_responses", True))}
    if "default" not in out and os.getenv("REDIS_URL"):
        out["default"] = {"url": os.environ["REDIS_URL"], "max_connections": 10,
                          "decode_responses": True}
    return out


class SgSettings:
    """配置视图。字段全部有默认值，缺配置不崩。"""

    def __init__(self, raw: Optional[Dict[str, Any]] = None):
        r = raw if isinstance(raw, dict) else {}
        self._raw: Dict[str, Any] = r

        # 装载元数据：init_settings() 会从 loader 回填
        self.meta = SimpleNamespace(profiles=[], sources=[], dotenv=[])

        # ---- app ----
        app = r.get("app") or {}
        self.app = SimpleNamespace(
            title=str(app.get("title", DEFAULTS["app"]["title"])),
            version=str(app.get("version", DEFAULTS["app"]["version"])),
            debug=bool(app.get("debug", False)),
            env=str(app.get("env", "dev")),
            echo_trace_in_body=bool(app.get("echo_trace_in_body", False)),
            check_do_leak=bool(app.get("check_do_leak", True)),
            wrap_response=bool(app.get("wrap_response", True)),
            build_commit=str(app.get("build_commit", "")),
            shutdown_timeout=_typed_float(app.get("shutdown_timeout", 10.0),
                                          "app.shutdown_timeout", default=10.0),
        )
        # 扁平别名（历史兼容）
        self.title = self.app.title
        self.version = self.app.version
        self.debug = self.app.debug
        self.env = self.app.env
        self.echo_trace_in_body = self.app.echo_trace_in_body
        self.check_do_leak = self.app.check_do_leak
        self.wrap_response = self.app.wrap_response
        self.build_commit = self.app.build_commit

        # ---- server ----
        srv = r.get("server") or {}
        origins = srv.get("cors_origins", DEFAULTS["server"]["cors_origins"])
        if isinstance(origins, str):
            origins = [o.strip() for o in origins.split(",") if o.strip()]
        self.server = SimpleNamespace(
            host=str(srv.get("host", "0.0.0.0")),
            port=_typed_int(srv.get("port", 8000), "server.port"),
            workers=_typed_int(srv.get("workers", 1), "server.workers"),
            cors_origins=list(origins),
            cors_allow_credentials=bool(srv.get("cors_allow_credentials", True)),
            max_body_bytes=_typed_int(srv.get("max_body_bytes", 0), "server.max_body_bytes"),
        )
        self.host = self.server.host
        self.port = self.server.port
        self.workers = self.server.workers

        # ---- db（多数据源）----
        db_raw = r.get("db") or {}
        if isinstance(db_raw, str):
            db_raw = {"default": {"url": db_raw}}
        self.db: Dict[str, DbConfig] = {}
        for name, cfg in db_raw.items():
            self.db[name] = _build_db(name, cfg if isinstance(cfg, dict) else {"url": str(cfg)})
        if "default" not in self.db and os.getenv("DATABASE_URL"):
            self.db["default"] = _build_db("default", {"url": os.environ["DATABASE_URL"]})

        _default_db = self.db.get("default")
        # 扁平别名（历史兼容 lifespan.py / 旧代码）
        self.db_url: str = _default_db.url if _default_db else ""
        self.db_type: str = _default_db.type if _default_db else ""
        self.db_pool_size: int = _default_db.pool_size if _default_db else 5
        self.db_echo: bool = _default_db.echo if _default_db else False

        # ---- redis ----
        self.redis: Dict[str, Dict[str, Any]] = _normalize_redis(r.get("redis") or {})

        # ---- qdrant ----
        q = r.get("qdrant") or {}
        self.qdrant = SimpleNamespace(
            enabled=bool(q.get("enabled", False)),
            host=str(q.get("host", "127.0.0.1")),
            port=int(q.get("port", 6333)),
            grpc_port=int(q.get("grpc_port", 6334)),
            https=bool(q.get("https", False)),
            api_key=str(q.get("api_key", "") or ""),
            prefix=str(q.get("prefix", "") or ""),
        )

        # ---- log ----
        lg = r.get("log") or {}
        level = str(lg.get("level", "INFO"))
        if self.app.debug and level.upper() == "INFO":
            level = "DEBUG"
        self.log = SimpleNamespace(
            backend=str(lg.get("backend", "stdlib")),
            level=level,
            format=str(lg.get("format", "text")),
            root_dir=str(lg.get("root_dir", "logs")),
            app_name=str(lg.get("app_name", "app")),
            console=bool(lg.get("console", True)),
            when=str(lg.get("when", "midnight")),
            interval=int(lg.get("interval", 1)),
            backup_count=int(lg.get("backup_count", 7)),
            max_bytes=int(lg.get("max_bytes", 0)),
            encoding=str(lg.get("encoding", "utf-8")),
        )

        # ---- file / s3 ----
        f = r.get("file") or {}
        self.file = SimpleNamespace(
            default_strategy=str(f.get("default_strategy", "auto")),
            provider=str(f.get("provider", "minio")),
            endpoint=str(f.get("endpoint", "") or ""),
            access_key=str(f.get("access_key", "") or ""),
            secret_key=str(f.get("secret_key", "") or ""),
            bucket=str(f.get("bucket", "") or ""),
            region=str(f.get("region", "us-east-1")),
            public_domain=str(f.get("public_domain", "") or ""),
            internal_prefix=str(f.get("internal_prefix", "/files")),
            root=str(f.get("root", "/data")),
            max_python_bytes=int(f.get("max_python_bytes", 100 * 1024 * 1024)),
            strict_backend=bool(f.get("strict_backend", True)),
        )
        # `s3` 是 `file` 的历史别名；保留它让旧代码不炸，同时使
        # hasattr(cfg, "default_strategy") 成立（修复策略分支不可达）。
        self.s3 = self.file
        self.s3_endpoint = self.file.endpoint
        self.s3_access_key = self.file.access_key
        self.s3_secret_key = self.file.secret_key
        self.s3_bucket = self.file.bucket
        self.s3_region = self.file.region
        self.s3_public_domain = self.file.public_domain

        # ---- security ----
        sec = r.get("security") or {}
        self.security = SimpleNamespace(
            jwt_ttl_access=int(sec.get("jwt_ttl_access", 900)),
            jwt_ttl_refresh=int(sec.get("jwt_ttl_refresh", 604800)),
            cookie_secure=bool(sec.get("cookie_secure", False)),
            cookie_samesite=str(sec.get("cookie_samesite", "strict")),
            cookie_domain=sec.get("cookie_domain"),
            login_max_attempts=int(sec.get("login_max_attempts", 5)),
            login_lock_seconds=int(sec.get("login_lock_seconds", 300)),
        )
        self.jwt_ttl_access = self.security.jwt_ttl_access
        self.jwt_ttl_refresh = self.security.jwt_ttl_refresh
        self.cookie_secure = self.security.cookie_secure
        self.cookie_samesite = self.security.cookie_samesite
        self.login_max_attempts = self.security.login_max_attempts
        self.login_lock_seconds = self.security.login_lock_seconds

        # ---- rate_limit ----
        rl = r.get("rate_limit") or {}
        self.rate_limit = SimpleNamespace(
            enabled=_typed_bool(rl.get("enabled", True), "rate_limit.enabled", default=True),
            redis=str(rl.get("redis", "default")),
            default_requests=_typed_int(rl.get("default_requests", 100),
                                        "rate_limit.default_requests", default=100),
            default_window=_typed_int(rl.get("default_window", 60),
                                      "rate_limit.default_window", default=60),
            ip_requests=_typed_int(rl.get("ip_requests", 60),
                                    "rate_limit.ip_requests", default=60),
            ip_window=_typed_int(rl.get("ip_window", 60),
                                  "rate_limit.ip_window", default=60),
            user_requests=_typed_int(rl.get("user_requests", 200),
                                     "rate_limit.user_requests", default=200),
            user_window=_typed_int(rl.get("user_window", 60),
                                    "rate_limit.user_window", default=60),
            skip_paths=list(rl.get("skip_paths", ["/health", "/ready", "/metrics",
                                                  "/docs", "/openapi.json"])),
        )

    # ---- 访问器 ----

    def get_db(self, name: str = "default") -> DbConfig:
        """取指定数据源。缺失时抛 ConfigError，不返回 None。"""
        cfg = self.db.get(name)
        if cfg is None:
            raise ConfigError(
                f"数据源 {name!r} 未配置。可用: {list(self.db) or '(无)'}。"
                f"请在 conf/config.yaml 的 db.{name} 下配置，"
                f"或用环境变量 SG_DB_{name.upper()}_URL 注入。"
            )
        return cfg

    def has_db(self, name: str = "default") -> bool:
        return name in self.db

    def get_redis(self, name: str = "default") -> Dict[str, Any]:
        cfg = self.redis.get(name)
        if cfg is None:
            raise ConfigError(f"Redis 实例 {name!r} 未配置。可用: {list(self.redis) or '(无)'}")
        return cfg

    def require_file_backend(self) -> SimpleNamespace:
        """README 规则 3：存储后端未显式配置时报错，而不是静默退化。"""
        f = self.file
        if f.endpoint and f.bucket and f.access_key and f.secret_key:
            return f
        if not f.strict_backend:
            return f
        raise ConfigError(
            "文件存储后端未配置完整（需要 endpoint / bucket / access_key / secret_key）。"
            "这是故意设计的不友好：请在 conf/config.yaml 的 file 段配好，"
            "或用 SG_FILE_ENDPOINT / SG_FILE_BUCKET / SG_FILE_ACCESS_KEY / "
            "SG_FILE_SECRET_KEY 注入。排查期可临时设 file.strict_backend=false。"
        )

    def as_dict(self) -> Dict[str, Any]:
        return dict(self._raw)

    def __repr__(self) -> str:  # 不泄漏密码
        return (f"SgSettings(title={self.title!r}, env={self.env!r}, "
                f"db={list(self.db)}, redis={list(self.redis)}, "
                f"file_configured={bool(self.file.endpoint and self.file.bucket)}, "
                f"debug={self.debug})")


# ---------- 全局单例 ----------

_SETTINGS: Optional[SgSettings] = None


def build_settings(raw: Optional[Dict[str, Any]] = None) -> SgSettings:
    """从原始 dict 构建配置对象（不写入全局单例）。

    Alembic / 测试用这个：拿到对象但不污染全局状态。
    """
    if raw is None:
        raw = ConfigLoader.instance().reload()
    return SgSettings(raw)


def set_settings(settings: SgSettings) -> SgSettings:
    """写入全局单例。测试与 Alembic 入口用。"""
    global _SETTINGS
    _SETTINGS = settings
    return settings


def init_settings(config_path: Optional[str] = None) -> SgSettings:
    """显式初始化：指定 yaml 路径；不传则自动向上探测 conf/config.yaml。"""
    loader = ConfigLoader.instance(config_path)
    settings = build_settings(loader.reload(config_path))
    settings.meta = SimpleNamespace(
        profiles=loader.profiles,
        sources=[str(p) for p in loader.loaded_files],
        dotenv=[str(p) for p in loader.dotenv_files],
    )
    return set_settings(settings)


def get_settings() -> SgSettings:
    """懒加载：首次调用自动初始化，永远不会 AssertionError。"""
    global _SETTINGS
    if _SETTINGS is None:
        init_settings()
    return _SETTINGS  # type: ignore[return-value]


def reload_settings(config_path: Optional[str] = None) -> SgSettings:
    """热重载配置（开发期用）。"""
    return init_settings(config_path)


__all__ = ["SgSettings", "DbConfig", "ConfigError", "build_settings",
           "set_settings", "init_settings", "get_settings", "reload_settings"]

# sagitta/Config/loader.py
"""配置装载：yaml 文件 + SG_ 环境变量覆盖 + 代码默认值。

优先级（后者覆盖前者）：
    代码默认值  <  conf/config.yaml  <  SG_ 开头的环境变量

README 承诺的两件事在这里实现：
  1. `SG_DB_DEFAULT_HOST=127.0.0.1` → 嵌套结构 `db.default.host`
  2. `SG_LOG_BACKEND=stdlib`        → 扁平写法，命中已存在的叶子路径

环境变量按下划线展开时存在天然歧义（`SG_DB_DEFAULT_HOST` 既可能是
`db.default.host` 也可能是 `db_default.host`），因此采用「已知路径优先 +
最长前缀匹配」策略：先用默认配置与 yaml 合并出已知路径集合，再按该集合
把环境变量名切分。切不出来时退化为「首段为节名、其余为叶子名」，保证
新增配置项无需改代码即可通过环境变量注入。
"""
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

ENV_PREFIX = "SG_"

# Profile 选择器：自由字符串（逗号分隔形成继承链），不内置任何环境名。
# 例：SG_PROFILE=prod；SG_PROFILE=staging,canary（后者覆盖前者）。
PROFILE_ENV = "SG_PROFILE"

# 个人覆盖文件：仅本机生效，必须 gitignore。
LOCAL_FILENAME = "config.local.yaml"

# 代码默认值：所有字段都有值，缺配置不崩。
# 这份 dict 同时充当「环境变量路径解析」的已知路径来源。
DEFAULTS: Dict[str, Any] = {
    "app": {
        "title": "Sagitta API",
        "version": "0.1.0",
        "debug": False,
        "env": "dev",
        "echo_trace_in_body": False,
        # DO 泄漏运行时守卫。README 硬规则 1，全环境保持 true。
        "check_do_leak": True,
        # 响应壳自动包装。关掉仅用于排查问题。
        "wrap_response": True,
        # 构建时注入的 git commit（CI build-arg / SG_BUILD_COMMIT），
        # 用于 /health 声明「线上跑的到底是哪一版」。
        "build_commit": "",
    },
    "server": {
        "host": "0.0.0.0",
        "port": 8000,
        "workers": 1,
        # bootstrap() 装配 CORS 用；宿主项目通过配置或环境变量覆盖，
        # 不再硬编码 localhost:5173。
        "cors_origins": ["http://localhost:5173", "http://127.0.0.1:5173"],
        "cors_allow_credentials": True,
    },
    # 多数据源：db.<name>.{type,url,...}。README 第 127 行的约定。
    "db": {
        "default": {
            "type": "postgresql",          # postgresql / mysql
            "url": "",                     # 完整 DSN，优先于分片字段
            "host": "127.0.0.1",
            "port": 5432,
            "user": "postgres",
            "password": "",
            "database": "sagitta",
            "pool_size": 5,
            "max_overflow": 5,
            "pool_recycle": 300,
            "pool_pre_ping": True,
            "echo": False,
            "init_sql": [],
            # 慢 SQL / 连接池观测阈值（ms）。<=0 表示关闭。
            "slow_query_ms": 500.0,
            "pool_checkout_warn_ms": 1000.0,
            "slow_query_sample_rate": 1.0,
        }
    },
    "redis": {
        "default": {"url": "redis://127.0.0.1:6379/0", "max_connections": 10},
    },
    "qdrant": {
        "enabled": False,
        "host": "127.0.0.1",
        "port": 6333,
        "grpc_port": 6334,
        "https": False,
        "api_key": "",
        "prefix": "",
    },
    "log": {
        "backend": "stdlib",               # stdlib / loguru
        "level": "INFO",
        "format": "text",                  # text / json
        "root_dir": "logs",
        "app_name": "app",
        "console": True,
        "when": "midnight",
        "interval": 1,
        "backup_count": 7,
        "max_bytes": 0,                    # >0 走 RotatingFileHandler
        "encoding": "utf-8",
    },
    "file": {
        # auto / oss / nginx / python。README 7.4 的三套链路。
        "default_strategy": "auto",
        "provider": "minio",               # minio / s3 / aliyun
        "endpoint": "",
        "access_key": "",
        "secret_key": "",
        "bucket": "",
        "region": "us-east-1",
        "public_domain": "",
        # nginx X-Accel-Redirect 用
        "internal_prefix": "/files",
        "root": "/data",
        # python 兜底策略的体积上限，超过主动 413，不静默 OOM。
        "max_python_bytes": 100 * 1024 * 1024,
        # 未配置存储时是否直接 500 + 明确文案（README 规则 3 的「故意不友好」）
        "strict_backend": True,
    },
    "security": {
        "jwt_ttl_access": 900,
        "jwt_ttl_refresh": 604800,
        "cookie_secure": False,            # prod 必须 true
        "cookie_samesite": "strict",
        "cookie_domain": None,
        "login_max_attempts": 5,
        "login_lock_seconds": 300,
    },
    "rate_limit": {
        "enabled": True,
        "redis": "default",
        "default_requests": 100,
        "default_window": 60,
        "ip_requests": 60,
        "ip_window": 60,
        "user_requests": 200,
        "user_window": 60,
        "skip_paths": ["/health", "/ready", "/metrics", "/docs", "/openapi.json"],
    },
}

# 兼容旧扁平写法：`database:` 节 → `db.default`
_LEGACY_FLAT_DB = ("url", "pool_size", "echo", "type", "host", "port",
                   "user", "password", "database", "max_overflow",
                   "pool_recycle", "pool_pre_ping", "init_sql")


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并；override 的 dict 与 base 的 dict 深合并，其余类型直接替换。"""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _walk_leaf_paths(node: Any, prefix: Tuple[str, ...] = ()) -> Iterator[Tuple[str, ...]]:
    """只产出叶子路径（值不是 dict 的节点），用于环境变量名解析。

    中间节点不参与匹配，避免 `SG_DB_DEFAULT=xxx` 把整个数据源配置
    dict 覆盖成字符串。
    """
    if isinstance(node, dict):
        if not node and prefix:
            yield prefix
        for k, v in node.items():
            yield from _walk_leaf_paths(v, prefix + (str(k),))
    elif prefix:
        yield prefix


def _coerce(value: str, sample: Any) -> Any:
    """按默认值的类型把环境变量字符串转成对应类型。"""
    if isinstance(sample, bool):
        return value.strip().lower() not in ("0", "false", "no", "off", "")
    if isinstance(sample, int) and not isinstance(sample, bool):
        try:
            return int(value)
        except ValueError:
            return value
    if isinstance(sample, float):
        try:
            return float(value)
        except ValueError:
            return value
    if isinstance(sample, list):
        # 逗号分隔 → 列表
        return [p.strip() for p in value.split(",") if p.strip()]
    return value


def _set_path(root: Dict[str, Any], path: Tuple[str, ...], value: Any) -> None:
    cur = root
    for seg in path[:-1]:
        nxt = cur.get(seg)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[seg] = nxt
        cur = nxt
    cur[path[-1]] = value


def _get_path(root: Dict[str, Any], path: Tuple[str, ...]) -> Any:
    cur: Any = root
    for seg in path:
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def _resolve_env_path(segments: List[str], known_paths: Set[Tuple[str, ...]]) -> Tuple[str, ...]:
    """把 SG_A_B_C 解析成配置路径。三级策略：

    1. **全路径精确匹配**：`SG_DB_DEFAULT_HOST` → `db.default.host`
    2. **唯一后缀匹配**（README 的「扁平写法」）：`SG_CHECK_DO_LEAK` →
       `app.check_do_leak`。允许省略节名前缀，前提是全库唯一。
    3. **兜底新建**：首段为节名、其余下划线合并为叶子名，使新增配置项
       无需改代码即可用环境变量注入。

    注意配置叶子名自身含下划线（如 `check_do_leak`、`default_strategy`），
    所以不能简单按「切分点」猜测，必须整串比对。
    """
    joined = "_".join(segments)

    for p in known_paths:
        if "_".join(p) == joined:
            return p

    cands = [p for p in known_paths if "_".join(p).endswith("_" + joined)]
    if len(cands) == 1:
        return cands[0]
    if cands:
        # 多个候选时取路径最短者（最贴近用户书写意图），并吵出来。
        import logging
        best = min(cands, key=lambda p: (len(p), "_".join(p)))
        logging.getLogger("sagitta.config").warning(
            "环境变量 SG_%s 可匹配多个配置项 %s，已选用 %s；请改用完整路径消除歧义。",
            joined.upper(), [".".join(c) for c in cands], ".".join(best),
        )
        return best

    if len(segments) == 1:
        return (segments[0],)
    return (segments[0], "_".join(segments[1:]))


def _normalize_legacy(raw: Dict[str, Any]) -> Dict[str, Any]:
    """把旧的扁平 `database:` 节折叠进 `db.default`，并映射 s3 → file。"""
    out = dict(raw)
    legacy_db = out.pop("database", None)
    if isinstance(legacy_db, dict):
        cfg = {k: v for k, v in legacy_db.items() if k in _LEGACY_FLAT_DB}
        # 旧的 database.url 是完整 DSN，直接作为 db.default.url
        out = _deep_merge(out, {"db": {"default": cfg}})
    elif isinstance(legacy_db, str):
        out = _deep_merge(out, {"db": {"default": {"url": legacy_db}}})

    legacy_s3 = out.pop("s3", None)
    if isinstance(legacy_s3, dict):
        mapping = {"endpoint": "endpoint", "access_key": "access_key",
                   "secret_key": "secret_key", "bucket": "bucket",
                   "region": "region", "public_domain": "public_domain"}
        filecfg = {mapping[k]: v for k, v in legacy_s3.items() if k in mapping}
        out = _deep_merge(out, {"file": filecfg})
    return out


def _profile_chain(environ: Optional[Dict[str, str]] = None) -> List[str]:
    """读取 SG_PROFILE，返回去重后的 profile 名序列（左 → 右优先级递增）。

    框架不认识这些名字，也不做合法性校验——profile 是宿主自定义的维度，
    可以是 prod / canary，也可以是 region/cn-north / role/worker。
    """
    env = os.environ if environ is None else environ
    raw = (env.get(PROFILE_ENV) or "").strip()
    if not raw:
        return []
    seen: Set[str] = set()
    out: List[str] = []
    for part in raw.split(","):
        name = part.strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _load_dotenv(project_dir: Optional[Path]) -> List[Path]:
    """加载 .env（**仅本地开发便利**），绝不覆盖真实环境变量。

    容器/K8s 中没有 .env 文件时自然 no-op——这正是它与运行环境解耦的方式。
    查找位置：cwd/.env，其次为配置文件所在目录的上级（项目根）/.env。
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return []

    candidates: List[Path] = []
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        candidates.append(cwd_env)
    if project_dir is not None:
        p = project_dir / ".env"
        if p.exists() and p not in candidates:
            candidates.append(p)

    loaded: List[Path] = []
    for p in candidates:
        try:
            # override=False：真实环境变量（Pod spec / CI）永远赢过文件
            load_dotenv(p, override=False)
            loaded.append(p)
        except Exception:
            import logging
            logging.getLogger("sagitta.config").exception(".env 加载失败: %s", p)
    return loaded


def _apply_env(raw: Dict[str, Any], defaults: Dict[str, Any],
               environ: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    env = dict(os.environ if environ is None else environ)
    merged_known = _deep_merge(defaults, raw)
    known = set(_walk_leaf_paths(merged_known))

    out = dict(raw)
    for key in sorted(env):
        if not key.startswith(ENV_PREFIX):
            continue
        value = env[key]
        body = key[len(ENV_PREFIX):].strip()
        if not body or value is None:
            continue
        segments = [s.lower() for s in body.split("_") if s]
        if not segments:
            continue
        path = _resolve_env_path(segments, known)
        sample = _get_path(merged_known, path)
        _set_path(out, path, _coerce(str(value), sample))
    return out


class ConfigLoader:
    """单例装载器。`instance().reload()` 返回合并后的原始 dict。"""

    _instance: Optional["ConfigLoader"] = None

    def __init__(self, config_path: Optional[str] = None):
        self._path: Optional[Path] = Path(config_path) if config_path else None
        self._raw: Dict[str, Any] = {}
        self._loaded_path: Optional[Path] = None
        # 装载元数据（只含路径/名字，绝不含配置值 → 可安全打日志）
        self._profiles: List[str] = []
        self._loaded_files: List[Path] = []
        self._dotenv_files: List[Path] = []

    @classmethod
    def instance(cls, config_path: Optional[str] = None) -> "ConfigLoader":
        if cls._instance is None or config_path is not None:
            cls._instance = cls(config_path)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    @property
    def path(self) -> Optional[Path]:
        return self._loaded_path

    def _discover(self) -> Optional[Path]:
        """未显式指定时，从 cwd 向上找 conf/config.yaml。"""
        if self._path is not None:
            return self._path if self._path.exists() else None
        cur = Path.cwd()
        for _ in range(6):
            cand = cur / "conf" / "config.yaml"
            if cand.exists():
                return cand
            if cur.parent == cur:
                break
            cur = cur.parent
        return None

    def _read_yaml(self, path: Path) -> Dict[str, Any]:
        try:
            import yaml
        except ImportError:
            return {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            # yaml 解析失败当空配置，不崩；但要吵出来，否则排查无从下手。
            import logging
            logging.getLogger("sagitta.config").exception("配置文件解析失败: %s", path)
            return {}
        return data if isinstance(data, dict) else {}

    def reload(self, config_path: Optional[str] = None) -> Dict[str, Any]:
        """重新装载，返回分层合并后的 dict。

        优先级（后者覆盖前者）::

            代码默认值
              < conf/config.yaml                 基线（入库、非敏感）
              < conf/config.<profile>.yaml ...   SG_PROFILE 链（入库、非敏感）
              < conf/config.local.yaml           个人覆盖（gitignore）
              < .env                              本地密钥便利件（gitignore）
              < 真实环境变量 SG_*                 K8s Secret/ConfigMap、CI
        """
        if config_path is not None:
            self._path = Path(config_path)

        path = self._discover()
        self._loaded_path = path
        conf_dir = path.parent if path else Path.cwd() / "conf"
        project_dir = path.parent.parent if path else None

        # .env 必须先于 SG_PROFILE 解析（profile 也可能由 .env 指定）
        self._dotenv_files = _load_dotenv(project_dir)
        self._profiles = _profile_chain()

        merged = dict(DEFAULTS)
        loaded_files: List[Path] = []

        def ingest(p: Path) -> None:
            nonlocal merged
            if not p.exists() or not p.is_file():
                return
            data = _normalize_legacy(self._read_yaml(p))
            merged = _deep_merge(merged, data)
            loaded_files.append(p)

        # 1) 基线
        if path:
            ingest(path)
        # 2) profile 链：缺失即跳过（可选层，不报错）
        for name in self._profiles:
            ingest(conf_dir / f"config.{name}.yaml")
        # 3) 个人覆盖
        ingest(conf_dir / LOCAL_FILENAME)
        # 4) 环境变量最高优先级
        merged = _apply_env(merged, DEFAULTS)

        self._raw = merged
        self._loaded_files = loaded_files

        import logging
        logging.getLogger("sagitta.config").info(
            "[config] profiles=%s files=%s dotenv=%s",
            self._profiles or "(none)",
            [str(p) for p in loaded_files] or "(none)",
            [str(p) for p in self._dotenv_files] or "(none)",
        )
        return merged

    @property
    def raw(self) -> Dict[str, Any]:
        return self._raw

    @property
    def profiles(self) -> List[str]:
        """实际生效的 profile 链。"""
        return list(self._profiles)

    @property
    def loaded_files(self) -> List[Path]:
        """实际加载到的 yaml 文件（按优先级升序）。"""
        return list(self._loaded_files)

    @property
    def dotenv_files(self) -> List[Path]:
        """实际加载到的 .env 文件。"""
        return list(self._dotenv_files)


__all__ = ["ConfigLoader", "DEFAULTS", "ENV_PREFIX"]

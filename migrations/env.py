# migrations/env.py
"""Alembic 迁移环境。

两件事必须在这里做对，否则 autogenerate 会**静默生成空迁移**：

1. **导入所有 DO 模块**。`Base.metadata` 只在模型类被 import 时才登记。
   框架层不知道宿主的业务包叫什么名字（本框架以 submodule 方式被引入，
   绝不能硬编码 `app.do` 这种路径），因此模型模块列表由外部注入：

     * `SG_ALEMBIC_MODEL_MODULES=app.do,app.models`   ← 推荐
     * 或 conf/config.yaml 的 `alembic.model_modules: [app.do]`
     * 或用 `alembic -x model_modules=app.do revision --autogenerate`

   一个都没配时，回退扫描 `app/do` 与 `app/models` 目录，并**吵出来**提示
   应显式配置——静默生成空迁移会让人以为模型没变化，排查成本极高。

2. **连接串由 SgSettings 注入**，不写进 alembic.ini（避免密码进 Git）。
   Alembic 是同步工具，这里取 `url_sync`（PG 走 psycopg、MySQL 走 pymysql）。
"""
import importlib
import logging
import os
import pkgutil
from logging.config import fileConfig
from pathlib import Path
from typing import List

from alembic import context
from sqlalchemy import engine_from_config, pool

from sagitta.Config.loader import ConfigLoader
from sagitta.Config.setting import build_settings
from sagitta.Database.base import Base

logger = logging.getLogger("alembic.env")

# Alembic 注入的配置对象是 context.config，不是裸名 config。
# 原实现写成 `config.set_main_option(...)` 会直接 NameError，迁移一条都跑不了。
config = context.config

settings = build_settings(ConfigLoader.instance().reload())

# 支持 `alembic -x db=default` 指定迁移哪个数据源；默认 default
_x_args = context.get_x_argument(as_dictionary=True)
DB_NAME = _x_args.get("db", os.getenv("SG_ALEMBIC_DB", "default"))


def _resolve_db_url(name: str) -> str:
    """取指定数据源的**同步** DSN。"""
    try:
        db = settings.get_db(name)
    except Exception as e:
        raise SystemExit(
            f"[alembic] 数据源 {name!r} 未配置: {e}\n"
            f"请在 conf/config.yaml 的 db.{name} 下配置，"
            f"或用 SG_DB_{name.upper()}_URL 注入。"
        ) from e
    if not db.url:
        raise SystemExit(f"[alembic] 数据源 {name!r} 未提供 url，无法迁移。")
    return db.url_sync


def _configured_model_modules() -> List[str]:
    """按优先级取模型模块列表：-x > 环境变量 > yaml > 目录扫描回退。"""
    raw = _x_args.get("model_modules") or os.getenv("SG_ALEMBIC_MODEL_MODULES") or ""
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]

    alembic_cfg = (settings.as_dict() or {}).get("alembic") or {}
    mods = alembic_cfg.get("model_modules") or []
    if isinstance(mods, str):
        mods = [m.strip() for m in mods.split(",") if m.strip()]
    if mods:
        return list(mods)

    # 回退：扫描常见目录。必须吵出来，否则空迁移无从察觉。
    fallback = []
    for cand in ("app.do", "app.models"):
        pkg_path = Path(cand.replace(".", os.sep))
        if pkg_path.is_dir():
            fallback.append(cand)
    if fallback:
        logger.warning(
            "未显式配置 alembic 模型模块，回退扫描 %s。请设置 "
            "SG_ALEMBIC_MODEL_MODULES=你的模型包 以避免漏模型导致空迁移。",
            fallback,
        )
    else:
        logger.warning(
            "未配置 alembic 模型模块，且未发现 app/do 或 app/models 目录。"
            "若 Base.metadata 为空，autogenerate 将生成空迁移。"
            "请设置 SG_ALEMBIC_MODEL_MODULES。"
        )
    return fallback


def _import_model_modules(modules: List[str]) -> None:
    """导入模型模块（含子包内全部模块），使 Base.metadata 完整。"""
    for name in modules:
        try:
            mod = importlib.import_module(name)
        except Exception:
            logger.exception("[alembic] 模型模块 %r 导入失败", name)
            continue
        # 若是个包，递归导入其下所有模块（模型可能分散在多个文件）
        if getattr(mod, "__path__", None):
            for info in pkgutil.walk_packages(mod.__path__, prefix=f"{name}."):
                try:
                    importlib.import_module(info.name)
                except Exception:
                    logger.exception("[alembic] 子模块 %r 导入失败", info.name)


_import_model_modules(_configured_model_modules())

# 迁移的元数据：所有继承 sagitta Base 的模型都在这里
target_metadata = Base.metadata

# 注入连接串（覆盖 alembic.ini 里留空的 sqlalchemy.url）
config.set_main_option("sqlalchemy.url", _resolve_db_url(DB_NAME))

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

logger.info("[alembic] db=%s tables=%d", DB_NAME, len(target_metadata.tables))


def _configure(connection=None, **extra) -> None:
    """统一的 context.configure 参数。

    compare_type / compare_server_default 开启后，列类型与默认值变更才会被
    autogenerate 检测到（README 第八节的承诺）。
    注意：server_default 比对依赖 Base.metadata 的 naming_convention，
    否则 MySQL 与 PG 之间会产生大量伪差异——见 Database/base.py。
    """
    kwargs = dict(
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        # 渲染 alembic 不认识的自定义类型时，按 SQLAlchemy 类型名处理
        render_as_batch=False,
    )
    if connection is not None:
        kwargs["connection"] = connection
    kwargs.update(extra)
    context.configure(**kwargs)


def run_migrations_offline() -> None:
    """offline 模式：不连数据库，只生成 SQL。

    `alembic upgrade head --sql > migrate.sql`，供生产环境人工 review 后执行
    （README 第八节建议的做法）。原实现缺这个分支，--sql 会直接失败。
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """online 模式：连数据库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

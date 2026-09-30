# sagittapi/cli.py
"""sgcli — Sagitta 工程脚手架 CLI。

用法::
    sgcli init myproject          # 生成新项目骨架
    sgcli create app users        # 在当前项目内创建业务模块
    sgcli serve                   # 启动 uvicorn 开发服务器
    sgcli migrate "add users"    # 生成 Alembic 迁移脚本
    sgcli version                 # 查看框架版本
"""
from __future__ import annotations
import sys
from pathlib import Path

try:
    import typer
except ImportError:
    typer = None

_PROJECT_MAIN = '''"""App entry."""
import logging
from sagittapi import bootstrap
from sagittapi.Config.setting import get_settings

logger = logging.getLogger("app")

def create_app():
    settings = get_settings()
    return bootstrap(settings=settings)

app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.server.host, port=settings.server.port, reload=settings.app.debug)
'''

_PROJECT_CONFIG = """app:
  title: {title}
  version: "0.1.0"
  debug: true
  env: dev
server:
  host: 0.0.0.0
  port: 8000
  cors_origins:
    - http://localhost:5173
db:
  default:
    type: postgresql
    host: 127.0.0.1
    port: 5432
    user: postgres
    password: ""
    database: {db_name}
    pool_size: 5
    max_overflow: 5
redis:
  default:
    url: redis://127.0.0.1:6379/0
log:
  level: DEBUG
  backend: stdlib
"""

_CONTROLLER_TPL = '''"""{module} Controller."""
from sagittapi.Flow.controller import Controller

class {cls}Controller(Controller):
    prefix = "/{module}"
'''

_SERVICE_TPL = '''"""{module} Service."""
from sagittapi.Core.inject import ServiceBase

class {cls}Service(ServiceBase):
    pass
'''

_DO_TPL = '''"""{module} DO."""
from sagittapi.Database.base import Base, SoftDeleteDO

class {cls}DO(Base, SoftDeleteDO):
    __tablename__ = "{table}"
'''

_REPO_TPL = '''"""{module} Repository."""
from sagittapi.Database.repository import BaseRepository

class {cls}Repository(BaseRepository):
    pass
'''

_DTO_TPL = '''"""{module} DTO."""
from pydantic import BaseModel

class {cls}InsertDTO(BaseModel):
    pass

class {cls}UpdateDTO(BaseModel):
    pass
'''

_VO_TPL = '''"""{module} VO."""
from pydantic import BaseModel

class {cls}VO(BaseModel):
    pass
'''

_BIZ_CODE_TPL = '''"""Business codes."""
from sagittapi.Core.biz_code import BizCode, BizCodes

class AppCodes(BizCodes):
    NOT_FOUND = BizCode(404, "Resource not found")

__all__ = ["AppCodes"]
'''

_REQUIREMENTS = "sagittapi[pg,alembic,loguru]\n"
_GITIGNORE = "__pycache__/\n*.pyc\n.venv/\nconf/config.local.yaml\n*.egg-info/\ndist/\n.uploads/\n"

_ALEMBIC_INI = "[alembic]\nscript_location = migrations\n"

_MIGRATIONS_ENV = '''"""Alembic env."""
from alembic import context
from sqlalchemy import engine_from_config, pool
from sagittapi.Config.loader import ConfigLoader
from sagittapi.Config.setting import build_settings
from sagittapi.Database.base import Base

try:
    import app  # noqa
except ImportError:
    pass

config = context.config
cfg = build_settings(ConfigLoader().load())
db_url = cfg.db["default"].url or ConfigLoader.build_dsn(cfg.db["default"])
config.set_main_option("sqlalchemy.url", db_url)
target_metadata = Base.metadata

def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"),
                       target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.bind.transaction:
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
'''

_MAKO = '''"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}

def upgrade() -> None:
    ${upgrades if upgrades else "pass"}

def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
'''


def _pascal(snake: str) -> str:
    return "".join(p.capitalize() for p in snake.split("_"))

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        typer.echo(f"  skip (exists): {path}")
        return
    path.write_text(content, encoding="utf-8")
    typer.echo(f"  create: {path}")

if typer is not None:
    app = typer.Typer(name="sgcli", help="Sagitta scaffold CLI", no_args_is_help=True, add_completion=False)

    @app.command()
    def init(name: str = typer.Argument(..., help="Project name")):
        """初始化新项目骨架。"""
        root = Path(name)
        typer.echo(f"Init project: {name}")
        dirs = [root/"app"/"controllers", root/"app"/"services", root/"app"/"do",
                root/"app"/"dto", root/"app"/"vo", root/"conf", root/"migrations"/"versions"]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
            (d/"__init__.py").touch()
        (root/"app"/"__init__.py").touch()
        _write(root/"app"/"main.py", _PROJECT_MAIN)
        _write(root/"app"/"biz_code.py", _BIZ_CODE_TPL)
        _write(root/"conf"/"config.yaml", _PROJECT_CONFIG.format(title=name.title(), db_name=name.replace("-","_")))
        _write(root/"requirements.txt", _REQUIREMENTS)
        _write(root/".gitignore", _GITIGNORE)
        _write(root/"migrations"/"alembic.ini", _ALEMBIC_INI)
        _write(root/"migrations"/"env.py", _MIGRATIONS_ENV)
        _write(root/"migrations"/"script.py.mako", _MAKO)
        typer.echo(f"\nProject {name} created. Next:")
        typer.echo(f"  cd {name}")
        typer.echo(f"  python -m venv .venv && .venv/Scripts/activate")
        typer.echo(f"  pip install -r requirements.txt")
        typer.echo(f"  sgcli serve")

    @app.command()
    def create(module: str = typer.Argument(..., help="Module name (snake_case)")):
        """创建业务模块（Controller/Service/DO/DTO/VO）。"""
        c = _pascal(module)
        b = Path("app")
        _write(b/"controllers"/f"{module}_controller.py", _CONTROLLER_TPL.format(module=module, cls=c))
        _write(b/"services"/f"{module}_service.py", _SERVICE_TPL.format(module=module, cls=c))
        _write(b/"do"/f"{module}_do.py", _DO_TPL.format(cls=c, table=module))
        _write(b/"do"/f"{module}_repo.py", _REPO_TPL.format(cls=c))
        _write(b/"dto"/f"{module}_dto.py", _DTO_TPL.format(cls=c))
        _write(b/"vo"/f"{module}_vo.py", _VO_TPL.format(cls=c))
        typer.echo(f"\nModule {module} created under app/.")

    @app.command()
    def serve(host: str = typer.Option("0.0.0.0","--host","-h"),
              port: int = typer.Option(8000,"--port","-p"),
              reload: bool = typer.Option(True,"--reload/--no-reload")):
        """启动 uvicorn 开发服务器。"""
        import uvicorn
        sys.path.insert(0, str(Path.cwd()))
        uvicorn.run("app.main:app", host=host, port=port, reload=reload)

    @app.command()
    def migrate(message: str = typer.Argument(..., help="Migration message")):
        """生成 Alembic 迁移脚本。"""
        try:
            from alembic.config import Config
            from alembic import command
        except ImportError:
            typer.echo("Error: alembic not installed. Run: pip install sagittapi[alembic]")
            raise typer.Exit(1)
        ini = Path("migrations/alembic.ini")
        if not ini.exists():
            typer.echo("Error: migrations/alembic.ini not found.")
            raise typer.Exit(1)
        cfg = Config(str(ini))
        cfg.set_main_option("script_location", str(Path("migrations")))
        command.revision(cfg, message=message, autogenerate=True)
        typer.echo(f"Migration created: {message}")

    @app.command()
    def version():
        """查看 sagittapi 版本。"""
        try:
            from sagittapi import __version__
            typer.echo(f"sagittapi {__version__}")
        except ImportError:
            typer.echo("sagittapi not installed")

    def main() -> None:
        app()
else:
    def main() -> None:
        print("sgcli requires typer: pip install sagittapi[cli]")
        sys.exit(1)

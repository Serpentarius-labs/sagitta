# sagittapi/cli.py
"""sgcli — Sagittapi project scaffolding CLI.

A lightweight command-line tool for scaffolding FastAPI projects built on the
sagittapi framework.  Designed to be both human-friendly and machine-parseable
so that LLM agents can use it as a callable skill.

Commands:
  init      Create a new project skeleton with standard directory layout.
  create    Generate a business module (Controller/Service/DO/DTO/VO/Repo).
  serve     Start the uvicorn development server.
  migrate   Create an Alembic autogenerate migration revision.
  version   Print the installed sagittapi version.

Usage examples:
  sgcli init myapi
  sgcli create users
  sgcli serve --port 9000 --no-reload
  sgcli migrate "create users table"
  sgcli version

Exit codes:
  0 = success, 1 = error (missing dependency, file not found, etc.)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import typer
except ImportError:
    typer = None  # type: ignore[assignment]


# ── Templates ────────────────────────────────────────────────────────────────

_PROJECT_MAIN = '''"""Application entry point."""
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

    uvicorn.run(
        "app.main:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=settings.app.debug,
    )
'''

_PROJECT_CONFIG = """\
app:
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

_CONTROLLER_TPL = '''"""{module} Controller — route layer (param validation only)."""
from sagittapi.Flow.controller import Controller


class {cls}Controller(Controller):
    prefix = "/{module}"
'''

_SERVICE_TPL = '''"""{module} Service — business logic (stateless singleton)."""
from sagittapi.Core.inject import ServiceBase


class {cls}Service(ServiceBase):
    pass
'''

_DO_TPL = '''"""{module} DO — SQLAlchemy model (must not leave app/)."""
from sagittapi.Database.base import Base, SoftDeleteDO


class {cls}DO(Base, SoftDeleteDO):
    __tablename__ = "{table}"
'''

_REPO_TPL = '''"""{module} Repository — the only place SQL is allowed."""
from sagittapi.Database.repository import BaseRepository


class {cls}Repository(BaseRepository):
    pass
'''

_DTO_TPL = '''"""{module} DTO — request validation models."""
from pydantic import BaseModel


class {cls}InsertDTO(BaseModel):
    pass


class {cls}UpdateDTO(BaseModel):
    pass
'''

_VO_TPL = '''"""{module} VO — response models (DO must never leak)."""
from pydantic import BaseModel


class {cls}VO(BaseModel):
    pass
'''

_BIZ_CODE_TPL = '''"""Business codes — all status codes defined here."""
from sagittapi.Core.biz_code import BizCode, BizCodes


class AppCodes(BizCodes):
    NOT_FOUND = BizCode(404, "Resource not found")


__all__ = ["AppCodes"]
'''

_REQUIREMENTS = "sagittapi[pg,alembic,loguru]\n"

_GITIGNORE = """\
__pycache__/
*.pyc
.venv/
conf/config.local.yaml
*.egg-info/
dist/
.uploads/
"""

_ALEMBIC_INI = """\
[alembic]
script_location = migrations
"""

_MIGRATIONS_ENV = '''"""Alembic migration environment."""
from alembic import context
from sqlalchemy import engine_from_config, pool

from sagittapi.Config.loader import ConfigLoader
from sagittapi.Config.setting import build_settings
from sagittapi.Database.base import Base

# Import app so Controller/DO registration populates Base.metadata
try:
    import app  # noqa: F401
except ImportError:
    pass

config = context.config

cfg = build_settings(ConfigLoader().load())
db_url = cfg.db["default"].url or ConfigLoader.build_dsn(cfg.db["default"])
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
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


# ── Helpers ──────────────────────────────────────────────────────────────────

def _pascal(snake: str) -> str:
    """Convert snake_case to PascalCase."""
    return "".join(p.capitalize() for p in snake.split("_"))


def _write(path: Path, content: str) -> None:
    """Write file with UTF-8 encoding and LF line endings (cross-platform safe).

    On Windows, Python write_text translates \\n to \\r\\n by default.
    Specifying newline="" disables newline translation so generated files
    have consistent LF endings on all platforms.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        typer.echo(f"  skip (exists): {path}")
        return
    path.write_text(content, encoding="utf-8", newline="\n")
    typer.echo(f"  create: {path}")


def _detect_venv_activate() -> str:
    """Return the platform-correct venv activation command.

    Windows:  .venv\\Scripts\\activate
    Unix:     source .venv/bin/activate
    """
    if os.name == "nt":
        return ".venv\\Scripts\\activate"
    return "source .venv/bin/activate"


# ── CLI App ──────────────────────────────────────────────────────────────────

if typer is not None:
    app = typer.Typer(
        name="sgcli",
        help=(
            "Sagittapi scaffolding CLI for FastAPI projects.\n\n"
            "Creates project skeletons, business modules, and wraps common "
            "dev tools (uvicorn, alembic) with sensible defaults.\n\n"
            "All commands are safe to re-run: existing files are skipped, "
            "never overwritten.\n\n"
            "Install CLI support: pip install sagittapi[cli]"
        ),
        no_args_is_help=True,
        add_completion=False,
        rich_markup_mode="rich",
    )

    @app.command(
        help=(
            "Create a new project skeleton.\n\n"
            "Generates a standard sagittapi project layout:\n"
            "  app/ (controllers, services, do, dto, vo)\n"
            "  conf/config.yaml (PostgreSQL + Redis defaults)\n"
            "  migrations/ (Alembic ready)\n"
            "  .gitignore, requirements.txt\n\n"
            "Arguments:\n"
            "  NAME    Project directory name (snake_case recommended).\n\n"
            "Examples:\n"
            "  sgcli init myapi\n"
            "  sgcli init blog_service\n\n"
            "Output: files created under <NAME>/ directory."
        ),
    )
    def init(
        name: str = typer.Argument(
            ...,
            help="Project name (becomes the directory name). snake_case recommended.",
            metavar="NAME",
        ),
    ) -> None:
        """Create a new project skeleton with standard directory layout."""
        root = Path(name)
        typer.echo(f"Initializing project: {name}")

        dirs = [
            root / "app" / "controllers",
            root / "app" / "services",
            root / "app" / "do",
            root / "app" / "dto",
            root / "app" / "vo",
            root / "conf",
            root / "migrations" / "versions",
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
            (d / "__init__.py").touch()
        (root / "app" / "__init__.py").touch()

        _write(root / "app" / "main.py", _PROJECT_MAIN)
        _write(root / "app" / "biz_code.py", _BIZ_CODE_TPL)
        _write(
            root / "conf" / "config.yaml",
            _PROJECT_CONFIG.format(
                title=name.title(),
                db_name=name.replace("-", "_"),
            ),
        )
        _write(root / "requirements.txt", _REQUIREMENTS)
        _write(root / ".gitignore", _GITIGNORE)
        _write(root / "migrations" / "alembic.ini", _ALEMBIC_INI)
        _write(root / "migrations" / "env.py", _MIGRATIONS_ENV)
        _write(root / "migrations" / "script.py.mako", _MAKO)

        activate_cmd = _detect_venv_activate()
        typer.echo(f"\nProject '{name}' created. Next steps:")
        typer.echo(f"  cd {name}")
        typer.echo(f"  python -m venv .venv && {activate_cmd}")
        typer.echo(f"  pip install -r requirements.txt")
        typer.echo(f"  sgcli serve")

    @app.command(
        help=(
            "Generate a business module with all layer files.\n\n"
            "Creates 6 files under app/ following the sagittapi convention:\n"
            "  app/controllers/<module>_controller.py\n"
            "  app/services/<module>_service.py\n"
            "  app/do/<module>_do.py        (SQLAlchemy model)\n"
            "  app/do/<module>_repo.py      (Repository pattern)\n"
            "  app/dto/<module>_dto.py      (Pydantic input)\n"
            "  app/vo/<module>_vo.py        (Pydantic output)\n\n"
            "Arguments:\n"
            "  MODULE    Module name in snake_case (e.g. users, blog_posts).\n\n"
            "Examples:\n"
            "  sgcli create users\n"
            "  sgcli create blog_posts\n\n"
            "Output: 6 files created under app/. Existing files are skipped."
        ),
    )
    def create(
        module: str = typer.Argument(
            ...,
            help="Module name in snake_case (e.g. users, blog_posts).",
            metavar="MODULE",
        ),
    ) -> None:
        """Generate Controller/Service/DO/DTO/VO/Repo files for a module."""
        cls = _pascal(module)
        base = Path("app")

        _write(
            base / "controllers" / f"{module}_controller.py",
            _CONTROLLER_TPL.format(module=module, cls=cls),
        )
        _write(
            base / "services" / f"{module}_service.py",
            _SERVICE_TPL.format(module=module, cls=cls),
        )
        _write(
            base / "do" / f"{module}_do.py",
            _DO_TPL.format(cls=cls, table=module),
        )
        _write(
            base / "do" / f"{module}_repo.py",
            _REPO_TPL.format(cls=cls),
        )
        _write(
            base / "dto" / f"{module}_dto.py",
            _DTO_TPL.format(cls=cls),
        )
        _write(
            base / "vo" / f"{module}_vo.py",
            _VO_TPL.format(cls=cls),
        )

        typer.echo(f"\nModule '{module}' created: 6 files under app/.")

    @app.command(
        help=(
            "Start the uvicorn development server.\n\n"
            "Wraps uvicorn.run with sagittapi defaults.  CWD must be a project\n"
            "root containing app/main.py.\n\n"
            "Options:\n"
            "  --host, -h      Bind address (default: 0.0.0.0)\n"
            "  --port, -p      Bind port (default: 8000)\n"
            "  --reload/--no-reload  Enable auto-reload (default: enabled)\n\n"
            "Examples:\n"
            "  sgcli serve\n"
            "  sgcli serve --port 9000 --no-reload\n"
            "  sgcli serve -h 127.0.0.1 -p 3000\n\n"
            "Output: uvicorn server running in foreground (Ctrl+C to stop)."
        ),
    )
    def serve(
        host: str = typer.Option("0.0.0.0", "--host", "-h", help="Bind address."),
        port: int = typer.Option(8000, "--port", "-p", help="Bind port."),
        reload: bool = typer.Option(
            True, "--reload/--no-reload",
            help="Enable auto-reload on file changes.",
        ),
    ) -> None:
        """Start uvicorn dev server for app.main:app."""
        import uvicorn

        sys.path.insert(0, str(Path.cwd()))
        uvicorn.run("app.main:app", host=host, port=port, reload=reload)

    @app.command(
        help=(
            "Create an Alembic migration revision with autogenerate.\n\n"
            "Wraps alembic.command.revision(autogenerate=True).  CWD must be a\n"
            "project root containing migrations/alembic.ini.\n\n"
            "Prerequisite: pip install sagittapi[alembic]\n\n"
            "Arguments:\n"
            "  MESSAGE    Migration description (e.g. 'create users table').\n\n"
            "Examples:\n"
            "  sgcli migrate 'create users table'\n"
            "  sgcli migrate 'add email column to users'\n\n"
            "Output: new migration script in migrations/versions/."
        ),
    )
    def migrate(
        message: str = typer.Argument(
            ...,
            help="Migration description message.",
            metavar="MESSAGE",
        ),
    ) -> None:
        """Generate an Alembic autogenerate migration revision."""
        try:
            from alembic.config import Config
            from alembic import command
        except ImportError:
            typer.echo(
                "ERROR: alembic not installed. "
                "Run: pip install sagittapi[alembic]"
            )
            raise typer.Exit(1)

        ini = Path("migrations/alembic.ini")
        if not ini.exists():
            typer.echo(
                "ERROR: migrations/alembic.ini not found. "
                "Run this command in the project root."
            )
            raise typer.Exit(1)

        cfg = Config(str(ini))
        cfg.set_main_option("script_location", str(Path("migrations")))
        command.revision(cfg, message=message, autogenerate=True)
        typer.echo(f"Migration created: {message}")

    @app.command(
        help=(
            "Print the installed sagittapi version.\n\n"
            "Useful for scripting and CI checks.\n\n"
            "Example:\n"
            "  sgcli version\n\n"
            "Output: 'sagittapi <version>' to stdout."
        ),
    )
    def version() -> None:
        """Print the installed sagittapi version."""
        try:
            from sagittapi import __version__

            typer.echo(f"sagittapi {__version__}")
        except ImportError:
            typer.echo("sagittapi not installed")
            raise typer.Exit(1)

    def main() -> None:
        app()

else:
    def main() -> None:
        print(
            "sgcli requires typer. "
            "Install with: pip install sagittapi[cli]"
        )
        sys.exit(1)
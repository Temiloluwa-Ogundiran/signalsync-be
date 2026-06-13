import os
import sys
from logging.config import fileConfig
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Enum as SQLAEnum
from sqlalchemy import engine_from_config, pool
from sqlalchemy.types import Enum as _Enum

from alembic import context
from alembic.autogenerate import renderers

# ---------------------------------------------------------------------------
# Bootstrap: add src/ to sys.path so `from app.*` imports resolve correctly
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

# Load .env before importing app modules (config.py reads env vars at import time)
load_dotenv(ROOT_DIR / ".env")

# ---------------------------------------------------------------------------
# Import Base (and all models so their tables are registered on metadata)
# ---------------------------------------------------------------------------
from app.core.database import Base  # noqa: E402

import app.models  # noqa: F401, E402 — triggers __init__.py which imports all models

# ---------------------------------------------------------------------------
# Alembic configuration
# ---------------------------------------------------------------------------
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Custom enum renderer — ensures autogenerate emits proper Enum declarations
# ---------------------------------------------------------------------------
def render_enum(type_: _Enum, autogen_context) -> str | bool:
    if isinstance(type_, _Enum):
        values = ", ".join(repr(v) for v in type_.enums)
        return f"sa.Enum({values}, name='{type_.name}')"
    return False


renderers.dispatch_for(SQLAEnum)(render_enum)


# ---------------------------------------------------------------------------
# Migration runners
# ---------------------------------------------------------------------------
def _get_url() -> str:
    # Migrations MUST bypass PgBouncer's transaction pooling: Alembic uses DDL and
    # its own session-scoped advisory locks, neither of which survive a transaction
    # pooler. DATABASE_URL_DIRECT points straight at Postgres; fall back to
    # DATABASE_URL for local/dev setups that have no pooler in front.
    url = os.getenv("DATABASE_URL_DIRECT") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "Neither DATABASE_URL_DIRECT nor DATABASE_URL is set. "
            "Create a .env file at the project root (see .env.example)."
        )
    return url


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (generates SQL script)."""
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    url = _get_url()
    config.set_main_option("sqlalchemy.url", url)

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

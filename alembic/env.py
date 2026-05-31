"""
alembic/env.py — Alembic environment for raw-SQL migrations (no ORM).

Reads the database URL from the DATABASE_URL environment variable.
Does NOT use SQLAlchemy ORM models (target_metadata = None).
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Alembic Config object — provides access to .ini values
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No ORM metadata — migrations are pure raw SQL via op.execute()
target_metadata = None


def get_url() -> str:
    """Read DATABASE_URL from environment (secrets never hard-coded — D-02)."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        # Also check the .ini setting (supports CI/CD where env var is set there)
        url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Export it as an environment variable before "
            "running Alembic. Example: DATABASE_URL=postgresql://user:pass@host/db alembic upgrade head"
        )
    # This project uses psycopg3 only (no psycopg2). SQLAlchemy defaults the
    # bare `postgresql://` scheme to the psycopg2 dialect, so coerce the scheme
    # to the psycopg3 driver (`postgresql+psycopg://`). Idempotent for URLs that
    # already specify the psycopg driver.
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url[len(prefix):]
            break
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL script, no live connection)."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (executes against a live DB)."""
    # Override the ini sqlalchemy.url with the env var
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

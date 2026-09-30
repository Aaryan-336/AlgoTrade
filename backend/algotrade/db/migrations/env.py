"""Alembic environment. The URL comes from the caller (see db/migrate.py)
or from DATABASE_URL, never from a checked-in file."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from algotrade.db.models import Base

config = context.config
url = config.get_main_option("sqlalchemy.url") or os.environ.get(
    "DATABASE_URL", "sqlite:///./algotrade.db")
config.set_main_option("sqlalchemy.url", url)
target_metadata = Base.metadata


def run_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=url.startswith("sqlite"))
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is None:
        connectable = engine_from_config(config.get_section(config.config_ini_section) or {},
                                         prefix="sqlalchemy.", poolclass=pool.NullPool)
        with connectable.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata,
                              render_as_batch=url.startswith("sqlite"))
            with context.begin_transaction():
                context.run_migrations()
        return
    context.configure(connection=connectable, target_metadata=target_metadata,
                      render_as_batch=url.startswith("sqlite"))
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()

"""Apply Alembic migrations on startup (versioned schema, docs/CLAUDE.md)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from algotrade.db.session import Database

MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def migrate(db: Database) -> None:
    cfg = alembic_config(str(db.engine.url.render_as_string(hide_password=False)))
    tables = set(inspect(db.engine).get_table_names())
    with db.engine.begin() as conn:
        cfg.attributes["connection"] = conn
        if "alembic_version" not in tables and "orders" in tables:
            command.stamp(cfg, "head")  # schema created by create_all (tests, old installs)
        else:
            command.upgrade(cfg, "head")

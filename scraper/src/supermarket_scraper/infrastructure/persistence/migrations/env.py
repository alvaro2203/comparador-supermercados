"""Alembic environment for the scraper schema.

At runtime ``upgrade_schema`` hands over an open connection through
``config.attributes["connection"]``. When Alembic is invoked from the command line
(``uv run alembic ...``) no connection is given, and one is opened from
``SCRAPER_DATABASE_URL`` exactly as the application does.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection

from supermarket_scraper.config import Settings
from supermarket_scraper.infrastructure.persistence.database import (
    build_engine,
    migration_connection,
)
from supermarket_scraper.infrastructure.persistence.tables import metadata


def run_migrations(connection: Connection) -> None:
    """Run the pending migrations on ``connection`` in batch mode, so SQLite can alter tables."""
    context.configure(
        connection=connection,
        target_metadata=metadata,
        render_as_batch=True,
        user_module_prefix="column_types.",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    """Migrate the shared connection, or a new one opened from the application settings."""
    shared = context.config.attributes.get("connection")
    if isinstance(shared, Connection):
        run_migrations(shared)
        return
    engine = build_engine(Settings().database_url)
    try:
        with migration_connection(engine) as connection:
            run_migrations(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    msg = "Offline (--sql) migrations are not supported; run them against a database"
    raise RuntimeError(msg)
run_online()

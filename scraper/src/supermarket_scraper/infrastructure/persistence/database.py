from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Final

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.engine import make_url

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Connection
    from sqlalchemy.pool import ConnectionPoolEntry

logger: Final = logging.getLogger(__name__)

MIGRATIONS_LOCATION: Final = "supermarket_scraper.infrastructure.persistence:migrations"
BASELINE_REVISION: Final = "0001"
_VERSION_TABLE: Final = "alembic_version"
_PRE_MIGRATION_MARKER_TABLE: Final = "supermarkets"


def build_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Create an engine for any SQLAlchemy URL, applying per-dialect hardening.

    For SQLite the parent directory of the database file is created and the
    connection enables foreign keys and write-ahead logging.
    """
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        if url.database and url.database != ":memory:":
            Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, echo=echo)
        event.listen(engine, "connect", _configure_sqlite)
        return engine
    return create_engine(url, echo=echo, pool_pre_ping=True)


def _configure_sqlite(dbapi_connection: object, _record: ConnectionPoolEntry) -> None:
    """Enable referential integrity and concurrent readers on every SQLite connection."""
    cursor_factory = getattr(dbapi_connection, "cursor", None)
    if cursor_factory is None:
        return
    cursor = cursor_factory()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def alembic_config() -> Config:
    """Build the Alembic configuration used at runtime, independent of ``alembic.ini``."""
    config = Config()
    config.set_main_option("script_location", MIGRATIONS_LOCATION)
    return config


def upgrade_schema(engine: Engine) -> None:
    """Apply every pending migration so the schema matches ``tables.py`` (idempotent).

    A database created before migrations existed (by ``create_all``) has the
    baseline tables but no version table. It is stamped at the baseline revision
    first, so its tables and data are adopted instead of recreated.
    """
    config = alembic_config()
    with _migration_connection(engine) as connection:
        config.attributes["connection"] = connection
        existing_tables = set(inspect(connection).get_table_names())
        if _VERSION_TABLE not in existing_tables and _PRE_MIGRATION_MARKER_TABLE in existing_tables:
            logger.info(
                "Adopting a database created before migrations",
                extra={"revision": BASELINE_REVISION},
            )
            command.stamp(config, BASELINE_REVISION)
        previous_revision = _current_revision(connection)
        command.upgrade(config, "head")
        current_revision = _current_revision(connection)
    if current_revision != previous_revision:
        logger.info(
            "Database schema upgraded",
            extra={"from_revision": previous_revision, "to_revision": current_revision},
        )


def _current_revision(connection: Connection) -> str | None:
    """Return the migration revision the database is at, or ``None`` if it has none."""
    return MigrationContext.configure(connection).get_current_revision()


@contextmanager
def _migration_connection(engine: Engine) -> Iterator[Connection]:
    """Yield a connection inside one transaction that migrations can safely use.

    On SQLite, foreign keys are switched off for the duration. Batch migrations
    rebuild a table by dropping it, and with enforcement on that drop would fire
    ``ON DELETE CASCADE`` and wipe the price history that references it.
    """
    with engine.connect() as connection:
        is_sqlite = connection.dialect.name == "sqlite"
        if is_sqlite:
            _set_sqlite_foreign_keys(connection, enabled=False)
        try:
            with connection.begin():
                yield connection
        finally:
            if is_sqlite:
                _set_sqlite_foreign_keys(connection, enabled=True)


def _set_sqlite_foreign_keys(connection: Connection, *, enabled: bool) -> None:
    """Toggle SQLite foreign key enforcement, which only works outside a transaction."""
    connection.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")
    connection.commit()

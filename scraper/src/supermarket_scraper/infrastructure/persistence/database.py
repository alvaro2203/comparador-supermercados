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
BASELINE_TABLES: Final = frozenset(
    {
        "supermarkets",
        "categories",
        "products",
        "product_categories",
        "price_snapshots",
        "scrape_runs",
    }
)


class SchemaMigrationError(Exception):
    """The database schema could not be brought to the latest migration."""


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

    Raises:
        SchemaMigrationError: A migration failed, in which case every statement
            of the attempt is rolled back, or the database holds only part of
            the baseline tables and cannot be adopted safely.
    """
    config = alembic_config()
    try:
        with migration_connection(engine) as connection:
            config.attributes["connection"] = connection
            _adopt_pre_migration_database(connection, config)
            previous_revision = _current_revision(connection)
            command.upgrade(config, "head")
            current_revision = _current_revision(connection)
    except SchemaMigrationError:
        raise
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        raise SchemaMigrationError(msg) from exc
    if current_revision != previous_revision:
        logger.info(
            "Database schema upgraded",
            extra={"from_revision": previous_revision, "to_revision": current_revision},
        )


def _adopt_pre_migration_database(connection: Connection, config: Config) -> None:
    """Stamp a complete pre-migration database at the baseline; refuse an incomplete one."""
    existing_tables = set(inspect(connection).get_table_names())
    baseline_present = BASELINE_TABLES & existing_tables
    if _VERSION_TABLE in existing_tables or not baseline_present:
        return
    missing = BASELINE_TABLES - existing_tables
    if missing:
        msg = (
            "The database has no migration version and only part of the baseline tables "
            f"(missing: {', '.join(sorted(missing))}). Restore it from a backup or point "
            "SCRAPER_DATABASE_URL at a new database"
        )
        raise SchemaMigrationError(msg)
    logger.info(
        "Adopting a database created before migrations",
        extra={"revision": BASELINE_REVISION},
    )
    command.stamp(config, BASELINE_REVISION)


def _current_revision(connection: Connection) -> str | None:
    """Return the migration revision the database is at, or ``None`` if it has none."""
    return MigrationContext.configure(connection).get_current_revision()


@contextmanager
def migration_connection(engine: Engine) -> Iterator[Connection]:
    """Yield a connection inside one transaction that migrations can safely use.

    Every migration path (``upgrade_schema`` and the Alembic command line) must
    go through here. On SQLite two driver defaults are overridden while it is open:

    * Foreign keys are switched off. Batch migrations rebuild a table by dropping
      it, and with enforcement on that drop would fire ``ON DELETE CASCADE`` and
      wipe the price history that references it.
    * The transaction is begun explicitly. Otherwise pysqlite commits each
      ``CREATE`` and ``DROP`` on its own, and a failing migration would leave half
      of its tables behind.

    Both are restored before the connection goes back to the pool.
    """
    with engine.connect() as connection:
        if connection.dialect.name != "sqlite":
            with connection.begin():
                yield connection
            return
        with _sqlite_migration_mode(connection), connection.begin():
            connection.exec_driver_sql("BEGIN")
            yield connection


@contextmanager
def _sqlite_migration_mode(connection: Connection) -> Iterator[None]:
    """Hand transaction control to SQLAlchemy and disable foreign keys until exit."""
    driver = connection.connection.driver_connection
    if driver is None:
        msg = "The SQLite connection was closed before migrating"
        raise SchemaMigrationError(msg)
    previous_isolation_level = driver.isolation_level
    driver.isolation_level = None
    _set_sqlite_foreign_keys(connection, enabled=False)
    try:
        yield
    finally:
        _set_sqlite_foreign_keys(connection, enabled=True)
        driver.isolation_level = previous_isolation_level


def _set_sqlite_foreign_keys(connection: Connection, *, enabled: bool) -> None:
    """Toggle SQLite foreign key enforcement, which only works outside a transaction."""
    connection.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")
    connection.commit()

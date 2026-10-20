from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Column, String, func, inspect, select

from supermarket_scraper.infrastructure.persistence import database
from supermarket_scraper.infrastructure.persistence.database import (
    BASELINE_REVISION,
    alembic_config,
    build_engine,
    upgrade_schema,
)
from supermarket_scraper.infrastructure.persistence.tables import (
    metadata,
    price_snapshots,
    products,
    supermarkets,
)

if TYPE_CHECKING:
    from sqlalchemy import Connection, Engine

ALEMBIC_INI = Path(__file__).parents[2] / "alembic.ini"
SEEN_AT = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)


def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def insert_price_history(connection: Connection) -> None:
    connection.execute(
        supermarkets.insert().values(
            code="mercadona",
            name="Mercadona",
            website="https://tienda.mercadona.es",
            country_code="ES",
            created_at=SEEN_AT,
            updated_at=SEEN_AT,
        )
    )
    connection.execute(
        products.insert().values(
            id=1,
            supermarket_code="mercadona",
            external_id="4241",
            name="Leche entera",
            selling_method="unit",
            first_seen_at=SEEN_AT,
            last_seen_at=SEEN_AT,
        )
    )
    connection.execute(
        price_snapshots.insert().values(
            product_id=1,
            captured_on=date(2026, 10, 1),
            captured_at=SEEN_AT,
            currency="EUR",
            amount=Decimal("0.95"),
            reference_unit="l",
        )
    )


def count_snapshots(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(price_snapshots)).scalar_one()


def test_migrations_build_exactly_the_schema_declared_in_tables(engine: Engine) -> None:
    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), metadata)

    assert differences == []
    assert current_revision(engine) == head_revision()


def test_new_database_upgrade_is_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    fresh = build_engine(f"sqlite:///{tmp_path / 'fresh.db'}")

    with caplog.at_level(logging.INFO, logger=database.__name__):
        upgrade_schema(fresh)

    assert [record.getMessage() for record in caplog.records] == ["Database schema upgraded"]
    fresh.dispose()


def test_upgrade_is_idempotent(engine: Engine, caplog: pytest.LogCaptureFixture) -> None:
    with engine.begin() as connection:
        insert_price_history(connection)

    with caplog.at_level(logging.INFO, logger=database.__name__):
        upgrade_schema(engine)

    assert current_revision(engine) == head_revision()
    assert count_snapshots(engine) == 1
    assert caplog.records == []


def test_pre_migration_database_is_adopted_without_losing_data(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    legacy = build_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    metadata.create_all(legacy)
    with legacy.begin() as connection:
        insert_price_history(connection)

    with caplog.at_level(logging.INFO, logger=database.__name__):
        upgrade_schema(legacy)

    assert current_revision(legacy) == head_revision()
    assert count_snapshots(legacy) == 1
    assert "Adopting a database created before migrations" in caplog.messages
    legacy.dispose()


def test_batch_rebuild_of_products_keeps_price_history(engine: Engine) -> None:
    with engine.begin() as connection:
        insert_price_history(connection)

    with database._migration_connection(engine) as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("products", recreate="always") as batch:
            batch.add_column(Column("nickname", String(20), nullable=True))

    assert "nickname" in {column["name"] for column in inspect(engine).get_columns("products")}
    assert count_snapshots(engine) == 1
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1


def test_baseline_downgrade_is_refused_and_keeps_data(engine: Engine) -> None:
    with engine.begin() as connection:
        insert_price_history(connection)
    config = alembic_config()

    with database._migration_connection(engine) as connection:
        config.attributes["connection"] = connection
        with pytest.raises(NotImplementedError, match="irreversible"):
            command.downgrade(config, "base")

    assert current_revision(engine) == BASELINE_REVISION
    assert count_snapshots(engine) == 1


def test_alembic_command_line_uses_scraper_database_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_file = tmp_path / "cli.db"
    monkeypatch.setenv("SCRAPER_DATABASE_URL", f"sqlite:///{database_file}")

    command.upgrade(Config(ALEMBIC_INI), "head")

    migrated = build_engine(f"sqlite:///{database_file}")
    assert current_revision(migrated) == head_revision()
    migrated.dispose()


def test_offline_sql_generation_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="Offline"):
        command.upgrade(alembic_config(), "head", sql=True)

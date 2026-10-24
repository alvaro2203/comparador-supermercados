from __future__ import annotations

import logging
import shutil
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
    SchemaMigrationError,
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
MIGRATIONS_DIR = Path(database.__file__).parent / "migrations"

REBUILD_PRODUCTS_REVISION = """
import sqlalchemy as sa
from alembic import op

revision = "test"
down_revision = "{head}"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("products", recreate="always") as batch:
        batch.add_column(sa.Column("nickname", sa.String(20), nullable=True))


def downgrade():
    pass
"""

FAILING_REVISION = """
import sqlalchemy as sa
from alembic import op

revision = "test"
down_revision = "{head}"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("leftover", sa.Column("id", sa.Integer, primary_key=True))
    with op.batch_alter_table("products", recreate="always") as batch:
        batch.add_column(sa.Column("nickname", sa.String(20), nullable=True))
    raise RuntimeError("boom in the test revision")


def downgrade():
    pass
"""


def migrations_with_revision(tmp_path: Path, source: str) -> Path:
    """Copy the real migrations and append one revision, written for the test, on top of head."""
    location = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, location, ignore=shutil.ignore_patterns("__pycache__"))
    revision = source.replace("{head}", str(head_revision()))
    (location / "versions" / "v_test_revision.py").write_text(revision, encoding="utf-8")
    return location


def head_revision() -> str | None:
    return ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()


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
    migrate_to(legacy, BASELINE_REVISION)
    with legacy.begin() as connection:
        connection.exec_driver_sql("DROP TABLE alembic_version")
        insert_price_history(connection)

    with caplog.at_level(logging.INFO, logger=database.__name__):
        upgrade_schema(legacy)

    assert current_revision(legacy) == head_revision()
    assert count_snapshots(legacy) == 1
    assert "Adopting a database created before migrations" in caplog.messages
    legacy.dispose()


def test_batch_rebuild_of_products_keeps_price_history(sqlite_engine: Engine) -> None:
    with sqlite_engine.begin() as connection:
        insert_price_history(connection)

    with database.migration_connection(sqlite_engine) as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("products", recreate="always") as batch:
            batch.add_column(Column("nickname", String(20), nullable=True))

    assert "nickname" in {
        column["name"] for column in inspect(sqlite_engine).get_columns("products")
    }
    assert count_snapshots(sqlite_engine) == 1
    with sqlite_engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1


def test_baseline_downgrade_is_refused_and_keeps_data(engine: Engine) -> None:
    with engine.begin() as connection:
        insert_price_history(connection)

    with pytest.raises(NotImplementedError, match="irreversible"):
        downgrade_to(engine, "base")

    assert current_revision(engine) == head_revision()
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


def test_alembic_command_line_rebuilds_products_without_losing_price_history(
    sqlite_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with sqlite_engine.begin() as connection:
        insert_price_history(connection)
    monkeypatch.setenv("SCRAPER_DATABASE_URL", sqlite_engine.url.render_as_string())
    config = Config(ALEMBIC_INI)
    config.set_main_option(
        "script_location", str(migrations_with_revision(tmp_path, REBUILD_PRODUCTS_REVISION))
    )

    command.upgrade(config, "head")

    assert current_revision(sqlite_engine) == "test"
    assert "nickname" in {
        column["name"] for column in inspect(sqlite_engine).get_columns("products")
    }
    assert count_snapshots(sqlite_engine) == 1


def test_failed_migration_rolls_back_every_statement(
    sqlite_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with sqlite_engine.begin() as connection:
        insert_price_history(connection)
    location = migrations_with_revision(tmp_path, FAILING_REVISION)
    monkeypatch.setattr(database, "MIGRATIONS_LOCATION", str(location))

    with pytest.raises(SchemaMigrationError, match="boom in the test revision"):
        upgrade_schema(sqlite_engine)

    tables = set(inspect(sqlite_engine).get_table_names())
    assert "leftover" not in tables
    assert "_alembic_tmp_products" not in tables
    assert current_revision(sqlite_engine) == head_revision()
    assert count_snapshots(sqlite_engine) == 1
    with sqlite_engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1


def test_partially_created_pre_migration_database_is_refused(tmp_path: Path) -> None:
    partial = build_engine(f"sqlite:///{tmp_path / 'partial.db'}")
    supermarkets.create(partial)

    with pytest.raises(SchemaMigrationError, match="price_snapshots"):
        upgrade_schema(partial)

    assert current_revision(partial) is None
    partial.dispose()


ORPHAN_ROW_REVISION = """
import sqlalchemy as sa
from alembic import op

revision = "test"
down_revision = "{head}"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "INSERT INTO price_snapshots "
        "(product_id, captured_on, captured_at, currency, amount, reference_unit, is_promotion) "
        "VALUES (999, '2026-10-02', '2026-10-02 06:00:00', 'EUR', 9500, 'l', 0)"
    )


def downgrade():
    pass
"""


def test_migration_that_leaves_orphan_rows_is_rolled_back(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with engine.begin() as connection:
        insert_price_history(connection)
    location = migrations_with_revision(tmp_path, ORPHAN_ROW_REVISION)
    monkeypatch.setattr(database, "MIGRATIONS_LOCATION", str(location))

    with pytest.raises(SchemaMigrationError, match="price_snapshots"):
        upgrade_schema(engine)

    assert current_revision(engine) == head_revision()
    assert count_snapshots(engine) == 1


def test_shared_connection_of_the_wrong_type_is_refused(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fallback_database = tmp_path / "fallback.db"
    monkeypatch.setenv("SCRAPER_DATABASE_URL", f"sqlite:///{fallback_database}")
    config = alembic_config()
    config.attributes["connection"] = engine

    with pytest.raises(TypeError, match="Connection"):
        command.upgrade(config, "head")

    assert not fallback_database.exists()


def migrate_to(engine: Engine, revision: str) -> None:
    config = alembic_config()
    with database.migration_connection(engine) as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def downgrade_to(engine: Engine, revision: str) -> None:
    config = alembic_config()
    with database.migration_connection(engine) as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, revision)


def stored_gtins(engine: Engine) -> dict[str, str | None]:
    with engine.connect() as connection:
        rows = connection.execute(select(products.c.external_id, products.c.gtin))
        return {row.external_id: row.gtin for row in rows}


def test_gtin_migration_backfills_valid_eans_and_survives_a_round_trip(tmp_path: Path) -> None:
    baseline = build_engine(f"sqlite:///{tmp_path / 'baseline.db'}")
    migrate_to(baseline, BASELINE_REVISION)
    with baseline.begin() as connection:
        insert_price_history(connection)
        connection.execute(
            products.insert(),
            [
                {
                    "supermarket_code": "mercadona",
                    "external_id": external_id,
                    "name": "Producto",
                    "ean": ean,
                    "selling_method": "unit",
                    "first_seen_at": SEEN_AT,
                    "last_seen_at": SEEN_AT,
                }
                for external_id, ean in (("nutella", "80177173"), ("typo", "8480000108141"))
            ],
        )

    migrate_to(baseline, "0002")

    expected = {"4241": None, "nutella": "00000080177173", "typo": None}
    assert stored_gtins(baseline) == expected
    downgrade_to(baseline, BASELINE_REVISION)
    assert "gtin" not in {column["name"] for column in inspect(baseline).get_columns("products")}
    migrate_to(baseline, "0002")
    assert stored_gtins(baseline) == expected
    assert count_snapshots(baseline) == 1
    baseline.dispose()

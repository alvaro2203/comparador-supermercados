from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select, update

from supermarket_scraper.infrastructure.persistence.data_copy import (
    COPY_ORDER,
    DataCopyError,
    copy_database,
)
from supermarket_scraper.infrastructure.persistence.database import build_engine, upgrade_schema
from supermarket_scraper.infrastructure.persistence.tables import products
from supermarket_scraper.presentation.cli import ExitCode, main
from tests.integration.test_price_report import two_days_of_prices

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from sqlalchemy import Engine, Table

    from supermarket_scraper.domain.repositories import UnitOfWork
    from supermarket_scraper.infrastructure.scrapers.mercadona.scraper import MercadonaScraper
    from tests.conftest import FakeApi, FixedClock


@pytest.fixture
def target(tmp_path: Path) -> Iterator[Engine]:
    engine = build_engine(f"sqlite:///{tmp_path / 'target.db'}")
    upgrade_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
async def source(
    engine: Engine,
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    fake_api: FakeApi,
) -> Engine:
    """Two days of prices, with one product pointing at another as its successor."""
    await two_days_of_prices(make_scraper, uow_factory, clock, fake_api)
    with engine.begin() as connection:
        ids = connection.execute(select(products.c.id).order_by(products.c.id)).scalars().all()
        connection.execute(
            update(products).where(products.c.id == ids[0]).values(replaced_by_product_id=ids[-1])
        )
    return engine


def rows_of(engine: Engine, table: Table) -> list[tuple[object, ...]]:
    with engine.connect() as connection:
        ordered = select(table).order_by(*table.primary_key.columns)
        return [tuple(row) for row in connection.execute(ordered)]


def test_copy_moves_every_table_keeping_ids_and_links(source: Engine, target: Engine) -> None:
    report = copy_database(source, target)

    assert report.committed
    for table in COPY_ORDER:
        assert rows_of(target, table) == rows_of(source, table)
        assert report.rows[table.name] == len(rows_of(source, table)) > 0
    with target.connect() as connection:
        linked = connection.execute(
            select(func.count()).where(products.c.replaced_by_product_id.is_not(None))
        ).scalar_one()
    assert linked == 1


def test_dry_run_verifies_the_copy_and_leaves_the_target_empty(
    source: Engine, target: Engine
) -> None:
    report = copy_database(source, target, dry_run=True)

    assert not report.committed
    assert report.rows["price_snapshots"] > 0
    assert all(rows_of(target, table) == [] for table in COPY_ORDER)


def test_copy_refuses_a_target_that_already_has_rows(source: Engine, target: Engine) -> None:
    copy_database(source, target)

    with pytest.raises(DataCopyError, match="already has rows in supermarkets"):
        copy_database(source, target)


def test_copy_refuses_a_target_at_another_schema_revision(engine: Engine, tmp_path: Path) -> None:
    unmigrated = build_engine(f"sqlite:///{tmp_path / 'unmigrated.db'}")

    with pytest.raises(DataCopyError, match="migration"):
        copy_database(engine, unmigrated)
    unmigrated.dispose()


def test_copy_db_command_copies_and_rejects_the_same_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source_url = f"sqlite:///{tmp_path / 'source.db'}"
    target_url = f"sqlite:///{tmp_path / 'target.db'}"
    monkeypatch.setenv("SCRAPER_DATABASE_URL", source_url)

    assert main(["copy-db", "--to", target_url, "--dry-run"]) == ExitCode.OK
    assert "verified and rolled back (dry run)" in capsys.readouterr().out
    assert main(["copy-db", "--to", target_url]) == ExitCode.OK
    assert "copied and verified" in capsys.readouterr().out
    assert main(["copy-db", "--to", source_url]) == ExitCode.USAGE
    assert "different database" in capsys.readouterr().err

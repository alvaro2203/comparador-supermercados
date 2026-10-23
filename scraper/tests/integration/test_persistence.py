from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, select
from sqlalchemy.exc import StatementError

from supermarket_scraper.domain.entities import RunStats, ScrapeRun, Supermarket
from supermarket_scraper.domain.value_objects import SupermarketCode
from supermarket_scraper.infrastructure.persistence.column_types import ExactDecimal, UtcDateTime
from supermarket_scraper.infrastructure.persistence.upsert import (
    PortableUpsert,
    SqliteUpsert,
    register_upsert_strategy,
    upsert,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy import Engine

    from supermarket_scraper.domain.repositories import UnitOfWork

metadata = MetaData()
sample = Table(
    "sample",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(20)),
    Column("amount", ExactDecimal()),
    Column("at", UtcDateTime()),
)


def test_exact_decimal_and_utc_roundtrip(engine: Engine) -> None:
    metadata.create_all(engine)
    madrid = timezone(timedelta(hours=2))
    with engine.begin() as connection:
        connection.execute(
            sample.insert().values(
                id=1,
                name="a",
                amount=Decimal("1234.56789"),
                at=datetime(2026, 1, 1, 8, tzinfo=madrid),
            )
        )
        row = connection.execute(select(sample)).one()
    assert row.amount == Decimal("1234.5679")
    assert row.at == datetime(2026, 1, 1, 6, tzinfo=UTC)


def test_naive_datetimes_are_rejected(engine: Engine) -> None:
    metadata.create_all(engine)
    with engine.begin() as connection, pytest.raises(StatementError):
        connection.execute(sample.insert().values(id=2, at=datetime.fromisoformat("2026-01-01")))


@pytest.mark.parametrize("portable", [False, True])
def test_upsert_inserts_then_updates(engine: Engine, *, portable: bool) -> None:
    metadata.create_all(engine)
    if portable:
        register_upsert_strategy("sqlite", PortableUpsert())
    try:
        with engine.begin() as connection:
            upsert(
                connection,
                sample,
                [{"id": 1, "name": "a", "amount": Decimal(1), "at": None}],
                conflict_columns=("id",),
                update_columns=("name",),
            )
            upsert(
                connection,
                sample,
                [
                    {"id": 1, "name": "b", "amount": Decimal(9), "at": None},
                    {"id": 2, "name": "c", "amount": Decimal(2), "at": None},
                ],
                conflict_columns=("id",),
                update_columns=("name",),
            )
            rows = connection.execute(select(sample).order_by(sample.c.id)).all()
    finally:
        register_upsert_strategy("sqlite", SqliteUpsert())
    assert [(row.id, row.name, row.amount) for row in rows] == [
        (1, "b", Decimal(1)),
        (2, "c", Decimal(2)),
    ]


def test_run_recorded_before_the_price_change_breakdown_reads_back_without_it(
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    code = SupermarketCode("mercadona")
    started = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
    legacy = ScrapeRun.start(code, started).complete(started, RunStats(price_changes=5))
    with uow_factory() as uow:
        uow.supermarkets.upsert(
            Supermarket(code=code, name="Mercadona", website="https://tienda.mercadona.es")
        )
        uow.runs.save(legacy)
        uow.commit()

    with uow_factory() as uow:
        stored = uow.runs.latest(code, 1)[0]
    assert stored.stats.price_changes == 5
    assert stored.stats.price_change_kinds is None

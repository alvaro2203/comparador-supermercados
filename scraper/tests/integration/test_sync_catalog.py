from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, cast

from sqlalchemy import func, select

from supermarket_scraper.application.sync_catalog import SyncCatalogUseCase, SyncOptions
from supermarket_scraper.domain.entities import RunStatus
from supermarket_scraper.domain.value_objects import SupermarketCode
from supermarket_scraper.infrastructure.persistence import tables as schema

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sqlalchemy import Engine, Table

    from supermarket_scraper.application.ports import CatalogSource, DetailsBatch
    from supermarket_scraper.domain.entities import CatalogSnapshot, Supermarket
    from supermarket_scraper.domain.repositories import UnitOfWork
    from supermarket_scraper.infrastructure.scrapers.mercadona.scraper import MercadonaScraper
    from tests.conftest import FakeApi, FixedClock

type Page = dict[str, list[dict[str, list[dict[str, object]]]]]


def oil_price(engine: Engine) -> list[tuple[str, Decimal]]:
    statement = (
        select(schema.price_snapshots.c.captured_on, schema.price_snapshots.c.amount)
        .join(schema.products, schema.products.c.id == schema.price_snapshots.c.product_id)
        .where(schema.products.c.external_id == "4241")
        .order_by(schema.price_snapshots.c.captured_on)
    )
    with engine.connect() as connection:
        return [(str(row.captured_on), row.amount) for row in connection.execute(statement)]


def count(engine: Engine, table: Table) -> int:
    with engine.connect() as connection:
        return int(connection.execute(select(func.count()).select_from(table)).scalar_one())


async def test_first_run_persists_full_catalog(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    engine: Engine,
    fake_api: FakeApi,
) -> None:
    template = cast("dict[str, object]", fake_api.responses["/api/products/4241/"])
    for external_id in ("4749", "34008", "15061"):
        fake_api.responses[f"/api/products/{external_id}/"] = {
            **template,
            "id": external_id,
            "ean": None,
        }
    use_case = SyncCatalogUseCase(uow_factory, clock)
    async with make_scraper() as source:
        report = await use_case.execute(source)

    run = report.run
    assert run.status is RunStatus.SUCCEEDED
    assert run.stats.products == 4
    assert run.stats.new_products == 4
    assert run.stats.categories == 6
    assert run.stats.details_enriched == 4
    assert run.stats.price_changes == 0
    assert count(engine, schema.product_categories) == 5
    assert oil_price(engine) == [("2026-10-01", Decimal("8.9500"))]

    with engine.connect() as connection:
        oil = connection.execute(
            select(schema.products).where(schema.products.c.external_id == "4241")
        ).one()
    assert oil.ean == "8480000108142"
    assert oil.brand == "Hacendado"
    assert oil.details_fetched_at is not None


async def test_daily_runs_build_price_history_and_detect_changes(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    engine: Engine,
    fake_api: FakeApi,
) -> None:
    use_case = SyncCatalogUseCase(uow_factory, clock, SyncOptions(fetch_details=False))
    async with make_scraper() as source:
        await use_case.execute(source)

    clock.advance()
    page = cast("Page", fake_api.responses["/api/categories/112/"])
    oil = page["categories"][0]["products"][0]
    instructions = cast("dict[str, object]", oil["price_instructions"])
    instructions.update(unit_price="9.45", price_decreased=False, previous_unit_price=None)
    async with make_scraper() as source:
        second = await use_case.execute(source)

    assert second.run.stats.new_products == 0
    assert second.run.stats.price_changes == 1
    assert oil_price(engine) == [
        ("2026-10-01", Decimal("8.9500")),
        ("2026-10-02", Decimal("9.4500")),
    ]

    async with make_scraper() as source:
        rerun = await use_case.execute(source)
    assert rerun.run.status is RunStatus.SUCCEEDED
    assert len(oil_price(engine)) == 2


async def test_vanished_products_are_delisted_and_come_back(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    engine: Engine,
    fake_api: FakeApi,
) -> None:
    use_case = SyncCatalogUseCase(
        uow_factory, clock, SyncOptions(fetch_details=False, max_delist_ratio=Decimal("0.5"))
    )
    async with make_scraper() as source:
        await use_case.execute(source)

    page = cast("Page", fake_api.responses["/api/categories/112/"])
    removed = page["categories"][1]["products"].pop()
    clock.advance()
    async with make_scraper() as source:
        report = await use_case.execute(source)
    assert report.run.stats.delisted_products == 1

    with uow_factory() as uow:
        assert uow.products.count_active(SupermarketCode("mercadona")) == 3

    page["categories"][1]["products"].append(removed)
    clock.advance()
    async with make_scraper() as source:
        await use_case.execute(source)
    with uow_factory() as uow:
        assert uow.products.count_active(SupermarketCode("mercadona")) == 4


async def test_partial_scrape_never_delists(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    fake_api: FakeApi,
) -> None:
    use_case = SyncCatalogUseCase(uow_factory, clock, SyncOptions(fetch_details=False))
    async with make_scraper() as source:
        await use_case.execute(source)

    fake_api.responses["/api/categories/115/"] = 503
    clock.advance()
    async with make_scraper() as source:
        report = await use_case.execute(source)

    assert report.run.status is RunStatus.PARTIAL
    assert report.run.stats.delisted_products == 0
    with uow_factory() as uow:
        assert uow.products.count_active(SupermarketCode("mercadona")) == 4


async def test_mass_disappearance_is_guarded(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    fake_api: FakeApi,
) -> None:
    use_case = SyncCatalogUseCase(uow_factory, clock, SyncOptions(fetch_details=False))
    async with make_scraper() as source:
        await use_case.execute(source)

    page = cast("Page", fake_api.responses["/api/categories/112/"])
    page["categories"][0]["products"].clear()
    clock.advance()
    async with make_scraper() as source:
        report = await use_case.execute(source)

    assert report.run.stats.delisted_products == 0


async def test_unreachable_source_marks_run_failed(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    fake_api: FakeApi,
) -> None:
    fake_api.responses["/api/categories/"] = 503
    use_case = SyncCatalogUseCase(uow_factory, clock)
    async with make_scraper() as source:
        report = await use_case.execute(source)

    assert report.run.status is RunStatus.FAILED
    assert report.run.error_message is not None
    with uow_factory() as uow:
        runs = uow.runs.latest(SupermarketCode("mercadona"), 5)
    assert [run.status for run in runs] == [RunStatus.FAILED]


async def test_dry_run_writes_nothing(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    engine: Engine,
) -> None:
    use_case = SyncCatalogUseCase(uow_factory, clock, SyncOptions(dry_run=True))
    async with make_scraper() as source:
        report = await use_case.execute(source)

    assert report.dry_run
    assert report.run.stats.products == 4
    assert count(engine, schema.products) == 0
    assert count(engine, schema.scrape_runs) == 0


async def test_detail_failures_are_counted_but_do_not_block_the_run(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
) -> None:
    use_case = SyncCatalogUseCase(uow_factory, clock)
    async with make_scraper() as source:
        report = await use_case.execute(source)

    assert report.run.status is RunStatus.PARTIAL
    assert report.run.stats.details_enriched == 1
    assert report.run.stats.errors == 3
    with uow_factory() as uow:
        pending = uow.products.external_ids_missing_details(SupermarketCode("mercadona"), 10)
    assert sorted(pending) == ["15061", "34008", "4749"]


class ExplodingDetailsSource:
    """Delegates to a real scraper but crashes while enriching product details."""

    def __init__(self, inner: MercadonaScraper) -> None:
        self._inner = inner

    @property
    def supermarket(self) -> Supermarket:
        return self._inner.supermarket

    async def fetch_catalog(self) -> CatalogSnapshot:
        return await self._inner.fetch_catalog()

    async def fetch_product_details(self, external_ids: Sequence[str]) -> DetailsBatch:
        msg = f"boom while enriching {len(external_ids)} products"
        raise RuntimeError(msg)


async def test_enrichment_crash_keeps_committed_catalog(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    engine: Engine,
) -> None:
    use_case = SyncCatalogUseCase(uow_factory, clock)
    async with make_scraper() as scraper:
        source = cast("CatalogSource", ExplodingDetailsSource(scraper))
        report = await use_case.execute(source)

    assert report.run.status is RunStatus.PARTIAL
    assert report.run.stats.new_products == 4
    assert report.run.stats.errors == 1
    assert count(engine, schema.products) == 4
    assert count(engine, schema.price_snapshots) == 4

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from sqlalchemy import select

from supermarket_scraper.application.enrich_details import EnrichDetailsUseCase, EnrichOptions
from supermarket_scraper.application.sync_catalog import SyncCatalogUseCase, SyncOptions
from supermarket_scraper.domain.entities import ProductDetails, RunStatus
from supermarket_scraper.infrastructure.persistence import tables as schema
from supermarket_scraper.infrastructure.scrapers.mercadona.scraper import MERCADONA_CODE

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest
    from sqlalchemy import Engine

    from supermarket_scraper.domain.repositories import UnitOfWork
    from supermarket_scraper.infrastructure.scrapers.mercadona.scraper import MercadonaScraper
    from tests.conftest import FakeApi, FixedClock

CATALOG_IDS = ("4241", "4749", "34008", "15061")


def serve_details(fake_api: FakeApi, eans: dict[str, str | None]) -> None:
    template = cast("dict[str, object]", fake_api.responses["/api/products/4241/"])
    for external_id, ean in eans.items():
        fake_api.responses[f"/api/products/{external_id}/"] = {
            **template,
            "id": external_id,
            "ean": ean,
        }


def detail_requests(fake_api: FakeApi) -> list[str]:
    return [call.url.path for call in fake_api.calls if call.url.path.startswith("/api/products/")]


def identity(engine: Engine) -> dict[str, tuple[str | None, str | None, bool]]:
    products = schema.products
    statement = select(
        products.c.external_id,
        products.c.ean,
        products.c.gtin,
        products.c.details_fetched_at.is_not(None).label("enriched"),
    )
    with engine.connect() as connection:
        return {
            row.external_id: (row.ean, row.gtin, bool(row.enriched))
            for row in connection.execute(statement)
        }


async def load_catalog_without_details(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
) -> None:
    use_case = SyncCatalogUseCase(uow_factory, clock, SyncOptions(fetch_details=False))
    async with make_scraper() as source:
        await use_case.execute(source)


async def test_backfill_enriches_every_pending_product_chunk_by_chunk(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    engine: Engine,
    fake_api: FakeApi,
) -> None:
    await load_catalog_without_details(make_scraper, uow_factory, clock)
    serve_details(
        fake_api,
        {"4241": "8480000108142", "4749": "80177173", "34008": "8480000108141", "15061": None},
    )
    use_case = EnrichDetailsUseCase(uow_factory, clock, EnrichOptions(chunk_size=3))

    async with make_scraper() as source:
        report = await use_case.execute(source)

    assert report.supermarket == MERCADONA_CODE
    assert (report.pending, report.enriched, report.failures) == (4, 4, 0)
    assert report.status is RunStatus.SUCCEEDED
    assert not report.stopped_early
    assert identity(engine) == {
        "4241": ("8480000108142", "08480000108142", True),
        "4749": ("80177173", "00000080177173", True),
        "34008": ("8480000108141", None, True),
        "15061": (None, None, True),
    }
    assert sorted(detail_requests(fake_api)) == sorted(
        f"/api/products/{external_id}/" for external_id in CATALOG_IDS
    )


async def test_backfill_honours_the_limit_and_resumes_later(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    engine: Engine,
    fake_api: FakeApi,
) -> None:
    await load_catalog_without_details(make_scraper, uow_factory, clock)
    serve_details(fake_api, dict.fromkeys(CATALOG_IDS, "80177173"))

    async with make_scraper() as source:
        first = await EnrichDetailsUseCase(uow_factory, clock, EnrichOptions(limit=1)).execute(
            source
        )
        rest = await EnrichDetailsUseCase(uow_factory, clock).execute(source)

    assert (first.pending, first.enriched) == (1, 1)
    assert (rest.pending, rest.enriched) == (3, 3)
    assert all(enriched for _, _, enriched in identity(engine).values())
    assert len(detail_requests(fake_api)) == 4


async def test_backfill_stops_when_a_whole_chunk_fails(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    engine: Engine,
    fake_api: FakeApi,
) -> None:
    await load_catalog_without_details(make_scraper, uow_factory, clock)
    del fake_api.responses["/api/products/4241/"]
    use_case = EnrichDetailsUseCase(uow_factory, clock, EnrichOptions(chunk_size=2))

    async with make_scraper() as source:
        report = await use_case.execute(source)

    assert (report.pending, report.enriched, report.failures) == (4, 0, 2)
    assert report.stopped_early
    assert report.status is RunStatus.FAILED
    assert len(detail_requests(fake_api)) == 2
    assert not any(enriched for _, _, enriched in identity(engine).values())


async def test_some_failures_make_the_backfill_partial(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await load_catalog_without_details(make_scraper, uow_factory, clock)
    caplog.set_level(logging.WARNING)

    async with make_scraper() as source:
        report = await EnrichDetailsUseCase(uow_factory, clock).execute(source)

    assert (report.pending, report.enriched, report.failures) == (4, 1, 3)
    assert not report.stopped_early
    assert report.status is RunStatus.PARTIAL
    assert sorted(failure.scope for failure in report.reasons) == [
        "product 15061",
        "product 34008",
        "product 4749",
    ]
    assert all("404" in failure.reason for failure in report.reasons)
    logged = {
        str(getattr(record, "scope", "")): str(getattr(record, "reason", ""))
        for record in caplog.records
        if record.message == "Detail enrichment failure"
    }
    assert logged.keys() == {failure.scope for failure in report.reasons}
    assert logged["product 4749"] == "GET /api/products/4749/ returned 404"


async def test_nothing_pending_makes_no_request(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    fake_api: FakeApi,
) -> None:
    async with make_scraper() as source:
        report = await EnrichDetailsUseCase(uow_factory, clock).execute(source)

    assert (report.pending, report.enriched, report.failures) == (0, 0, 0)
    assert report.status is RunStatus.SUCCEEDED
    assert fake_api.calls == []


async def test_gtin_always_follows_the_stored_ean(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    engine: Engine,
) -> None:
    await load_catalog_without_details(make_scraper, uow_factory, clock)

    def apply(ean: str | None) -> None:
        with uow_factory() as uow:
            uow.products.apply_details(
                [ProductDetails(supermarket=MERCADONA_CODE, external_id="4241", ean=ean)],
                clock.now(),
            )
            uow.commit()

    apply("80177173")
    assert identity(engine)["4241"][:2] == ("80177173", "00000080177173")
    apply(None)
    assert identity(engine)["4241"][:2] == ("80177173", "00000080177173")
    apply("8480000108141")
    assert identity(engine)["4241"][:2] == ("8480000108141", None)


async def test_stopping_after_a_stored_chunk_is_partial(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    fake_api: FakeApi,
) -> None:
    await load_catalog_without_details(make_scraper, uow_factory, clock)
    use_case = EnrichDetailsUseCase(uow_factory, clock, EnrichOptions(chunk_size=1))

    async with make_scraper() as source:
        report = await use_case.execute(source)

    assert (report.pending, report.enriched, report.failures) == (4, 1, 1)
    assert report.stopped_early
    assert report.status is RunStatus.PARTIAL
    assert detail_requests(fake_api) == ["/api/products/4241/", "/api/products/4749/"]

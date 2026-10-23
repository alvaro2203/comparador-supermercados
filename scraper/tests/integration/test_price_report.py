from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, cast

import pytest

from supermarket_scraper.application.price_report import BuildPriceReportUseCase
from supermarket_scraper.application.sync_catalog import SyncCatalogUseCase, SyncOptions
from supermarket_scraper.domain.entities import PriceChangeCounts
from supermarket_scraper.domain.exceptions import NoPriceDataError
from supermarket_scraper.infrastructure.scrapers.mercadona.scraper import MERCADONA_CODE

if TYPE_CHECKING:
    from collections.abc import Callable

    from supermarket_scraper.domain.repositories import UnitOfWork
    from supermarket_scraper.infrastructure.scrapers.mercadona.scraper import MercadonaScraper
    from tests.conftest import FakeApi, FixedClock

type Page = dict[str, list[dict[str, list[dict[str, object]]]]]


async def two_days_of_prices(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    fake_api: FakeApi,
) -> None:
    """Day 1: the fixture catalog. Day 2: a promotion ends, prices move, one swap of products."""
    use_case = SyncCatalogUseCase(
        uow_factory, clock, SyncOptions(fetch_details=False, max_delist_ratio=Decimal("0.5"))
    )
    async with make_scraper() as source:
        await use_case.execute(source)

    oils = cast("Page", fake_api.responses["/api/categories/112/"])["categories"]
    oil, garrafa = oils[0]["products"]
    salt = oils[1]["products"][0]
    cast("dict[str, object]", oil["price_instructions"]).update(
        unit_price="9.45", price_decreased=False, previous_unit_price=None
    )
    cast("dict[str, object]", garrafa["price_instructions"]).update(unit_price="39.95")
    cast("dict[str, object]", salt["price_instructions"]).update(unit_price="0.30")
    oils[1]["products"].append({**salt, "id": "99999", "display_name": "Sal gorda Hacendado"})
    spices = cast("Page", fake_api.responses["/api/categories/115/"])["categories"]
    spices[0]["products"].pop(0)
    clock.advance()
    async with make_scraper() as source:
        await use_case.execute(source)


async def test_report_compares_the_latest_day_with_the_previous_one(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    fake_api: FakeApi,
) -> None:
    await two_days_of_prices(make_scraper, uow_factory, clock, fake_api)

    report = BuildPriceReportUseCase(uow_factory).execute(MERCADONA_CODE)

    assert (report.day, report.previous_day) == (date(2026, 10, 2), date(2026, 10, 1))
    assert report.active_products == 4
    assert report.price_change_kinds == PriceChangeCounts(
        increased=1, decreased=1, promotions_ended=1
    )
    assert [move.product.external_id for move in report.top_increases] == ["4749"]
    increase = report.top_increases[0]
    assert increase.product.label == "Aceite de oliva 0,4º Hacendado (Garrafa 5 l)"
    assert (increase.before.amount, increase.after.amount) == (Decimal("36.95"), Decimal("39.95"))
    assert increase.change_percentage == Decimal("8.12")
    assert [move.product.external_id for move in report.top_decreases] == ["34008"]
    assert report.top_decreases[0].change_percentage == Decimal("-14.29")
    assert report.promotions_started == ()
    assert [product.external_id for product in report.new_products] == ["99999"]
    assert [product.external_id for product in report.delisted_products] == ["15061"]
    assert report.new_arrivals == 1
    assert report.promotions_active == 0
    assert (report.products_with_gtin, report.unknown_reference_units) == (0, 0)


async def test_report_of_an_earlier_day_and_top_limit(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    fake_api: FakeApi,
) -> None:
    await two_days_of_prices(make_scraper, uow_factory, clock, fake_api)

    first = BuildPriceReportUseCase(uow_factory).execute(MERCADONA_CODE, day=date(2026, 10, 1))

    assert first.previous_day is None
    assert first.price_change_kinds is None
    assert first.new_products == first.delisted_products == ()
    assert first.promotions_active == 1


async def test_report_without_data_is_refused(
    uow_factory: Callable[[], UnitOfWork],
    make_scraper: Callable[[], MercadonaScraper],
    clock: FixedClock,
    fake_api: FakeApi,
) -> None:
    with pytest.raises(NoPriceDataError, match="No prices stored"):
        BuildPriceReportUseCase(uow_factory).execute(MERCADONA_CODE)

    await two_days_of_prices(make_scraper, uow_factory, clock, fake_api)
    with pytest.raises(NoPriceDataError, match="2026-09-30"):
        BuildPriceReportUseCase(uow_factory).execute(MERCADONA_CODE, day=date(2026, 9, 30))


async def test_top_moves_are_sorted_and_limited(
    make_scraper: Callable[[], MercadonaScraper],
    uow_factory: Callable[[], UnitOfWork],
    clock: FixedClock,
    fake_api: FakeApi,
) -> None:
    use_case = SyncCatalogUseCase(uow_factory, clock, SyncOptions(fetch_details=False))
    async with make_scraper() as source:
        await use_case.execute(source)
    oils = cast("Page", fake_api.responses["/api/categories/112/"])["categories"]
    garrafa = oils[0]["products"][1]
    salt = oils[1]["products"][0]
    cast("dict[str, object]", garrafa["price_instructions"]).update(unit_price="37.95")
    cast("dict[str, object]", salt["price_instructions"]).update(unit_price="0.70")
    clock.advance()
    async with make_scraper() as source:
        await use_case.execute(source)

    report = BuildPriceReportUseCase(uow_factory).execute(MERCADONA_CODE, top=1)

    assert [move.product.external_id for move in report.top_increases] == ["34008"]
    assert report.top_increases[0].change_percentage == Decimal("100.00")

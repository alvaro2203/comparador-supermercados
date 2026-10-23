from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, cast

from supermarket_scraper.domain.value_objects import MeasureUnit, SellingMethod
from supermarket_scraper.infrastructure.scrapers.registry import default_registry, discover_scrapers

if TYPE_CHECKING:
    from collections.abc import Callable

    from supermarket_scraper.infrastructure.scrapers.mercadona.scraper import MercadonaScraper
    from tests.conftest import FakeApi


async def test_fetch_catalog_normalizes_tree_products_and_prices(
    make_scraper: Callable[[], MercadonaScraper], fake_api: FakeApi
) -> None:
    async with make_scraper() as scraper:
        snapshot = await scraper.fetch_catalog()

    assert snapshot.is_complete
    category_ids = {category.external_id for category in snapshot.categories}
    assert category_ids == {"12", "112", "115", "420", "421", "430"}
    assert "999" not in category_ids

    listings = {item.product.external_id: item for item in snapshot.deduplicated_listings()}
    assert set(listings) == {"4241", "4749", "34008", "15061"}

    oil = listings["4241"]
    assert oil.product.category_external_ids == ("420", "430")
    assert oil.price.amount == Decimal("8.95")
    assert oil.price.previous_amount == Decimal("9.45")
    assert oil.price.is_promotion
    assert oil.price.reference_unit is MeasureUnit.LITRE
    assert oil.price.vat_rate == Decimal(4)
    assert oil.product.packaging.selling_method is SellingMethod.UNIT

    assert listings["4749"].product.is_new_arrival
    assert not oil.product.is_new_arrival
    pepper = listings["15061"].price
    assert pepper.reference_amount == Decimal("29.000")
    assert pepper.reference_unit is MeasureUnit.KILOGRAM
    salt = listings["34008"]
    assert salt.price.previous_amount is None
    assert salt.product.packaging.is_pack
    assert salt.product.packaging.total_units == 2
    assert not listings["15061"].product.is_available

    for request in fake_api.calls:
        assert request.url.params["wh"] == "vlc1"
        assert request.url.params["lang"] == "es"


async def test_failing_category_is_isolated(
    make_scraper: Callable[[], MercadonaScraper], fake_api: FakeApi
) -> None:
    fake_api.responses["/api/categories/115/"] = 500
    async with make_scraper() as scraper:
        snapshot = await scraper.fetch_catalog()

    assert not snapshot.is_complete
    assert len(snapshot.failures) == 1
    assert "category 115" in snapshot.failures[0].scope
    assert {item.product.external_id for item in snapshot.listings} == {"4241", "4749", "34008"}


async def test_invalid_product_is_isolated(
    make_scraper: Callable[[], MercadonaScraper], fake_api: FakeApi
) -> None:
    page = cast(
        "dict[str, list[dict[str, list[dict[str, object]]]]]",
        fake_api.responses["/api/categories/115/"],
    )
    page["categories"][0]["products"].append(
        {"id": "666", "display_name": "Roto", "price_instructions": {"unit_price": "-1"}}
    )
    async with make_scraper() as scraper:
        snapshot = await scraper.fetch_catalog()

    assert len(snapshot.failures) == 1
    assert "666" not in {item.product.external_id for item in snapshot.listings}


async def test_schema_drift_is_reported_as_failure(
    make_scraper: Callable[[], MercadonaScraper], fake_api: FakeApi
) -> None:
    fake_api.responses["/api/categories/112/"] = {"unexpected": True}
    async with make_scraper() as scraper:
        snapshot = await scraper.fetch_catalog()

    assert len(snapshot.failures) == 1
    assert "Unexpected Mercadona" in snapshot.failures[0].reason


async def test_fetch_product_details_cleans_html_and_isolates_missing(
    make_scraper: Callable[[], MercadonaScraper],
) -> None:
    async with make_scraper() as scraper:
        batch = await scraper.fetch_product_details(["4241", "missing"])

    assert len(batch.details) == 1
    assert len(batch.failures) == 1
    detail = batch.details[0]
    assert detail.ean == "8480000108142"
    assert detail.brand == "Hacendado"
    assert detail.origin == "España"
    assert detail.description == "Aceite de oliva virgen extra & afrutado"
    assert detail.allergens is None
    assert detail.image_url is not None


def test_mercadona_is_discovered_by_registry() -> None:
    discover_scrapers()
    assert "mercadona" in default_registry.codes()


async def test_product_without_its_new_arrival_flag_is_schema_drift(
    make_scraper: Callable[[], MercadonaScraper], fake_api: FakeApi
) -> None:
    page = cast(
        "dict[str, list[dict[str, list[dict[str, object]]]]]",
        fake_api.responses["/api/categories/115/"],
    )
    del page["categories"][0]["products"][0]["is_new_arrival"]
    async with make_scraper() as scraper:
        snapshot = await scraper.fetch_catalog()

    assert len(snapshot.failures) == 1
    assert "is_new_arrival" in snapshot.failures[0].reason
    assert "15061" not in {item.product.external_id for item in snapshot.listings}

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from supermarket_scraper.domain.entities import (
    CatalogSnapshot,
    Category,
    Product,
    ProductListing,
    RunStats,
    RunStatus,
    ScrapeFailure,
    ScrapeRun,
    Supermarket,
)
from supermarket_scraper.domain.exceptions import InvalidEntityError
from supermarket_scraper.domain.value_objects import MeasureUnit, Price, SupermarketCode

CODE = SupermarketCode("mercadona")
NOW = datetime(2026, 10, 1, tzinfo=UTC)


def test_supermarket_code_rejects_invalid_slugs() -> None:
    with pytest.raises(InvalidEntityError):
        SupermarketCode("Mercadona!")


def test_price_rejects_negative_amounts() -> None:
    with pytest.raises(InvalidEntityError):
        Price(amount=Decimal("-0.01"))


def test_price_rejects_invalid_currency() -> None:
    with pytest.raises(InvalidEntityError):
        Price(amount=Decimal(1), currency="euro")


def test_discount_percentage() -> None:
    price = Price(amount=Decimal("8.95"), previous_amount=Decimal("9.45"), is_promotion=True)
    assert price.discount_percentage == Decimal("5.29")
    assert Price(amount=Decimal(1)).discount_percentage is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("kg", MeasureUnit.KILOGRAM),
        ("L", MeasureUnit.LITRE),
        ("ud.", MeasureUnit.UNIT),
        ("docena", MeasureUnit.DOZEN),
        (None, MeasureUnit.UNKNOWN),
        ("fanegas", MeasureUnit.UNKNOWN),
    ],
)
def test_measure_unit_parsing(raw: str | None, expected: MeasureUnit) -> None:
    assert MeasureUnit.parse(raw) == expected


def test_product_requires_numeric_ean() -> None:
    with pytest.raises(InvalidEntityError):
        Product(supermarket=CODE, external_id="1", name="Leche", ean="ABC")


def test_category_cannot_be_its_own_parent() -> None:
    with pytest.raises(InvalidEntityError):
        Category(supermarket=CODE, external_id="1", name="A", level=1, parent_external_id="1")


def test_snapshot_merges_duplicated_products_and_their_categories() -> None:
    price = Price(amount=Decimal(1))
    first = ProductListing(
        Product(supermarket=CODE, external_id="1", name="Sal", category_external_ids=("a",)),
        price,
    )
    second = ProductListing(
        Product(supermarket=CODE, external_id="1", name="Sal", category_external_ids=("b", "a")),
        price,
    )
    snapshot = CatalogSnapshot(
        supermarket=Supermarket(code=CODE, name="Mercadona", website="https://x"),
        categories=(),
        listings=(first, second),
    )
    merged = snapshot.deduplicated_listings()
    assert len(merged) == 1
    assert merged[0].product.category_external_ids == ("a", "b")


def test_run_lifecycle() -> None:
    run = ScrapeRun.start(CODE, NOW)
    assert run.status is RunStatus.RUNNING
    assert run.complete(NOW, RunStats(products=10)).status is RunStatus.SUCCEEDED
    assert run.complete(NOW, RunStats(errors=1)).status is RunStatus.PARTIAL
    failed = run.fail(NOW, "boom")
    assert failed.status is RunStatus.FAILED
    assert failed.error_message == "boom"


def test_snapshot_completeness() -> None:
    supermarket = Supermarket(code=CODE, name="Mercadona", website="https://x")
    assert CatalogSnapshot(supermarket, (), ()).is_complete
    incomplete = CatalogSnapshot(supermarket, (), (), (ScrapeFailure("x", "y"),))
    assert not incomplete.is_complete

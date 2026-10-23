from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from supermarket_scraper.domain.entities import (
    CatalogSnapshot,
    Category,
    PackagingInfo,
    PriceChangeCounts,
    Product,
    ProductDetails,
    ProductListing,
    RunStats,
    RunStatus,
    ScrapeFailure,
    ScrapeRun,
    Supermarket,
)
from supermarket_scraper.domain.exceptions import InvalidEntityError
from supermarket_scraper.domain.value_objects import (
    MeasureUnit,
    Price,
    PriceChangeKind,
    SupermarketCode,
    normalize_gtin,
)

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


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("8480000108142", "08480000108142"),
        ("80177173", "00000080177173"),
        ("150570037606", "00150570037606"),
        ("80432115985", "00080432115985"),
        ("08480000108142", "08480000108142"),
        ("8480000108141", None),
        ("1234567", None),
        ("123456789012345", None),
        ("848000010814A", None),
        (chr(0xFF18) + "480000108142", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_gtin(code: str | None, expected: str | None) -> None:
    assert normalize_gtin(code) == expected


def test_product_and_details_expose_their_gtin() -> None:
    product = Product(supermarket=CODE, external_id="1", name="Leche", ean="80177173")
    details = ProductDetails(supermarket=CODE, external_id="1", ean="8480000108141")

    assert product.gtin == "00000080177173"
    assert details.gtin is None
    assert ProductDetails(supermarket=CODE, external_id="1").gtin is None


@pytest.mark.parametrize(
    ("raw", "quantity", "unit"),
    [
        ("kg", Decimal(1), MeasureUnit.KILOGRAM),
        ("L", Decimal(1), MeasureUnit.LITRE),
        ("ud", Decimal(1), MeasureUnit.UNIT),
        ("100 g", Decimal("0.1"), MeasureUnit.KILOGRAM),
        ("100 ml", Decimal("0.1"), MeasureUnit.LITRE),
        ("75 cl", Decimal("0.75"), MeasureUnit.LITRE),
        ("12 ud", Decimal(12), MeasureUnit.UNIT),
        ("1,5 l", Decimal("1.5"), MeasureUnit.LITRE),
        ("100g", Decimal("0.1"), MeasureUnit.KILOGRAM),
        ("0 g", Decimal(1), MeasureUnit.UNKNOWN),
        ("lavado", Decimal(1), MeasureUnit.UNKNOWN),
        ("100 furlongs", Decimal(1), MeasureUnit.UNKNOWN),
        (None, Decimal(1), MeasureUnit.UNKNOWN),
    ],
)
def test_reference_format_is_split_into_quantity_and_base_unit(
    raw: str | None, quantity: Decimal, unit: MeasureUnit
) -> None:
    assert MeasureUnit.parse_quantity(raw) == (quantity, unit)


@pytest.mark.parametrize(
    ("before", "after", "kind"),
    [
        (Price(Decimal("1.00")), Price(Decimal("1.00")), None),
        (Price(Decimal("1.00")), Price(Decimal("1.10")), PriceChangeKind.INCREASED),
        (Price(Decimal("1.10")), Price(Decimal("1.00")), PriceChangeKind.DECREASED),
        (
            Price(Decimal("1.10")),
            Price(Decimal("0.90"), is_promotion=True),
            PriceChangeKind.PROMOTION_STARTED,
        ),
        (
            Price(Decimal("0.90"), is_promotion=True),
            Price(Decimal("1.10")),
            PriceChangeKind.PROMOTION_ENDED,
        ),
        (
            Price(Decimal("0.90"), is_promotion=True),
            Price(Decimal("0.80"), is_promotion=True),
            PriceChangeKind.DECREASED,
        ),
        (
            Price(Decimal("1.00"), is_promotion=True),
            Price(Decimal("1.00")),
            PriceChangeKind.PROMOTION_ENDED,
        ),
    ],
)
def test_price_change_is_classified(
    before: Price, after: Price, kind: PriceChangeKind | None
) -> None:
    assert after.change_from(before) is kind


def test_price_change_counts_tally_every_kind() -> None:
    counts = PriceChangeCounts.tally(
        [
            PriceChangeKind.INCREASED,
            PriceChangeKind.INCREASED,
            PriceChangeKind.DECREASED,
            PriceChangeKind.PROMOTION_STARTED,
            PriceChangeKind.PROMOTION_ENDED,
            PriceChangeKind.PROMOTION_ENDED,
        ]
    )

    assert counts == PriceChangeCounts(
        increased=2, decreased=1, promotions_started=1, promotions_ended=2
    )
    assert counts.total == 6
    assert PriceChangeCounts.tally([]).total == 0


@pytest.mark.parametrize(
    ("packaging", "label"),
    [
        (
            PackagingInfo(packaging="Garrafa", unit_size=Decimal(5), size_format="l"),
            "Aceite (Garrafa 5 l)",
        ),
        (
            PackagingInfo(packaging="Spray", unit_size=Decimal("0.2"), size_format="l"),
            "Aceite (Spray 200 ml)",
        ),
        (
            PackagingInfo(packaging="Bote", unit_size=Decimal("0.025"), size_format="kg"),
            "Aceite (Bote 25 g)",
        ),
        (PackagingInfo(unit_size=Decimal("1.5"), size_format="kg"), "Aceite (1,5 kg)"),
        (PackagingInfo(unit_size=Decimal(36), size_format="ud"), "Aceite (36 ud)"),
        (
            PackagingInfo(
                packaging="Pack-6",
                unit_size=Decimal(9),
                size_format="l",
                is_pack=True,
                pack_size=Decimal("1.5"),
                total_units=6,
            ),
            "Aceite (Pack-6 6 x 1,5 l)",
        ),
        (
            PackagingInfo(
                packaging="Pieza",
                unit_size=Decimal("0.13"),
                size_format="kg",
                is_approximate_size=True,
            ),
            "Aceite (Pieza aprox. 130 g)",
        ),
        (
            PackagingInfo(
                packaging="Paquete", unit_size=Decimal(1), size_format="ud", total_units=26
            ),
            "Aceite (Paquete 26 ud)",
        ),
        (
            PackagingInfo(
                packaging="Caja", unit_size=Decimal("0.24"), size_format="l", total_units=12
            ),
            "Aceite (Caja 240 ml, 12 ud)",
        ),
        (PackagingInfo(packaging="Caja"), "Aceite (Caja)"),
        (PackagingInfo(), "Aceite"),
    ],
)
def test_product_label_always_carries_its_format(packaging: PackagingInfo, label: str) -> None:
    product = Product(supermarket=CODE, external_id="1", name="Aceite", packaging=packaging)

    assert product.label == label

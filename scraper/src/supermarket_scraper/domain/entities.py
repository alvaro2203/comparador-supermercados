from __future__ import annotations

import re
import uuid
from collections import Counter
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from supermarket_scraper.domain.exceptions import InvalidEntityError
from supermarket_scraper.domain.value_objects import (
    Price,
    PriceChangeKind,
    SellingMethod,
    SupermarketCode,
    normalize_gtin,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date, datetime

_COUNTRY_CODE: Final = re.compile(r"^[A-Z]{2}$")


_SMALLER_UNITS: Final[dict[str, tuple[str, Decimal]]] = {
    "kg": ("g", Decimal(1000)),
    "l": ("ml", Decimal(1000)),
}


def _quantity(amount: Decimal, size_format: str | None) -> str:
    """Write a size the way a Spanish shelf label does: ``25 g``, ``1,5 l``, ``36 ud``."""
    unit = size_format or ""
    if unit in _SMALLER_UNITS and amount < 1:
        unit, factor = _SMALLER_UNITS[unit]
        amount *= factor
    number = format(amount.normalize(), "f").replace(".", ",")
    return f"{number} {unit}".strip()


def _require_text(value: str, label: str) -> None:
    """Raise if a mandatory text attribute is blank."""
    if not value.strip():
        msg = f"{label} cannot be empty"
        raise InvalidEntityError(msg)


@dataclass(frozen=True, slots=True)
class Supermarket:
    """A supermarket chain whose catalog is tracked."""

    code: SupermarketCode
    name: str
    website: str
    country_code: str = "ES"

    def __post_init__(self) -> None:
        """Validate mandatory attributes."""
        _require_text(self.name, "Supermarket name")
        if not _COUNTRY_CODE.fullmatch(self.country_code):
            msg = f"Invalid ISO-3166 country code: {self.country_code!r}"
            raise InvalidEntityError(msg)


@dataclass(frozen=True, slots=True)
class Category:
    """A node of a supermarket category tree, identified by the supermarket's own id."""

    supermarket: SupermarketCode
    external_id: str
    name: str
    level: int
    position: int = 0
    parent_external_id: str | None = None

    def __post_init__(self) -> None:
        """Validate identifiers and tree depth."""
        _require_text(self.external_id, "Category external id")
        _require_text(self.name, "Category name")
        if self.level < 0:
            msg = f"Category level cannot be negative: {self.level}"
            raise InvalidEntityError(msg)
        if self.parent_external_id == self.external_id:
            msg = f"Category {self.external_id} cannot be its own parent"
            raise InvalidEntityError(msg)


@dataclass(frozen=True, slots=True)
class PackagingInfo:
    """Size and format information about how a product is packaged and sold."""

    packaging: str | None = None
    unit_size: Decimal | None = None
    size_format: str | None = None
    is_pack: bool = False
    pack_size: Decimal | None = None
    total_units: int | None = None
    selling_method: SellingMethod = SellingMethod.UNKNOWN
    is_approximate_size: bool = False

    def describe(self) -> str:
        """Describe the presentation: ``Garrafa 5 l``, ``Pack-6 6 x 1,5 l``, ``Paquete 26 ud``...

        Several products of a chain often share a name and differ only here, so any
        listing must show it next to the name.
        """
        units = self.total_units if self.total_units and self.total_units > 1 else None
        if self.is_pack and units and self.pack_size:
            size = f"{units} x {_quantity(self.pack_size, self.size_format)}"
        elif units and self.size_format == "ud" and self.unit_size == 1:
            size = f"{units} ud"
        elif self.unit_size:
            size = _quantity(self.unit_size, self.size_format)
            size = f"{size}, {units} ud" if units else size
        else:
            size = ""
        if size and self.is_approximate_size:
            size = f"aprox. {size}"
        return " ".join(part for part in (self.packaging, size) if part)


@dataclass(frozen=True, slots=True)
class Product:
    """A sellable item of a supermarket catalog as seen on a given scrape."""

    supermarket: SupermarketCode
    external_id: str
    name: str
    packaging: PackagingInfo = field(default_factory=PackagingInfo)
    category_external_ids: tuple[str, ...] = ()
    slug: str | None = None
    brand: str | None = None
    ean: str | None = None
    thumbnail_url: str | None = None
    share_url: str | None = None
    is_available: bool = True
    is_new_arrival: bool = False

    def __post_init__(self) -> None:
        """Validate identifiers and EAN format."""
        _require_text(self.external_id, "Product external id")
        _require_text(self.name, "Product name")
        if self.ean is not None and not self.ean.isdigit():
            msg = f"EAN must be numeric, got {self.ean!r}"
            raise InvalidEntityError(msg)

    @property
    def gtin(self) -> str | None:
        """Return the EAN as a 14-digit GTIN, or ``None`` if it is missing or invalid."""
        return normalize_gtin(self.ean)

    @property
    def label(self) -> str:
        """Return the name followed by the presentation, the way listings must show a product."""
        presentation = self.packaging.describe()
        return f"{self.name} ({presentation})" if presentation else self.name


@dataclass(frozen=True, slots=True)
class ProductListing:
    """A product together with the price observed for it during a scrape."""

    product: Product
    price: Price


@dataclass(frozen=True, slots=True)
class ProductDetails:
    """Rich product attributes only available from a product detail endpoint."""

    supermarket: SupermarketCode
    external_id: str
    ean: str | None = None
    brand: str | None = None
    origin: str | None = None
    legal_name: str | None = None
    description: str | None = None
    ingredients: str | None = None
    allergens: str | None = None
    image_url: str | None = None

    @property
    def gtin(self) -> str | None:
        """Return the EAN as a 14-digit GTIN, or ``None`` if it is missing or invalid."""
        return normalize_gtin(self.ean)


@dataclass(frozen=True, slots=True)
class PriceObservation:
    """A price recorded for a product on a specific calendar day."""

    supermarket: SupermarketCode
    product_external_id: str
    captured_on: date
    captured_at: datetime
    price: Price


@dataclass(frozen=True, slots=True)
class ScrapeFailure:
    """A non-fatal error isolated to one part of a catalog (e.g. a category)."""

    scope: str
    reason: str


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    """Normalized result of fully traversing a supermarket catalog once."""

    supermarket: Supermarket
    categories: tuple[Category, ...]
    listings: tuple[ProductListing, ...]
    failures: tuple[ScrapeFailure, ...] = ()

    @property
    def is_complete(self) -> bool:
        """Tell whether every part of the catalog was fetched successfully."""
        return not self.failures

    def deduplicated_listings(self) -> tuple[ProductListing, ...]:
        """Merge listings of the same product seen under several categories."""
        merged: dict[str, ProductListing] = {}
        for listing in self.listings:
            key = listing.product.external_id
            current = merged.get(key)
            if current is None:
                merged[key] = listing
                continue
            categories = tuple(
                dict.fromkeys(
                    current.product.category_external_ids + listing.product.category_external_ids
                )
            )
            merged[key] = replace(
                current, product=replace(current.product, category_external_ids=categories)
            )
        return tuple(merged.values())


class RunStatus(StrEnum):
    """Lifecycle state of a scrape execution."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PriceChangeCounts:
    """How many products changed price on a day, by kind of change."""

    increased: int = 0
    decreased: int = 0
    promotions_started: int = 0
    promotions_ended: int = 0

    @classmethod
    def tally(cls, kinds: Iterable[PriceChangeKind]) -> PriceChangeCounts:
        """Count each kind of change in ``kinds``."""
        counter = Counter(kinds)
        return cls(
            increased=counter[PriceChangeKind.INCREASED],
            decreased=counter[PriceChangeKind.DECREASED],
            promotions_started=counter[PriceChangeKind.PROMOTION_STARTED],
            promotions_ended=counter[PriceChangeKind.PROMOTION_ENDED],
        )

    @property
    def total(self) -> int:
        """Return how many changes there were of any kind."""
        return self.increased + self.decreased + self.promotions_started + self.promotions_ended


@dataclass(frozen=True, slots=True)
class RunStats:
    """Counters describing what a scrape run changed in the catalog.

    ``price_change_kinds`` breaks ``price_changes`` down by kind. It is ``None`` for
    runs recorded before the breakdown existed, which never had it.
    """

    categories: int = 0
    products: int = 0
    new_products: int = 0
    delisted_products: int = 0
    price_changes: int = 0
    details_enriched: int = 0
    errors: int = 0
    price_change_kinds: PriceChangeCounts | None = None


@dataclass(frozen=True, slots=True)
class ScrapeRun:
    """Audit record of one execution of the downloader for one supermarket."""

    id: str
    supermarket: SupermarketCode
    started_at: datetime
    status: RunStatus = RunStatus.RUNNING
    finished_at: datetime | None = None
    stats: RunStats = field(default_factory=RunStats)
    error_message: str | None = None

    @classmethod
    def start(cls, supermarket: SupermarketCode, at: datetime) -> ScrapeRun:
        """Create a new running execution with a random identifier."""
        return cls(id=str(uuid.uuid4()), supermarket=supermarket, started_at=at)

    def complete(self, at: datetime, stats: RunStats) -> ScrapeRun:
        """Close the run as succeeded, or partial when some failures were isolated."""
        status = RunStatus.PARTIAL if stats.errors else RunStatus.SUCCEEDED
        return replace(self, status=status, finished_at=at, stats=stats)

    def fail(self, at: datetime, error: str, stats: RunStats | None = None) -> ScrapeRun:
        """Close the run as failed, keeping whatever statistics were gathered."""
        return replace(
            self,
            status=RunStatus.FAILED,
            finished_at=at,
            error_message=error[:2000],
            stats=stats or self.stats,
        )

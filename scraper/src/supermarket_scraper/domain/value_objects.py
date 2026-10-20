from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final, override

from supermarket_scraper.domain.exceptions import InvalidEntityError

_SUPERMARKET_CODE: Final = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_CURRENCY: Final = re.compile(r"^[A-Z]{3}$")
_ZERO: Final = Decimal(0)
_HUNDRED: Final = Decimal(100)


class MeasureUnit(StrEnum):
    """Normalized unit in which reference prices are expressed."""

    KILOGRAM = "kg"
    LITRE = "l"
    UNIT = "unit"
    DOZEN = "dozen"
    METRE = "m"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, raw: str | None) -> MeasureUnit:
        """Map a free-text unit label coming from any supermarket to a normalized unit."""
        if raw is None:
            return cls.UNKNOWN
        token = raw.strip().lower().rstrip(".")
        return _UNIT_ALIASES.get(token, cls.UNKNOWN)


_UNIT_ALIASES: Final[dict[str, MeasureUnit]] = {
    "kg": MeasureUnit.KILOGRAM,
    "kilo": MeasureUnit.KILOGRAM,
    "kilogramo": MeasureUnit.KILOGRAM,
    "l": MeasureUnit.LITRE,
    "lt": MeasureUnit.LITRE,
    "litro": MeasureUnit.LITRE,
    "ud": MeasureUnit.UNIT,
    "uds": MeasureUnit.UNIT,
    "unidad": MeasureUnit.UNIT,
    "unit": MeasureUnit.UNIT,
    "docena": MeasureUnit.DOZEN,
    "dozen": MeasureUnit.DOZEN,
    "m": MeasureUnit.METRE,
    "metro": MeasureUnit.METRE,
}


class SellingMethod(StrEnum):
    """How a product is sold at the shelf."""

    UNIT = "unit"
    WEIGHT = "weight"
    BUNCH = "bunch"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SupermarketCode:
    """Stable, slug-like identifier of a supermarket chain (e.g. ``mercadona``)."""

    value: str

    def __post_init__(self) -> None:
        """Validate the slug format."""
        if not _SUPERMARKET_CODE.fullmatch(self.value):
            msg = f"Invalid supermarket code: {self.value!r}"
            raise InvalidEntityError(msg)

    @override
    def __str__(self) -> str:
        """Return the raw code."""
        return self.value


@dataclass(frozen=True, slots=True)
class Price:
    """Shelf price observed for a product, plus its comparable reference price."""

    amount: Decimal
    currency: str = "EUR"
    reference_amount: Decimal | None = None
    reference_unit: MeasureUnit = MeasureUnit.UNKNOWN
    previous_amount: Decimal | None = None
    bulk_amount: Decimal | None = None
    vat_rate: Decimal | None = None
    is_promotion: bool = False

    def __post_init__(self) -> None:
        """Enforce non-negative amounts and a valid ISO-4217 currency."""
        if not _CURRENCY.fullmatch(self.currency):
            msg = f"Invalid currency code: {self.currency!r}"
            raise InvalidEntityError(msg)
        for label, value in (
            ("amount", self.amount),
            ("reference_amount", self.reference_amount),
            ("previous_amount", self.previous_amount),
            ("bulk_amount", self.bulk_amount),
            ("vat_rate", self.vat_rate),
        ):
            if value is not None and value < _ZERO:
                msg = f"Price {label} cannot be negative: {value}"
                raise InvalidEntityError(msg)

    @property
    def discount_percentage(self) -> Decimal | None:
        """Return the discount versus the previous price, if the product is on promotion."""
        if self.previous_amount is None or self.previous_amount <= self.amount:
            return None
        ratio = (self.previous_amount - self.amount) / self.previous_amount
        return (ratio * _HUNDRED).quantize(Decimal("0.01"))

    def same_shelf_price(self, other: Price) -> bool:
        """Tell whether two observations carry the same price the customer would pay."""
        return (
            self.amount == other.amount
            and self.currency == other.currency
            and self.is_promotion == other.is_promotion
        )

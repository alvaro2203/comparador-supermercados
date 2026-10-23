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
_ONE: Final = Decimal(1)
_HUNDRED: Final = Decimal(100)
_GTIN_CANDIDATE: Final = re.compile(r"^[0-9]{8,14}$")
GTIN_LENGTH: Final = 14


def normalize_gtin(code: str | None) -> str | None:
    """Return a barcode as a 14-digit GTIN, or ``None`` if it is not a valid one.

    EAN-8, UPC-A (12 digits, often published without its leading zero), EAN-13
    and GTIN-14 are one numbering scheme of different widths: left-padding with
    zeros gives every code the same 14-digit form, so the same product matches
    across chains however each one formats it. Leading zeros do not change the
    check digit, which is verified so that a mistyped code never matches an
    unrelated product.
    """
    if code is None or not _GTIN_CANDIDATE.fullmatch(code):
        return None
    gtin = code.zfill(GTIN_LENGTH)
    *body, check = (int(digit) for digit in gtin)
    weighted = sum(digit * (3 if position % 2 == 0 else 1) for position, digit in enumerate(body))
    return gtin if (10 - weighted % 10) % 10 == check else None


class MeasureUnit(StrEnum):
    """Normalized unit in which reference prices are expressed."""

    KILOGRAM = "kg"
    LITRE = "l"
    UNIT = "unit"
    DOZEN = "dozen"
    METRE = "m"
    WASH = "wash"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, raw: str | None) -> MeasureUnit:
        """Map a free-text unit label coming from any supermarket to a normalized unit."""
        if raw is None:
            return cls.UNKNOWN
        token = raw.strip().lower().rstrip(".")
        return _UNIT_ALIASES.get(token, cls.UNKNOWN)

    @classmethod
    def parse_quantity(cls, raw: str | None) -> tuple[Decimal, MeasureUnit]:
        """Split a reference format such as ``100 g`` into a quantity of a base unit.

        ``100 g`` is ``(0.1, KILOGRAM)``, ``75 cl`` is ``(0.75, LITRE)``, and both
        ``12 ud`` and a dozen (``dz``) are ``(12, UNIT)``. Dividing a reference price by
        the quantity gives a price per kilogram, litre, unit, metre or wash (detergents
        are quoted per ``lv``, lavado), comparable across products and chains whatever
        size each one quotes. Anything unrecognised is ``(1, UNKNOWN)``.
        """
        match = _REFERENCE_FORMAT.fullmatch(raw.strip().lower()) if raw else None
        if match is None:
            return _ONE, cls.UNKNOWN
        amount, token = match.groups()
        quantity = Decimal(amount.replace(",", ".")) if amount else _ONE
        token = token.rstrip(".")
        if token in _SUB_UNITS:
            unit, factor = _SUB_UNITS[token]
            quantity *= factor
        else:
            unit = _UNIT_ALIASES.get(token, cls.UNKNOWN)
        if unit is cls.UNKNOWN or quantity <= _ZERO:
            return _ONE, cls.UNKNOWN
        return quantity.normalize(), unit


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
    "lv": MeasureUnit.WASH,
    "lavado": MeasureUnit.WASH,
    "lavados": MeasureUnit.WASH,
}

_SUB_UNITS: Final[dict[str, tuple[MeasureUnit, Decimal]]] = {
    "g": (MeasureUnit.KILOGRAM, Decimal("0.001")),
    "gr": (MeasureUnit.KILOGRAM, Decimal("0.001")),
    "gramo": (MeasureUnit.KILOGRAM, Decimal("0.001")),
    "ml": (MeasureUnit.LITRE, Decimal("0.001")),
    "cl": (MeasureUnit.LITRE, Decimal("0.01")),
    "cm": (MeasureUnit.METRE, Decimal("0.01")),
    "dz": (MeasureUnit.UNIT, Decimal(12)),
    "dc": (MeasureUnit.UNIT, Decimal(12)),
    "docena": (MeasureUnit.UNIT, Decimal(12)),
}
_REFERENCE_FORMAT: Final = re.compile(r"(?:([0-9]+(?:[.,][0-9]+)?)\s*)?([a-z]+\.?)")


class PriceChangeKind(StrEnum):
    """Why the shelf price of a product differs from its previous observation.

    Entering or leaving a promotion is told apart from a change of the regular
    price, so a temporary discount is never mistaken for a price cut.
    """

    INCREASED = "increased"
    DECREASED = "decreased"
    PROMOTION_STARTED = "promotion_started"
    PROMOTION_ENDED = "promotion_ended"


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

    def change_from(self, previous: Price) -> PriceChangeKind | None:
        """Classify how this price differs from ``previous``, or ``None`` if it does not.

        A promotion starting or ending takes precedence over the amount. Otherwise
        the direction of the amount decides, also for a promotion whose price moved.
        """
        if self.same_shelf_price(previous):
            return None
        if self.is_promotion and not previous.is_promotion:
            return PriceChangeKind.PROMOTION_STARTED
        if previous.is_promotion and not self.is_promotion:
            return PriceChangeKind.PROMOTION_ENDED
        if self.amount > previous.amount:
            return PriceChangeKind.INCREASED
        return PriceChangeKind.DECREASED

    def same_shelf_price(self, other: Price) -> bool:
        """Tell whether two observations carry the same price the customer would pay."""
        return (
            self.amount == other.amount
            and self.currency == other.currency
            and self.is_promotion == other.is_promotion
        )

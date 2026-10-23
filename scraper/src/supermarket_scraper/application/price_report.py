from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from supermarket_scraper.domain.entities import PriceChangeCounts
from supermarket_scraper.domain.exceptions import NoPriceDataError
from supermarket_scraper.domain.value_objects import MeasureUnit, PriceChangeKind

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from datetime import date

    from supermarket_scraper.domain.entities import Product
    from supermarket_scraper.domain.repositories import UnitOfWork
    from supermarket_scraper.domain.value_objects import Price, SupermarketCode

_HUNDRED: Final = Decimal(100)
_CENT: Final = Decimal("0.01")
DEFAULT_TOP: Final = 10


@dataclass(frozen=True, slots=True)
class PriceMove:
    """A product whose shelf price differs between two days."""

    product: Product
    before: Price
    after: Price

    @property
    def change_percentage(self) -> Decimal:
        """Return the relative change of the shelf price, in percent with two decimals."""
        if not self.before.amount:
            return Decimal(0)
        ratio = (self.after.amount - self.before.amount) / self.before.amount
        return (ratio * _HUNDRED).quantize(_CENT)


@dataclass(frozen=True, slots=True)
class PriceReport:
    """What changed in a supermarket's prices and catalog on one day versus the day before.

    Counts refer to the products priced on ``day``. ``products_with_gtin`` and
    ``new_arrivals`` use the products' current data, since neither is historized.
    Without a previous day, there is nothing to compare: the change fields are
    ``None`` or empty.
    """

    supermarket: SupermarketCode
    day: date
    previous_day: date | None
    active_products: int
    products_with_gtin: int
    new_arrivals: int
    promotions_active: int
    unknown_reference_units: int
    price_change_kinds: PriceChangeCounts | None
    top_increases: tuple[PriceMove, ...]
    top_decreases: tuple[PriceMove, ...]
    promotions_started: tuple[PriceMove, ...]
    new_products: tuple[Product, ...]
    delisted_products: tuple[Product, ...]


class BuildPriceReportUseCase:
    """Compare one day of stored prices with the previous captured day. Read-only."""

    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        """Wire the use case with the store it reads from."""
        self._uow_factory = uow_factory

    def execute(
        self, supermarket: SupermarketCode, *, day: date | None = None, top: int = DEFAULT_TOP
    ) -> PriceReport:
        """Build the report for ``day`` (default: the latest captured day).

        Raises:
            NoPriceDataError: The supermarket has no stored prices, or none on ``day``.
        """
        with self._uow_factory() as uow:
            days = list(uow.prices.captured_days(supermarket))
            if not days:
                msg = f"No prices stored for {supermarket}"
                raise NoPriceDataError(msg)
            target = days[0] if day is None else day
            if target not in days:
                msg = f"No prices stored for {supermarket} on {target.isoformat()}"
                raise NoPriceDataError(msg)
            earlier = [captured for captured in days if captured < target]
            previous_day = earlier[0] if earlier else None
            current = uow.prices.prices_on(supermarket, target)
            previous = uow.prices.prices_on(supermarket, previous_day) if previous_day else {}
            products = {
                product.external_id: product
                for product in uow.products.find(supermarket, current.keys() | previous.keys())
            }
        priced = [products[key] for key in current]
        moves = _moves(current, previous, products) if previous_day else []
        return PriceReport(
            supermarket=supermarket,
            day=target,
            previous_day=previous_day,
            active_products=len(current),
            products_with_gtin=sum(product.gtin is not None for product in priced),
            new_arrivals=sum(product.is_new_arrival for product in priced),
            promotions_active=sum(price.is_promotion for price in current.values()),
            unknown_reference_units=sum(
                price.reference_unit is MeasureUnit.UNKNOWN for price in current.values()
            ),
            price_change_kinds=(
                PriceChangeCounts.tally(kind for kind, _ in moves) if previous_day else None
            ),
            top_increases=_top(moves, PriceChangeKind.INCREASED, top, largest_first=True),
            top_decreases=_top(moves, PriceChangeKind.DECREASED, top, largest_first=False),
            promotions_started=_top(
                moves, PriceChangeKind.PROMOTION_STARTED, top, largest_first=False
            ),
            new_products=_by_label(
                products[key] for key in current if previous_day and key not in previous
            ),
            delisted_products=_by_label(products[key] for key in previous if key not in current),
        )


def _moves(
    current: Mapping[str, Price], previous: Mapping[str, Price], products: Mapping[str, Product]
) -> list[tuple[PriceChangeKind, PriceMove]]:
    """Classify every product priced on both days whose shelf price changed."""
    moves: list[tuple[PriceChangeKind, PriceMove]] = []
    for key, after in current.items():
        before = previous.get(key)
        kind = after.change_from(before) if before is not None else None
        if before is not None and kind is not None:
            moves.append((kind, PriceMove(products[key], before, after)))
    return moves


def _top(
    moves: Iterable[tuple[PriceChangeKind, PriceMove]],
    kind: PriceChangeKind,
    limit: int,
    *,
    largest_first: bool,
) -> tuple[PriceMove, ...]:
    """Return the ``limit`` moves of one kind with the largest relative change."""
    selected = [move for move_kind, move in moves if move_kind is kind]
    selected.sort(
        key=lambda move: (move.change_percentage, move.product.label), reverse=largest_first
    )
    return tuple(selected[:limit])


def _by_label(products: Iterable[Product]) -> tuple[Product, ...]:
    """Sort products by their label, the way people read a listing."""
    return tuple(sorted(products, key=lambda product: product.label))

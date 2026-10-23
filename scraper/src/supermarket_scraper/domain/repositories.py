from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence
    from datetime import datetime
    from types import TracebackType

    from supermarket_scraper.domain.entities import (
        Category,
        PriceChangeCounts,
        PriceObservation,
        Product,
        ProductDetails,
        ScrapeRun,
        Supermarket,
    )
    from supermarket_scraper.domain.value_objects import SupermarketCode


@dataclass(frozen=True, slots=True)
class ProductUpsertResult:
    """Outcome of synchronizing a batch of products with the store."""

    inserted: int
    updated: int
    new_external_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PriceRecordResult:
    """Outcome of recording a batch of daily price observations."""

    recorded: int
    kinds: PriceChangeCounts

    @property
    def changed(self) -> int:
        """Return how many products changed price versus their previous observation."""
        return self.kinds.total


class SupermarketRepository(ABC):
    """Persistence contract for supermarket chains."""

    @abstractmethod
    def upsert(self, supermarket: Supermarket) -> None:
        """Insert the supermarket or refresh its attributes."""

    @abstractmethod
    def list_all(self) -> Sequence[Supermarket]:
        """Return every known supermarket."""


class CategoryRepository(ABC):
    """Persistence contract for category trees."""

    @abstractmethod
    def sync(
        self, supermarket: SupermarketCode, categories: Sequence[Category], seen_at: datetime
    ) -> int:
        """Upsert the given categories and return how many were written."""

    @abstractmethod
    def deactivate_missing(
        self, supermarket: SupermarketCode, seen_external_ids: Collection[str], at: datetime
    ) -> int:
        """Flag active categories absent from the latest catalog and return how many."""


class ProductRepository(ABC):
    """Persistence contract for catalog products."""

    @abstractmethod
    def sync(
        self, supermarket: SupermarketCode, products: Sequence[Product], seen_at: datetime
    ) -> ProductUpsertResult:
        """Upsert products, link them to categories and report which ones are new."""

    @abstractmethod
    def count_active(self, supermarket: SupermarketCode) -> int:
        """Return how many products are currently listed for the supermarket."""

    @abstractmethod
    def delist_missing(
        self, supermarket: SupermarketCode, seen_external_ids: Collection[str], at: datetime
    ) -> int:
        """Mark active products absent from the latest catalog as delisted."""

    @abstractmethod
    def external_ids_missing_details(
        self, supermarket: SupermarketCode, limit: int
    ) -> Sequence[str]:
        """Return active products that were never enriched with detail data."""

    @abstractmethod
    def apply_details(self, details: Sequence[ProductDetails], at: datetime) -> int:
        """Store detail attributes and return how many products were enriched."""

    @abstractmethod
    def link_successors(self, supermarket: SupermarketCode) -> int:
        """Link delisted products to the product that replaced them and return how many.

        A successor is the only active product with the same GTIN that was first
        seen after the delisted one was last seen: the same article relisted under
        a new external id. Products sharing a GTIN while both listed (for example a
        loaf sold whole and sliced) are siblings, never successors. A product that
        comes back to the catalog loses its link.
        """


class PriceRepository(ABC):
    """Persistence contract for the historical price series."""

    @abstractmethod
    def record(self, observations: Sequence[PriceObservation]) -> PriceRecordResult:
        """Store one observation per product and day, idempotently for re-runs."""


class ScrapeRunRepository(ABC):
    """Persistence contract for the audit log of executions."""

    @abstractmethod
    def save(self, run: ScrapeRun) -> None:
        """Insert or update a run record."""

    @abstractmethod
    def latest(self, supermarket: SupermarketCode, limit: int) -> Sequence[ScrapeRun]:
        """Return the most recent runs for a supermarket, newest first."""


class UnitOfWork(ABC):
    """Transactional boundary grouping every repository over a single connection."""

    supermarkets: SupermarketRepository
    categories: CategoryRepository
    products: ProductRepository
    prices: PriceRepository
    runs: ScrapeRunRepository

    @abstractmethod
    def __enter__(self) -> Self:
        """Open a transaction and bind the repositories to it."""

    @abstractmethod
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Roll back any uncommitted work and release the connection."""

    @abstractmethod
    def commit(self) -> None:
        """Persist every change made inside the transaction."""

    @abstractmethod
    def rollback(self) -> None:
        """Discard every change made inside the transaction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date, datetime
    from types import TracebackType

    from supermarket_scraper.domain.entities import (
        CatalogSnapshot,
        ProductDetails,
        ScrapeFailure,
        Supermarket,
    )


@dataclass(frozen=True, slots=True)
class DetailsBatch:
    """Result of enriching a set of products with their detail pages."""

    details: tuple[ProductDetails, ...]
    failures: tuple[ScrapeFailure, ...] = ()


class CatalogSource(ABC):
    """Contract every supermarket integration must fulfil to feed the downloader."""

    @property
    @abstractmethod
    def supermarket(self) -> Supermarket:
        """Return the supermarket this source extracts data from."""

    @abstractmethod
    async def fetch_catalog(self) -> CatalogSnapshot:
        """Traverse the whole catalog and return normalized categories, products and prices."""

    @abstractmethod
    async def fetch_product_details(self, external_ids: Sequence[str]) -> DetailsBatch:
        """Fetch rich attributes (EAN, brand, ingredients...) for the given products."""

    @abstractmethod
    async def aclose(self) -> None:
        """Release network resources held by the source."""

    async def __aenter__(self) -> Self:
        """Enter the async context, returning the source itself."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the source when leaving the async context."""
        await self.aclose()


class Clock(ABC):
    """Abstraction over the system time so that use cases are deterministic in tests."""

    @abstractmethod
    def now(self) -> datetime:
        """Return the current timezone-aware instant in UTC."""

    @abstractmethod
    def today(self) -> date:
        """Return the current calendar day in the business timezone."""

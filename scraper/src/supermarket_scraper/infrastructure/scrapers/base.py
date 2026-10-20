from __future__ import annotations

import asyncio
import logging
from abc import abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, override

from supermarket_scraper.application.ports import CatalogSource, DetailsBatch
from supermarket_scraper.domain.entities import (
    CatalogSnapshot,
    Category,
    ProductDetails,
    ProductListing,
    ScrapeFailure,
)
from supermarket_scraper.domain.exceptions import DomainError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from supermarket_scraper.infrastructure.http.client import AsyncJsonClient

logger: Final = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CategoryIndex[PageRefT]:
    """Categories discovered from the catalog entry point plus the pages left to crawl."""

    categories: tuple[Category, ...]
    pages: tuple[PageRefT, ...]


@dataclass(frozen=True, slots=True)
class CategorizedItem[RawProductT]:
    """A raw product payload together with the category it was listed under."""

    category_external_id: str
    item: RawProductT


@dataclass(frozen=True, slots=True)
class CategoryPage[RawProductT]:
    """Parsed content of one crawled catalog page."""

    categories: tuple[Category, ...]
    products: tuple[CategorizedItem[RawProductT], ...]


@dataclass(frozen=True, slots=True)
class _PageOutcome:
    """Normalized result of crawling one page, including isolated failures."""

    categories: tuple[Category, ...] = ()
    listings: tuple[ProductListing, ...] = ()
    failures: tuple[ScrapeFailure, ...] = ()


class BaseSupermarketScraper[PageRefT, RawProductT, RawDetailT](CatalogSource):
    """Template for HTTP/JSON supermarket integrations.

    Subclasses implement three small stages for every resource type:

    * **fetch** - perform the network call and return the raw payload;
    * **parse** - validate the raw payload into typed source-specific models;
    * **normalize** - translate those models into domain entities.

    The base class owns the orchestration: concurrency, error isolation per
    page and per product, de-duplication and failure reporting. Adding a new
    supermarket therefore never requires touching this class.
    """

    def __init__(self, http: AsyncJsonClient) -> None:
        """Keep the HTTP client used by every fetch stage."""
        self._http = http

    @abstractmethod
    async def fetch_index(self) -> object:
        """Download the catalog entry point (usually the category tree)."""

    @abstractmethod
    def parse_index(self, payload: object) -> CategoryIndex[PageRefT]:
        """Extract the top-level categories and the pages to crawl from the entry point."""

    @abstractmethod
    async def fetch_page(self, ref: PageRefT) -> object:
        """Download one catalog page."""

    @abstractmethod
    def parse_page(self, ref: PageRefT, payload: object) -> CategoryPage[RawProductT]:
        """Extract nested categories and raw products from a catalog page."""

    @abstractmethod
    def normalize_product(self, raw: RawProductT, category_external_id: str) -> ProductListing:
        """Translate a raw product into a domain product with its current price."""

    @abstractmethod
    def describe_page(self, ref: PageRefT) -> str:
        """Return a human-readable identifier of a page, used in failure reports."""

    @abstractmethod
    async def fetch_detail(self, external_id: str) -> object:
        """Download the detail document of a single product."""

    @abstractmethod
    def parse_detail(self, payload: object) -> RawDetailT:
        """Validate a product detail payload."""

    @abstractmethod
    def normalize_detail(self, raw: RawDetailT) -> ProductDetails:
        """Translate a raw product detail into domain detail attributes."""

    @override
    async def fetch_catalog(self) -> CatalogSnapshot:
        """Crawl the entry point and every page concurrently, isolating page failures."""
        index = self.parse_index(await self.fetch_index())
        logger.info(
            "Category index fetched",
            extra={"supermarket": str(self.supermarket.code), "pages": len(index.pages)},
        )
        outcomes = await asyncio.gather(*(self._crawl_page(ref) for ref in index.pages))
        categories = list(index.categories)
        listings: list[ProductListing] = []
        failures: list[ScrapeFailure] = []
        for outcome in outcomes:
            categories.extend(outcome.categories)
            listings.extend(outcome.listings)
            failures.extend(outcome.failures)
        return CatalogSnapshot(
            supermarket=self.supermarket,
            categories=tuple({category.external_id: category for category in categories}.values()),
            listings=tuple(listings),
            failures=tuple(failures),
        )

    @override
    async def fetch_product_details(self, external_ids: Sequence[str]) -> DetailsBatch:
        """Fetch and normalize detail pages concurrently, isolating per-product failures."""
        results = await asyncio.gather(*(self._detail(external_id) for external_id in external_ids))
        details = tuple(item for item in results if isinstance(item, ProductDetails))
        failures = tuple(item for item in results if isinstance(item, ScrapeFailure))
        return DetailsBatch(details=details, failures=failures)

    @override
    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._http.aclose()

    async def _crawl_page(self, ref: PageRefT) -> _PageOutcome:
        """Fetch, parse and normalize one page without letting errors escape."""
        scope = self.describe_page(ref)
        try:
            page = self.parse_page(ref, await self.fetch_page(ref))
        except DomainError as exc:
            logger.warning("Page failed", extra={"scope": scope, "error": str(exc)})
            return _PageOutcome(failures=(ScrapeFailure(scope=scope, reason=str(exc)),))
        listings: list[ProductListing] = []
        failures: list[ScrapeFailure] = []
        for entry in page.products:
            try:
                listings.append(self.normalize_product(entry.item, entry.category_external_id))
            except DomainError as exc:
                failures.append(ScrapeFailure(scope=f"{scope} / product", reason=str(exc)))
        return _PageOutcome(
            categories=page.categories, listings=tuple(listings), failures=tuple(failures)
        )

    async def _detail(self, external_id: str) -> ProductDetails | ScrapeFailure:
        """Fetch one product detail, returning a failure record instead of raising."""
        try:
            return self.normalize_detail(self.parse_detail(await self.fetch_detail(external_id)))
        except DomainError as exc:
            return ScrapeFailure(scope=f"product {external_id}", reason=str(exc))

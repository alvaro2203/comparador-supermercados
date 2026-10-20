from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from supermarket_scraper.domain.exceptions import UnknownSupermarketError

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

    from supermarket_scraper.application.ports import CatalogSource
    from supermarket_scraper.infrastructure.http.client import HttpClientConfig


@dataclass(frozen=True, slots=True)
class ScraperContext:
    """Shared infrastructure handed to every scraper factory."""

    http: HttpClientConfig
    transport: httpx.AsyncBaseTransport | None = None


class ScraperFactory(Protocol):
    """Callable able to build a ready-to-use catalog source."""

    def __call__(self, context: ScraperContext, /) -> CatalogSource:
        """Build the catalog source."""
        ...


class BuildableScraper(Protocol):
    """Structural type of a scraper class that can build itself from a context."""

    def from_context(self, context: ScraperContext, /) -> CatalogSource:
        """Build an instance from the shared context."""
        ...


class ScraperRegistry:
    """Catalog of available supermarket integrations, keyed by supermarket code."""

    def __init__(self) -> None:
        """Start with no registered integrations."""
        self._factories: dict[str, ScraperFactory] = {}

    def register(self, code: str, factory: ScraperFactory) -> None:
        """Register a factory, refusing silent overrides of an existing code."""
        if code in self._factories:
            msg = f"A scraper is already registered for {code!r}"
            raise ValueError(msg)
        self._factories[code] = factory

    def create(self, code: str, context: ScraperContext) -> CatalogSource:
        """Instantiate the scraper registered for ``code``."""
        try:
            factory = self._factories[code]
        except KeyError:
            known = ", ".join(self.codes()) or "none"
            msg = f"No scraper registered for {code!r} (available: {known})"
            raise UnknownSupermarketError(msg) from None
        return factory(context)

    def codes(self) -> tuple[str, ...]:
        """Return the registered supermarket codes in alphabetical order."""
        return tuple(sorted(self._factories))


default_registry: Final = ScraperRegistry()


def register_scraper[C: BuildableScraper](
    code: str, registry: ScraperRegistry = default_registry
) -> Callable[[C], C]:
    """Class decorator that registers a scraper under ``code`` when its module is imported."""

    def decorator(scraper_class: C) -> C:
        registry.register(code, scraper_class.from_context)
        return scraper_class

    return decorator


def discover_scrapers(package_name: str = "supermarket_scraper.infrastructure.scrapers") -> None:
    """Import every module below ``package_name`` so their decorators register them."""
    package = importlib.import_module(package_name)
    for module in pkgutil.walk_packages(package.__path__, prefix=f"{package_name}."):
        importlib.import_module(module.name)

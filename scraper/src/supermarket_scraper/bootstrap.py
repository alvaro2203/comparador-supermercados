from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from supermarket_scraper.application.sync_catalog import SyncCatalogUseCase, SyncOptions
from supermarket_scraper.infrastructure.clock import SystemClock
from supermarket_scraper.infrastructure.http.client import HttpClientConfig
from supermarket_scraper.infrastructure.persistence.database import build_engine
from supermarket_scraper.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from supermarket_scraper.infrastructure.scrapers.registry import (
    ScraperContext,
    ScraperRegistry,
    default_registry,
    discover_scrapers,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy import Engine

    from supermarket_scraper.config import Settings
    from supermarket_scraper.domain.repositories import UnitOfWork


@dataclass(frozen=True, slots=True)
class Container:
    """Fully wired object graph for one process (composition root)."""

    settings: Settings
    engine: Engine
    registry: ScraperRegistry
    scraper_context: ScraperContext
    uow_factory: Callable[[], UnitOfWork]
    clock: SystemClock

    def sync_use_case(
        self, *, dry_run: bool = False, fetch_details: bool | None = None
    ) -> SyncCatalogUseCase:
        """Build the catalog synchronization use case with the configured options."""
        options = SyncOptions(
            fetch_details=self.settings.fetch_details if fetch_details is None else fetch_details,
            details_limit=self.settings.details_limit,
            max_delist_ratio=self.settings.max_delist_ratio,
            dry_run=dry_run,
        )
        return SyncCatalogUseCase(self.uow_factory, self.clock, options)


def build_container(settings: Settings) -> Container:
    """Instantiate infrastructure adapters and register every available scraper."""
    engine = build_engine(settings.database_url, echo=settings.database_echo)
    discover_scrapers()
    http = HttpClientConfig(
        timeout_seconds=settings.http_timeout_seconds,
        max_retries=settings.http_max_retries,
        backoff_base_seconds=settings.http_backoff_base_seconds,
        backoff_max_seconds=settings.http_backoff_max_seconds,
        max_concurrency=settings.http_max_concurrency,
        min_interval_seconds=settings.http_min_interval_seconds,
        user_agent=settings.http_user_agent,
    )
    return Container(
        settings=settings,
        engine=engine,
        registry=default_registry,
        scraper_context=ScraperContext(http=http),
        uow_factory=partial(SqlAlchemyUnitOfWork, engine),
        clock=SystemClock(settings.timezone),
    )

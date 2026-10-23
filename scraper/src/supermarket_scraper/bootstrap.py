from __future__ import annotations

from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING

import httpx

from supermarket_scraper.application.enrich_details import EnrichDetailsUseCase, EnrichOptions
from supermarket_scraper.application.price_report import BuildPriceReportUseCase
from supermarket_scraper.application.sync_catalog import SyncCatalogUseCase, SyncOptions
from supermarket_scraper.infrastructure.clock import FrozenClock, SystemClock
from supermarket_scraper.infrastructure.http.archive import (
    Manifest,
    RecordingTransport,
    ReplayTransport,
    read_manifest,
    write_manifest,
)
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
    from pathlib import Path

    from sqlalchemy import Engine

    from supermarket_scraper.application.ports import Clock
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
    clock: Clock

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

    def report_use_case(self) -> BuildPriceReportUseCase:
        """Build the read-only price report use case."""
        return BuildPriceReportUseCase(self.uow_factory)

    def enrich_use_case(self, *, limit: int | None = None) -> EnrichDetailsUseCase:
        """Build the detail backfill use case, enriching every pending product unless limited."""
        return EnrichDetailsUseCase(self.uow_factory, self.clock, EnrichOptions(limit=limit))

    def raw_archive_dir(self, code: str) -> Path:
        """Return where today's raw JSON of ``code`` is archived: ``<raw_dir>/<code>/<day>``."""
        return self.settings.raw_dir / code / self.clock.today().isoformat()

    def recording(self, code: str) -> Container:
        """Return a copy whose scrapers also save every successful response to an archive.

        The archive directory and its manifest are created right away. Requests still go
        to the network with the usual pacing; recording adds no request.
        """
        root = self.raw_archive_dir(code)
        write_manifest(root, Manifest(supermarket=code, captured_at=self.clock.now()))
        http = self.scraper_context.http
        inner = self.scraper_context.transport or httpx.AsyncHTTPTransport(
            limits=httpx.Limits(max_connections=http.max_concurrency)
        )
        context = replace(self.scraper_context, transport=RecordingTransport(inner, root))
        return replace(self, scraper_context=context)

    def replaying(self, root: Path) -> tuple[str, Container]:
        """Return the archive's supermarket and a copy that serves it instead of the network.

        The clock is frozen at the capture instant, so prices are stored under the day
        they were downloaded. Pacing and retries are off: no request leaves the machine,
        and anything not archived is a 404.
        """
        manifest = read_manifest(root)
        http = replace(self.scraper_context.http, min_interval_seconds=0, max_retries=0)
        context = ScraperContext(http=http, transport=ReplayTransport(root))
        clock = FrozenClock(manifest.captured_at, self.settings.timezone)
        return manifest.supermarket, replace(self, scraper_context=context, clock=clock)


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

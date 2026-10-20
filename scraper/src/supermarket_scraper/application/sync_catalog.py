from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from supermarket_scraper.application.enrich_details import EnrichDetailsUseCase, EnrichOptions
from supermarket_scraper.domain.entities import (
    CatalogSnapshot,
    PriceObservation,
    RunStats,
    ScrapeRun,
)
from supermarket_scraper.domain.exceptions import EmptyCatalogError

if TYPE_CHECKING:
    from collections.abc import Callable

    from supermarket_scraper.application.ports import CatalogSource, Clock
    from supermarket_scraper.domain.repositories import UnitOfWork

logger: Final = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SyncOptions:
    """Tunable behaviour of a catalog synchronization."""

    fetch_details: bool = True
    details_limit: int = 500
    max_delist_ratio: Decimal = Decimal("0.20")
    dry_run: bool = False


@dataclass(frozen=True, slots=True)
class SyncReport:
    """Summary returned to the caller once a synchronization finishes."""

    run: ScrapeRun
    dry_run: bool


class SyncCatalogUseCase:
    """Download a supermarket catalog and merge it into the persistent store.

    The use case is agnostic of both the supermarket being scraped and the
    database engine: it only talks to the ``CatalogSource`` and ``UnitOfWork``
    abstractions.
    """

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        options: SyncOptions | None = None,
    ) -> None:
        """Wire the use case with its collaborators."""
        self._uow_factory = uow_factory
        self._clock = clock
        self._options = options or SyncOptions()

    async def execute(self, source: CatalogSource) -> SyncReport:
        """Run a full synchronization for the given source and return its report."""
        supermarket = source.supermarket
        run = ScrapeRun.start(supermarket.code, self._clock.now())
        log_extra = {"supermarket": str(supermarket.code), "run_id": run.id}
        logger.info("Catalog sync started", extra=log_extra)

        if not self._options.dry_run:
            with self._uow_factory() as uow:
                uow.supermarkets.upsert(supermarket)
                uow.runs.save(run)
                uow.commit()

        stats = RunStats()
        try:
            snapshot = self._ensure_not_empty(await source.fetch_catalog())
            stats = self._snapshot_stats(snapshot)
            if self._options.dry_run:
                return SyncReport(run=run.complete(self._clock.now(), stats), dry_run=True)
            stats = self._persist_snapshot(snapshot, stats)
            if self._options.fetch_details:
                stats = await self._enrich_details(source, stats)
        except Exception as exc:
            failed = run.fail(self._clock.now(), f"{type(exc).__name__}: {exc}", stats)
            logger.exception("Catalog sync failed", extra=log_extra)
            if not self._options.dry_run:
                self._save_run(failed)
            return SyncReport(run=failed, dry_run=self._options.dry_run)

        finished = run.complete(self._clock.now(), stats)
        self._save_run(finished)
        logger.info(
            "Catalog sync finished",
            extra={**log_extra, "status": finished.status.value, "stats": stats},
        )
        return SyncReport(run=finished, dry_run=False)

    @staticmethod
    def _ensure_not_empty(snapshot: CatalogSnapshot) -> CatalogSnapshot:
        """Reject catalogs without products, which always signal a broken source."""
        if not snapshot.listings:
            msg = f"{snapshot.supermarket.code} returned an empty catalog"
            raise EmptyCatalogError(msg)
        return snapshot

    @staticmethod
    def _snapshot_stats(snapshot: CatalogSnapshot) -> RunStats:
        """Compute the counters that can be derived from the snapshot alone."""
        for failure in snapshot.failures:
            logger.warning(
                "Partial scrape failure",
                extra={"scope": failure.scope, "reason": failure.reason},
            )
        return RunStats(
            categories=len(snapshot.categories),
            products=len(snapshot.deduplicated_listings()),
            errors=len(snapshot.failures),
        )

    def _persist_snapshot(self, snapshot: CatalogSnapshot, stats: RunStats) -> RunStats:
        """Write categories, products and prices in a single atomic transaction."""
        code = snapshot.supermarket.code
        now = self._clock.now()
        today = self._clock.today()
        listings = snapshot.deduplicated_listings()
        observations = [
            PriceObservation(
                supermarket=code,
                product_external_id=listing.product.external_id,
                captured_on=today,
                captured_at=now,
                price=listing.price,
            )
            for listing in listings
        ]
        with self._uow_factory() as uow:
            uow.categories.sync(code, snapshot.categories, now)
            active_before = uow.products.count_active(code)
            upsert = uow.products.sync(code, [listing.product for listing in listings], now)
            prices = uow.prices.record(observations)
            delisted = 0
            if self._can_delist(snapshot, active_before, len(listings)):
                seen_products = {listing.product.external_id for listing in listings}
                delisted = uow.products.delist_missing(code, seen_products, now)
                uow.categories.deactivate_missing(
                    code, {category.external_id for category in snapshot.categories}, now
                )
            uow.commit()
        return replace(
            stats,
            new_products=len(upsert.new_external_ids),
            delisted_products=delisted,
            price_changes=prices.changed,
        )

    def _can_delist(self, snapshot: CatalogSnapshot, active_before: int, seen: int) -> bool:
        """Guard against wiping the catalog when the source returned an incomplete view."""
        if not snapshot.is_complete:
            logger.warning("Skipping delisting: catalog scrape was incomplete")
            return False
        if active_before == 0:
            return True
        missing_ratio = Decimal(max(active_before - seen, 0)) / Decimal(active_before)
        if missing_ratio > self._options.max_delist_ratio:
            logger.warning(
                "Skipping delisting: too many products vanished at once",
                extra={"missing_ratio": str(missing_ratio.quantize(Decimal("0.0001")))},
            )
            return False
        return True

    async def _enrich_details(self, source: CatalogSource, stats: RunStats) -> RunStats:
        """Enrich products, never letting an enrichment crash undo the committed catalog.

        Prices and products are already persisted when this step starts, so any
        unexpected error here downgrades the run to partial instead of failed.
        """
        try:
            return await self._fetch_and_store_details(source, stats)
        except Exception:
            logger.exception(
                "Detail enrichment aborted", extra={"supermarket": str(source.supermarket.code)}
            )
            return replace(stats, errors=stats.errors + 1)

    async def _fetch_and_store_details(self, source: CatalogSource, stats: RunStats) -> RunStats:
        """Fetch detail pages for up to ``details_limit`` products never enriched before."""
        enrichment = EnrichDetailsUseCase(
            self._uow_factory, self._clock, EnrichOptions(limit=self._options.details_limit)
        )
        report = await enrichment.execute(source)
        return replace(
            stats, details_enriched=report.enriched, errors=stats.errors + report.failures
        )

    def _save_run(self, run: ScrapeRun) -> None:
        """Persist the final state of a run without letting audit errors mask the result."""
        try:
            with self._uow_factory() as uow:
                uow.runs.save(run)
                uow.commit()
        except Exception:
            logger.exception("Could not persist scrape run", extra={"run_id": run.id})

from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import batched
from typing import TYPE_CHECKING, Final

from supermarket_scraper.domain.entities import RunStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from supermarket_scraper.application.ports import CatalogSource, Clock
    from supermarket_scraper.domain.entities import ScrapeFailure
    from supermarket_scraper.domain.repositories import UnitOfWork
    from supermarket_scraper.domain.value_objects import SupermarketCode

logger: Final = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EnrichOptions:
    """Tunable behaviour of a detail enrichment.

    Attributes:
        limit: Maximum number of products to enrich, or ``None`` for every pending one.
        chunk_size: Products fetched and committed together. A smaller chunk loses
            less work if the process is interrupted.
    """

    limit: int | None = None
    chunk_size: int = 100


@dataclass(frozen=True, slots=True)
class EnrichReport:
    """Summary of a detail enrichment for one supermarket."""

    supermarket: SupermarketCode
    pending: int
    enriched: int
    failures: int
    stopped_early: bool = False
    reasons: tuple[ScrapeFailure, ...] = ()

    @property
    def status(self) -> RunStatus:
        """Classify the outcome: failed if nothing was stored, partial if some products failed."""
        if self.failures and not self.enriched:
            return RunStatus.FAILED
        if self.failures:
            return RunStatus.PARTIAL
        return RunStatus.SUCCEEDED


class EnrichDetailsUseCase:
    """Fetch detail pages (EAN, brand, ingredients...) for products never enriched.

    The pending products are listed once and processed in chunks, each committed
    on its own, so an interrupted backfill keeps what it stored and the next
    execution resumes with what is still pending. A chunk in which every request
    fails stops the work: the source is most likely down, and going through
    thousands of remaining products would only hammer it.
    """

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        options: EnrichOptions | None = None,
    ) -> None:
        """Wire the use case with its collaborators."""
        self._uow_factory = uow_factory
        self._clock = clock
        self._options = options or EnrichOptions()

    async def execute(self, source: CatalogSource) -> EnrichReport:
        """Enrich the pending products of ``source`` and return what happened."""
        code = source.supermarket.code
        with self._uow_factory() as uow:
            limit = self._options.limit
            if limit is None:
                limit = uow.products.count_active(code)
            pending = list(uow.products.external_ids_missing_details(code, limit))
        log_extra = {"supermarket": str(code), "pending": len(pending)}
        enriched = 0
        reasons: list[ScrapeFailure] = []
        for chunk in batched(pending, self._options.chunk_size):
            batch = await source.fetch_product_details(chunk)
            with self._uow_factory() as uow:
                enriched += uow.products.apply_details(batch.details, self._clock.now())
                uow.commit()
            for failure in batch.failures:
                logger.warning(
                    "Detail enrichment failure",
                    extra={**log_extra, "scope": failure.scope, "reason": failure.reason},
                )
            reasons.extend(batch.failures)
            logger.info(
                "Detail enrichment progress",
                extra={**log_extra, "enriched": enriched, "failures": len(reasons)},
            )
            if batch.failures and not batch.details:
                logger.warning("Detail enrichment stopped: a whole chunk failed", extra=log_extra)
                return EnrichReport(
                    code,
                    len(pending),
                    enriched,
                    len(reasons),
                    stopped_early=True,
                    reasons=tuple(reasons),
                )
        return EnrichReport(code, len(pending), enriched, len(reasons), reasons=tuple(reasons))

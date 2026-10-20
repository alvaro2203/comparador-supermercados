---
paths:
  - "scraper/src/supermarket_scraper/application/**/*.py"
---

# Application layer

`SyncCatalogUseCase` orchestrates a run through two ports: `CatalogSource` (any supermarket) and
`UnitOfWork` (any store), plus `Clock`. It must stay ignorant of which chain and which database.

## Boundaries

- Import only from `domain` and from `application` itself. Concrete adapters arrive via the
  constructor, wired in `bootstrap.py`.
- Never call `datetime.now()`: use `self._clock.now()` (UTC instant) and `self._clock.today()`
  (business day in `SCRAPER_TIMEZONE`). Tests depend on `FixedClock`.
- New ports are `ABC`s in `ports.py`, with docstrings that define the contract precisely.

## Run semantics (keep them intact)

- Catalog write is one transaction: categories, products, prices and delisting commit together.
- Detail enrichment runs after that commit and can never turn a stored day into a failure.
  A crash there adds one error and makes the run `partial`.
- `failed` means nothing from this run was stored. `partial` means data was stored with isolated errors.
  The CLI exit codes depend on this distinction.
- An empty catalog is always an error (`EmptyCatalogError`), never "everything was delisted".
- Delisting runs only when the snapshot is complete and the missing ratio is within
  `max_delist_ratio`. Do not bypass or loosen this guard.
- `dry_run` performs no writes at all, including the `scrape_runs` audit row.
- The audit row is saved at start and at the end. A failure to save it is logged, never raised.

## When adding behaviour

- New counters go in `RunStats`, the `scrape_runs` table, the repository mapping and the CLI report.
- Options go in `SyncOptions` and are filled from `Settings` in `bootstrap.py`, never read from env here.

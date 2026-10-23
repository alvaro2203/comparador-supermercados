# Comparador de supermercados

Price comparator for Spanish supermarkets. Only the **scraper** exists so far, in `scraper/`: a daily
downloader that walks each chain's catalog and stores one price snapshot per product per day. A REST
API and a comparator UI are planned. The API will reuse `domain` and `infrastructure/persistence`
from the scraper, and will match the same product across chains by `gtin` (the EAN normalized to 14 digits).

Mercadona is the only integration. It uses the unofficial JSON API behind `tienda.mercadona.es`.
Prices are tracked for the Madrid warehouse (`MERCADONA_WAREHOUSE=mad1`) only, by decision.
Details are in [scraper/README.md](scraper/README.md). Read it before architectural changes. This
file only holds what you need to work without re-deriving it.

## Where to work

All tooling (uv, ruff, mypy, pytest) runs from `scraper/`, not from the repo root.
The repo root has no `pyproject.toml`.

Environment: Windows 10, PowerShell. Python >= 3.12, managed with [uv](https://docs.astral.sh/uv/).
Do not use the system Python or `pip`; use `uv run ...`. `scraper/.venv` is created by `uv sync`.

## Commands

Run from `scraper/`:

```powershell
uv sync                                    # install runtime + dev deps from uv.lock
uv run ruff format .                       # format
uv run ruff check .                        # lint (every Ruff rule enabled)
uv run mypy                                # mypy --strict over src and tests
uv run pytest                              # all tests
uv run pytest tests/unit/test_mercadona_scraper.py::test_name   # single test
uv run pytest --cov --cov-fail-under=90    # what CI runs
```

CI (`.github/workflows/ci.yml`, Linux + Windows) runs, in order: `ruff format --check`,
`ruff check`, `mypy`, `pytest --cov --cov-fail-under=90`. Run all four before finishing a change.
Pre-commit runs the same format, lint and type checks, and a `commit-msg` hook (`cz check`) that
rejects messages that are not [Conventional Commits](https://www.conventionalcommits.org/).

Versioning (Commitizen, config in `[tool.commitizen]`): the version is computed from the commit
types, never edited by hand. Releases are cut by the user, not by Claude:

```powershell
uv run cz bump --dry-run     # preview the next version and changelog entry (safe)
uv run cz bump               # needs a clean tree: commits with -a, tags vX.Y.Z, updates CHANGELOG.md
```

Running the app:

```powershell
uv run supermarket-scraper --version          # installed version
uv run supermarket-scraper init-db            # create or migrate the schema (run also does it)
uv run supermarket-scraper list               # registered chains
uv run supermarket-scraper run --dry-run      # full scrape, writes nothing
uv run supermarket-scraper run [CODE ...] [--details|--no-details]
uv run supermarket-scraper enrich [CODE ...] [--limit N]   # backfill EAN/details (real API)
uv run supermarket-scraper dump [CODE ...]    # raw JSON to data/raw/<code>/<day>/, no database (real API)
uv run supermarket-scraper run --save-raw     # normal run that also archives the raw JSON
uv run supermarket-scraper run --from-raw data/raw/mercadona/<day>   # process an archive, offline
uv run supermarket-scraper report [CODE ...] [--day YYYY-MM-DD] [--top N]   # day vs previous day
uv run supermarket-scraper history mercadona [--limit N]
```

`run` without `--dry-run` hits the real Mercadona API (~150 requests, plus up to 500 details) and
`enrich` makes one request per pending product (~3,300 on a fresh database). Both write to
`scraper/data/supermarkets.db`; `dump` makes the same ~150 requests and writes only files. Do not
run any of them unless asked. `report` and `run --from-raw` are offline. Tests never touch the network.

Exit codes: `0` ok, `1` partial (data stored), `2` failed, `64` usage or config error.

## Architecture (dependencies point inward)

```
presentation (cli.py) -> application (use case, ports) -> domain (entities, repository contracts)
bootstrap.py (composition root) -> infrastructure (http, scrapers, sql) -> application / domain
```

All paths below are under `scraper/src/supermarket_scraper/`.

- `domain/`: immutable entities and value objects, abstract repositories, `UnitOfWork`. Pure Python, no I/O.
- `application/`: `SyncCatalogUseCase`, `EnrichDetailsUseCase` (which the sync reuses for details),
  `BuildPriceReportUseCase` (read-only) and the ports `CatalogSource` and `Clock`.
- `infrastructure/`: `http/client.py` (retrying, rate-limited `AsyncJsonClient`), `http/archive.py`
  (raw JSON recording and replay as httpx transports, wired by `Container.recording` /
  `Container.replaying` in `bootstrap.py`), `scrapers/`
  (`base.py` template method, `registry.py`, one package per chain), `persistence/` (SQLAlchemy Core).
- `bootstrap.py`: the only place concrete classes are wired. `config.py`: typed settings from env or `.env`.
  Exception: `infrastructure/persistence/migrations/env.py` is a developer-only second entry point.
  From the `alembic` command line it reads `Settings` and builds its own engine; at runtime it gets
  its connection from `upgrade_schema()`.
- `presentation/cli.py`: argparse CLI and exit codes.

Never import `infrastructure` from `domain` or `application`.

### Adding a supermarket

Add a new package `infrastructure/scrapers/<code>/` with `__init__.py`, `schemas.py` (Pydantic models
of the payloads) and `scraper.py` (a `@register_scraper("<code>")` subclass of `BaseSupermarketScraper`).
**Do not edit existing files**: auto-discovery picks the package up. The full method list is in
README section 5. Add recorded payloads to `tests/fixtures/<code>/` and mirror
`tests/unit/test_mercadona_scraper.py`.

### Persistence

- SQLAlchemy Core only (no ORM). Prices are never floats: use `ExactDecimal`. Timestamps are
  timezone-aware UTC (`UtcDateTime`).
- Upserts go through per-dialect `UpsertStrategy` in `persistence/upsert.py`.
- Schema is versioned with Alembic. Migrations live in `persistence/migrations/versions/` and
  `upgrade_schema()` applies them on `init-db`, `run` and `history`; a pre-Alembic database is
  stamped at baseline `0001` automatically. A column change needs a new migration
  (`uv run alembic revision --autogenerate --rev-id 000N -m "..."`, from `scraper/`); the steps and
  the SQLite foreign-key caveat are in `.claude/rules/scraper/persistence.md`.
- Snapshots are unique per `(product, captured_on)`. Reruns on the same day overwrite. Days follow
  `SCRAPER_TIMEZONE`.
- Delisting is a soft flag and is skipped on incomplete scrapes or when more than
  `SCRAPER_MAX_DELIST_RATIO` of the catalog vanishes. Products are never deleted.
- `price_snapshots.reference_amount` is per base unit (kg, l, unit...): compare products by it,
  never by the shelf `amount`. Show products with `Product.label` (name plus presentation).
- `gtin` is not unique within a chain (bread sold whole and sliced shares it). A delisted product
  relisted under a new id with the same `gtin` is linked through `replaced_by_product_id`.

## Code conventions (enforced, so match them)

- `from __future__ import annotations` at the top of every module. Imports used only for typing go
  under `if TYPE_CHECKING:`, except for Pydantic models and settings, whose types are needed at runtime.
- Python 3.12 style: PEP 695 generics, `@override` on every override (mypy `explicit-override`), `X | None`.
- `mypy --strict`, with `warn_unreachable`, `disallow_any_unimported` and the Pydantic plugin. No bare
  `# type: ignore` (needs an error code).
- Ruff with `select = ["ALL"]`, line length 100, double quotes. Ignores are only those listed in
  `pyproject.toml`. Fix findings instead of adding `# noqa`; if one is unavoidable, name the rule
  and keep it narrow.
- Docstrings are mandatory, Google style, on every public module member and class. **No inline
  comments**: express intent with names and docstrings.
- Pydantic validates every external payload. A changed API must surface as `SourceSchemaError`, never
  as a silently wrong value.
- Tests are exempt from `D`, `S101`, `PLR2004`, `ANN401`, `SLF001` and `ARG`. Pytest runs with
  `filterwarnings = ["error"]`, so any warning fails the suite, and with `asyncio_mode = "auto"`
  (async tests need no marker).

## Testing

- `tests/unit/`: domain, HTTP client, Mercadona parsing. `tests/integration/`: SQL persistence, the
  use case over SQLite, and the CLI end to end.
- `tests/conftest.py` provides `FakeApi` (an `httpx.MockTransport` keyed by URL path), `FixedClock`,
  `no_sleep`, and fixtures `fake_api`, `make_scraper`, `clock`, `engine` (temp SQLite) and `uow_factory`.
  Reuse them rather than mocking again.
- Fixtures in `tests/fixtures/mercadona/*.json` mirror the real API shape. The live API can drift, so
  these do not prove production still works.

## Configuration

`SCRAPER_*` and `MERCADONA_*` environment variables, or `scraper/.env` (copy from `.env.example`,
which documents every setting and its default). `.env`, `data/`, `logs/` and `*.db` are git-ignored.
Keep the HTTP politeness defaults (`SCRAPER_HTTP_MAX_CONCURRENCY=4`,
`SCRAPER_HTTP_MIN_INTERVAL_SECONDS=0.25`) conservative: this scrapes a real site.

## Deployment files

`scraper/Dockerfile`, `deploy/systemd/` and `deploy/cron/` for Linux, and `scripts/run_daily.ps1` plus
`scripts/register_windows_task.ps1` (Task Scheduler) for Windows. Daily runners have lock files and log
rotation, so change them carefully.

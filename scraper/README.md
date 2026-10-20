# supermarket-scraper

Daily downloader for Spanish supermarket catalogs. It walks a chain's full catalog (categories,
products, prices, availability and new arrivals), merges it into a relational database, and keeps
**one price snapshot per product per day** to build a price history.

The first integration is **Mercadona**. Carrefour, DIA, Ahorramas and others plug in as new
subclasses, with no changes to the core.

---

## 1. Architecture

Clean Architecture: dependencies always point inward.

```
presentation (CLI) ──► application (use cases, ports) ──► domain (entities, repository contracts)
        │                          ▲                                   ▲
        └──► bootstrap ──► infrastructure (HTTP, scrapers, SQL) ───────┘
```

| Layer | Responsibility | Depends on |
|---|---|---|
| `domain` | Immutable entities and value objects (`Product`, `Price`, `Category`, `ScrapeRun`…), invariants, abstract repositories and `UnitOfWork`. Pure Python: no I/O, no frameworks. | nothing |
| `application` | `SyncCatalogUseCase`, which orchestrates a run. Defines the ports it needs: `CatalogSource` (any supermarket) and `Clock`. | domain |
| `infrastructure` | Adapters: retrying, rate-limited `httpx` client; `BaseSupermarketScraper` and `MercadonaScraper`; the scraper registry; SQLAlchemy Core repositories; dialect-aware upserts; logging. | application, domain |
| `presentation` | `argparse` CLI with scheduler-friendly exit codes. | bootstrap |
| `bootstrap` | Composition root: the only place where concrete classes are wired together. | everything |

### Extensible scrapers: Template Method plus Registry

`BaseSupermarketScraper[PageRefT, RawProductT, RawDetailT]` owns the orchestration: concurrent
crawling, per-page and per-product error isolation, de-duplication and failure reporting. A
subclass only implements small **fetch → parse → normalize** steps:

| Stage | Catalog index | Catalog page | Product detail |
|---|---|---|---|
| fetch (network) | `fetch_index()` | `fetch_page(ref)` | `fetch_detail(id)` |
| parse (validate into typed models) | `parse_index(payload)` | `parse_page(ref, payload)` | `parse_detail(payload)` |
| normalize (map to domain) | — | `normalize_product(raw, category_id)` | `normalize_detail(raw)` |

The `@register_scraper("code")` decorator plus package auto-discovery mean that **adding a
supermarket means adding one new package**. No existing file is edited (Open/Closed Principle).

### Pluggable persistence

The domain and application layers only see `UnitOfWork` and the abstract repositories in
`domain/repositories.py`. The shipped adapter uses SQLAlchemy Core with portable column types:

* `ExactDecimal` stores `NUMERIC(14,4)` on PostgreSQL and MySQL, and a scaled `BIGINT` on SQLite,
  so prices never pass through floating point.
* `UtcDateTime` always stores and returns timezone-aware UTC timestamps.
* Upserts go through an `UpsertStrategy` per dialect: native `ON CONFLICT` for SQLite and
  PostgreSQL, `ON DUPLICATE KEY` for MySQL and MariaDB, and a portable fallback for anything else.
  `register_upsert_strategy()` adds a native one without touching existing code.

**Switching SQLite → PostgreSQL or MySQL only means changing `SCRAPER_DATABASE_URL`.** A non-SQL
store (for example a document DB) would be a new `UnitOfWork` implementation wired in
`bootstrap.py`. Use cases and scrapers stay untouched.

### Robustness built in

* **Idempotent days**: snapshots are unique per `(product, captured_on)`, so re-running the same
  day overwrites that day's prices instead of duplicating them. Days follow `SCRAPER_TIMEZONE`.
* **Delisting safety net**: products missing from the catalog are marked delisted (never deleted).
  This is skipped when the scrape was incomplete, or when more than `SCRAPER_MAX_DELIST_RATIO` of
  the catalog vanished at once, which almost always means the source broke rather than the shop
  emptied.
* **Error isolation**: a broken category page or a malformed product is recorded as a failure and
  the run is marked `partial`. Everything else is still stored.
* **Atomic catalog write**: categories, products and prices are committed in one transaction.
  Detail enrichment runs afterwards, and its failure can never roll back the day's prices.
* **Schema-drift detection**: every payload is validated with Pydantic, and a changed API raises
  `SourceSchemaError` naming the field that broke.
* **Polite HTTP**: concurrency cap, minimum spacing between requests, and exponential backoff with
  jitter on 408/425/429/5xx. `Retry-After` is honoured.
* **Audit trail**: every execution is stored in `scrape_runs` with its status and counters.

---

## 2. Project structure

```
comparador-supermercados/          # repository root
├── .pre-commit-config.yaml         # format, lint and type-check on commit (runs on scraper/)
├── .github/workflows/ci.yml        # Linux + Windows CI (working directory: scraper/)
└── scraper/
    ├── pyproject.toml                  # deps + Ruff (ALL rules) + mypy --strict + pytest + coverage
    ├── uv.lock                         # reproducible dependency lock
    ├── alembic.ini                     # Alembic command line, for developers only
    ├── .env.example                    # every setting, documented
    ├── Dockerfile
    ├── deploy/
    │   ├── cron/crontab.example
    │   └── systemd/supermarket-scraper.{service,timer}
    ├── scripts/
    │   ├── run_daily.ps1               # Windows daily runner (mutex lock, log rotation)
    │   ├── register_windows_task.ps1   # registers the Task Scheduler job
    │   └── run_daily.sh                # Linux daily runner (flock, log rotation)
    ├── src/supermarket_scraper/
    │   ├── domain/
    │   │   ├── entities.py             # Supermarket, Category, Product, ProductListing, PriceObservation,
    │   │   │                           # ProductDetails, CatalogSnapshot, ScrapeRun, RunStats
    │   │   ├── value_objects.py        # SupermarketCode, Price, MeasureUnit, SellingMethod
    │   │   ├── repositories.py         # abstract repositories + UnitOfWork
    │   │   └── exceptions.py
    │   ├── application/
    │   │   ├── ports.py                # CatalogSource (scraper contract), Clock
    │   │   └── sync_catalog.py         # SyncCatalogUseCase
    │   ├── infrastructure/
    │   │   ├── http/client.py          # AsyncJsonClient (retries, pacing, concurrency)
    │   │   ├── scrapers/
    │   │   │   ├── base.py             # BaseSupermarketScraper (template method)
    │   │   │   ├── registry.py         # @register_scraper + auto-discovery
    │   │   │   └── mercadona/
    │   │   │       ├── schemas.py      # Pydantic models of the Mercadona API
    │   │   │       └── scraper.py      # MercadonaScraper
    │   │   ├── persistence/
    │   │   │   ├── tables.py           # SQLAlchemy schema
    │   │   │   ├── column_types.py     # ExactDecimal, UtcDateTime
    │   │   │   ├── upsert.py           # per-dialect upsert strategies
    │   │   │   ├── repositories.py     # SQL repository implementations
    │   │   │   ├── unit_of_work.py
    │   │   │   ├── database.py         # engine factory, upgrade_schema()
    │   │   │   └── migrations/         # Alembic env.py, script template, versions/
    │   │   ├── clock.py
    │   │   └── logging_setup.py        # text or JSON logs
    │   ├── presentation/cli.py
    │   ├── bootstrap.py                # composition root
    │   └── config.py                   # typed settings (env / .env)
    └── tests/
        ├── fixtures/mercadona/*.json   # recorded API payloads
        ├── unit/                       # domain, HTTP client, Mercadona parsing
        └── integration/                # SQL persistence, use case over SQLite, CLI end to end
```

### Data model

| Table | Content |
|---|---|
| `supermarkets` | One row per chain (`code` PK, e.g. `mercadona`). |
| `categories` | Full tree per chain (`external_id`, `parent_external_id`, `level`, `is_active`). |
| `products` | Current state of each product: name, packaging, size, URLs, availability, `is_new_arrival`, `is_active`, `first_seen_at` / `last_seen_at` / `delisted_at`, plus detail data (EAN, brand, origin, ingredients, allergens). |
| `product_categories` | Many-to-many: a product can appear under several categories. |
| `price_snapshots` | **Price history**, one row per product per day: shelf price, reference price per kg/l/unit, previous price, promotion flag, VAT. |
| `scrape_runs` | Audit log of every execution with counters and errors. |

`ean` is indexed because it is the natural key for matching the same product across chains in
the comparator.

#### Migrations

The schema is versioned with [Alembic](https://alembic.sqlalchemy.org/). The current version
is stored in the `alembic_version` table. `init-db`, `run` and `history` call `upgrade_schema()`,
which applies any pending migration before doing anything else, so a deployment only needs the new
code. A database created before migrations existed is detected and stamped at the baseline
revision `0001`. Its tables and data are kept as they are.

To change the schema, edit `tables.py` and then generate a migration from `scraper/`. Use a scratch
database, not `data/supermarkets.db`:

```powershell
$env:SCRAPER_DATABASE_URL = "sqlite:///C:/temp/scratch.db"
uv run alembic upgrade head
uv run alembic revision --autogenerate --rev-id 0002 -m "Add x to products"
```

Review the generated file in `migrations/versions/` before you commit it. Rules for migrations:

* **They never delete stored data.** A downgrade that would lose data raises instead, as the
  baseline does.
* **On SQLite, operations run in batch mode** (the table is rebuilt), with foreign keys
  temporarily off so that rebuilding `products` cannot cascade into `price_snapshots`.
* **Tests check the migrations against `tables.py`.** A test fails if applying them doesn't
  produce exactly the schema declared there.

---

## 3. Getting started

Requirements: Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/).

```powershell
# Windows (PowerShell). The commands are the same on Linux and macOS.
cd D:\Programacion\comparador-supermercados\scraper
uv sync                                   # creates .venv with runtime and dev dependencies
copy .env.example .env                    # optional: every value has a sane default
uv run supermarket-scraper init-db        # creates data\supermarkets.db
uv run supermarket-scraper run --dry-run  # full scrape, validates everything, writes nothing
uv run supermarket-scraper run            # real run
uv run supermarket-scraper history mercadona
```

### CLI

| Command | Description |
|---|---|
| `init-db` | Create the schema or apply pending migrations (idempotent; `run` also does this automatically). |
| `list` | List registered supermarkets. |
| `run [CODE ...]` | Scrape the given chains (default: all). `--dry-run` writes nothing. `--details` / `--no-details` override `SCRAPER_FETCH_DETAILS`. |
| `history CODE [--limit N]` | Show the latest runs and their counters. |

Exit codes: `0` success, `1` partial (some pages or details failed, data stored), `2` failed,
`64` usage or configuration error. Schedulers and monitors can alert on anything other than `0`.

### Configuration

All settings come from environment variables or `.env`. See `.env.example` for the documented
list. The most important ones:

| Variable | Default | Notes |
|---|---|---|
| `SCRAPER_DATABASE_URL` | `sqlite:///data/supermarkets.db` | Any SQLAlchemy URL. |
| `SCRAPER_TIMEZONE` | `Europe/Madrid` | Decides which calendar day a snapshot belongs to. |
| `SCRAPER_FETCH_DETAILS` / `SCRAPER_DETAILS_LIMIT` | `true` / `500` | EAN and brand enrichment, for never-enriched products only, at most N per run. The whole Mercadona catalog is enriched over the first ~10 days, then only new arrivals. |
| `SCRAPER_HTTP_MAX_CONCURRENCY` / `SCRAPER_HTTP_MIN_INTERVAL_SECONDS` | `4` / `0.25` | Politeness. Keep them conservative. |
| `SCRAPER_MAX_DELIST_RATIO` | `0.20` | Safety net described above. |
| `SCRAPER_LOG_FORMAT` | `text` | `json` for log aggregators. |
| `MERCADONA_WAREHOUSE` | `mad1` | Prices are regional (`mad1` Madrid, `vlc1` Valencia, `bcn1` Barcelona…). |

---

## 4. Daily execution

Mercadona updates prices overnight, so a run between 06:00 and 08:00 local time is a good choice.
A full Mercadona run makes about 150 requests and takes 1–3 minutes, plus up to 500 detail
requests during the first days. Each run applies pending schema migrations first, so the runners
below need no extra step after an update.

### Windows (Task Scheduler)

```powershell
cd D:\Programacion\comparador-supermercados\scraper
powershell -ExecutionPolicy Bypass -File scripts\register_windows_task.ps1 -At 06:30
Start-ScheduledTask -TaskName SupermarketScraperDaily   # optional: test it right now
```

The task runs `scripts\run_daily.ps1`, which prevents overlapping runs, appends to
`logs\scraper-YYYY-MM-DD.log` and deletes logs older than 30 days. It is set to run as soon as
possible if the PC was off at 06:30, to wake the PC, and to retry twice. The exit code shows up
as *Last Run Result* (`0x0` ok, `0x1` partial, `0x2` failed). `uv` must be on the `PATH` of the
user that runs the task.

### Linux: cron

```bash
chmod +x scripts/run_daily.sh
crontab -e     # then paste deploy/cron/crontab.example, adjusting the path
```

### Linux: systemd (recommended on a server)

```bash
sudo cp deploy/systemd/supermarket-scraper.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now supermarket-scraper.timer
systemctl list-timers supermarket-scraper.timer
journalctl -u supermarket-scraper.service
```

`Persistent=true` catches up on a missed run after downtime, and the unit runs sandboxed.

### Docker

```bash
docker build -t supermarket-scraper .
docker run --rm -v scraper-data:/data supermarket-scraper run
# PostgreSQL: -e SCRAPER_DATABASE_URL=postgresql+psycopg://user:pass@db:5432/supermarkets
```

---

## 5. Adding a new supermarket

Create `src/supermarket_scraper/infrastructure/scrapers/<code>/` with an empty `__init__.py`,
`schemas.py` (Pydantic models of that chain's payloads) and `scraper.py`:

```python
@register_scraper("dia")
class DiaScraper(BaseSupermarketScraper[DiaPageRef, DiaProduct, DiaDetail]):
    """Scraper for DIA's online shop."""

    @classmethod
    def from_context(cls, context: ScraperContext) -> Self: ...
    @property
    @override
    def supermarket(self) -> Supermarket: ...
    @override
    async def fetch_index(self) -> object: ...
    @override
    def parse_index(self, payload: object) -> CategoryIndex[DiaPageRef]: ...
    @override
    async def fetch_page(self, ref: DiaPageRef) -> object: ...
    @override
    def parse_page(self, ref: DiaPageRef, payload: object) -> CategoryPage[DiaProduct]: ...
    @override
    def normalize_product(self, raw: DiaProduct, category_external_id: str) -> ProductListing: ...
    @override
    def describe_page(self, ref: DiaPageRef) -> str: ...
    @override
    async def fetch_detail(self, external_id: str) -> object: ...
    @override
    def parse_detail(self, payload: object) -> DiaDetail: ...
    @override
    def normalize_detail(self, raw: DiaDetail) -> ProductDetails: ...
```

That's all. `supermarket-scraper list` will show `dia`, and the daily job will pick it up
automatically. Record a few real payloads into `tests/fixtures/dia/` and mirror
`tests/unit/test_mercadona_scraper.py`.

If a chain doesn't fit the index → pages model (for example a paginated search API or HTML
rendering), implement the smaller `CatalogSource` port directly. The use case only depends on that.

---

## 6. Quality toolchain

```bash
uv run ruff format .        # formatting
uv run ruff check .         # lint: every Ruff rule enabled (select = ["ALL"])
uv run mypy                 # mypy --strict + extra error codes, over src and tests
uv run pytest --cov         # 54 tests, ~94% branch coverage
uv run pre-commit install   # run format, lint and mypy on every commit (config at the repo root)
```

The code uses Python 3.12 generics and `@override` throughout. Docstrings are mandatory (Google
style, enforced by Ruff) and there are no inline comments.

---

## 7. Notes and next steps

* The Mercadona API is the unofficial JSON API behind `tienda.mercadona.es`. It may change without
  notice; schema validation turns such changes into explicit `partial` or `failed` runs. Check the
  site's terms of use, and keep the default rate limits.
* The test fixtures mirror the real payload shape, but run `run --dry-run` once on your machine
  to confirm the live API still matches before you schedule the job.
* Schema changes go through Alembic migrations (see "Migrations" under the data model). The
  migrations are only tested on SQLite. Run `alembic upgrade head` against a PostgreSQL or MySQL
  database once before relying on it in production.
* The future REST API can reuse `domain` and `infrastructure/persistence` directly, by adding
  query-side repositories (for example price history per product, or cheapest by EAN) next to the
  existing ones.

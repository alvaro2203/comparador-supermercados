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
| `application` | `SyncCatalogUseCase`, which orchestrates a run, and `EnrichDetailsUseCase`, which fetches product details (the daily run reuses it). Defines the ports it needs: `CatalogSource` (any supermarket) and `Clock`. | domain |
| `infrastructure` | Adapters: retrying, rate-limited `httpx` client; `BaseSupermarketScraper` and `MercadonaScraper`; the scraper registry; SQLAlchemy Core repositories; dialect-aware upserts; logging. | application, domain |
| `presentation` | `argparse` CLI with scheduler-friendly exit codes. | bootstrap |
| `bootstrap` | Composition root: the only place where concrete classes are wired together. | everything |

There is one other entry point, for developers only: `infrastructure/persistence/migrations/env.py`,
which Alembic loads. At runtime it receives its connection from `upgrade_schema()`. When it is run
from the `alembic` command line, it reads `Settings` and builds its own engine outside `bootstrap`.

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
    │                                   # + Commitizen (version and changelog)
    ├── uv.lock                         # reproducible dependency lock
    ├── CHANGELOG.md                    # written by `cz bump` (from the first release on)
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
    │   │   ├── sync_catalog.py         # SyncCatalogUseCase
    │   │   ├── enrich_details.py       # EnrichDetailsUseCase (detail backfill)
    │   │   └── price_report.py         # BuildPriceReportUseCase (read-only day-over-day report)
    │   ├── infrastructure/
    │   │   ├── http/client.py          # AsyncJsonClient (retries, pacing, concurrency)
    │   │   ├── http/archive.py         # raw JSON archive: recording and replay transports
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
| `products` | Current state of each product: name, packaging, size, `unit_name` (what `total_units` counts: `lavados`, `pastillas`…), URLs, availability, `is_new_arrival`, `is_active`, `first_seen_at` / `last_seen_at` / `delisted_at`, plus detail data (EAN, brand, origin, ingredients, allergens), `gtin` and, on a delisted product, `replaced_by_product_id`. |
| `product_categories` | Many-to-many: a product can appear under several categories. |
| `price_snapshots` | **Price history**, one row per product per day: shelf price, reference price per kg/l/unit, previous price, promotion flag, VAT. |
| `scrape_runs` | Audit log of every execution with counters and errors, including the price changes broken down into regular increases and decreases and promotions started and ended (NULL for runs recorded before migration `0004`). |

`ean` keeps the code exactly as the chain publishes it. `gtin` is the same code left-padded to 14
digits (`normalize_gtin` in `domain/value_objects.py`), so an EAN-8, a UPC-A with or without its
leading zero and an EAN-13 compare equal. It is NULL when there is no EAN or the EAN fails its
check digit, so `ean IS NOT NULL AND gtin IS NULL` lists invalid codes. `gtin` is indexed: it is
the key for matching the same product across chains in the comparator.

`gtin` is **not unique within a chain**. Mercadona sells some loaves whole and sliced as two
products (`9638.1` and `9638.2`) with the same EAN, size and price. The comparator returns every
product of a `gtin` as variants; it never picks one.

`replaced_by_product_id` handles a chain relisting the same article under a new id. On a delisted
product, it points at the only active product with the same `gtin` first seen after the delisted
one was last seen. Siblings that share a `gtin` while both are listed are never linked, an
ambiguous match (two candidates) is left unlinked, and a product that comes back loses its link.
A change of size is a new GTIN and therefore a different product, never a successor: compare those
by reference price. Each `run` computes the links after fetching details.

**Reference price.** `reference_amount` is always a price per base unit (`reference_unit`: kg, l,
unit, m or wash), so it compares products of any size and any chain. Chains quote it per
`100 g`, `75 cl`, `12 ud` or per dozen (`dz`); `MeasureUnit.parse_quantity` turns that into a
quantity of the base unit and the scraper divides by it (Mercadona's orégano: 4,00 €/100 g becomes
40,00 €/kg; eggs at 2,625 €/dozen become 0,21875 €/unit). Detergents are quoted per wash (`lv`,
lavado), which is their legal reference unit in Spain. Comparisons and alerts across products must
use `reference_amount`, never the shelf `amount`.

For canned and jarred food the reference is per kg of **drained weight**, the legal one, so it
differs from Mercadona's `bulk_amount` (per kg of gross weight). Olives at 1,05 € for 350 g with
150 g drained are 3,00 €/kg gross but 7,00 €/kg drained; `reference_amount` holds the 7,00. Do not
"fix" it to match `bulk_amount`: about 200 such products are expected to differ.
Snapshots taken before this fix for formats quoted per `100 g` or `100 ml` (about 530 a day, 18-23
October 2026) keep `reference_unit = 'unknown'` and their per-100 value: they are not comparable.

**Price changes.** `Price.change_from` classifies each change against the previous observation:
a promotion starting or ending first, otherwise a regular increase or decrease. `price_changes`
is the total; the breakdown tells a temporary discount from a real price move.

**New arrivals.** `is_new_arrival` is the chain's own "new" badge (Mercadona's product-level
`is_new_arrival`, which marks products for a few weeks). `first_seen_at` is when this scraper first
saw the product. They answer different questions; use the one that fits.

**Product labels.** Many products share a name and differ only in presentation (262 names, 631
Mercadona products). Any listing must show `Product.label`, the name plus
`PackagingInfo.describe()`: "Aceite de oliva virgen extra Hacendado (Garrafa 5 l)",
"(Pack-6 6 x 1,5 l)", "(Paquete 26 ud)", "(Botella 3 l, 50 lavados)". Units are named after
`unit_name` when the chain gives one, and plain "ud" otherwise (Mercadona's generic `ud.` is stored
as NULL). It is unique across Mercadona's active catalog.

#### Migrations

The schema is versioned with [Alembic](https://alembic.sqlalchemy.org/). The current version
is stored in the `alembic_version` table. `init-db`, `run` and `history` call `upgrade_schema()`,
which applies any pending migration before doing anything else, so a deployment only needs the new
code. A database created before migrations existed is detected and stamped at the baseline
revision `0001`. Its tables and data are kept as they are. If it holds only some of the six baseline
tables, it is refused with an error instead of being stamped.

Each upgrade runs in a single transaction. If a migration fails, every statement it ran is rolled
back, the database stays at its previous revision, and the command exits with `2`.

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
  temporarily off so that rebuilding `products` cannot cascade into `price_snapshots`. Before
  committing, `PRAGMA foreign_key_check` must come back clean, or the migration is rolled back.
  This applies both to `upgrade_schema()` and to the `alembic` command line, which share
  `migration_connection()`.
* **The `alembic` command line does not adopt old databases.** On a database created before
  migrations existed (no `alembic_version` table), `alembic upgrade head` fails with "table
  already exists". Run `uv run supermarket-scraper init-db` on it first.
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
uv run supermarket-scraper enrich         # optional: fetch every EAN now instead of 500 per run
uv run supermarket-scraper history mercadona
```

### CLI

| Command | Description |
|---|---|
| `--version` | Print the installed version (see "Versioning and releases"). |
| `init-db` | Create the schema or apply pending migrations (idempotent; `run` also does this automatically). |
| `list` | List registered supermarkets. |
| `run [CODE ...]` | Scrape the given chains (default: all). `--dry-run` writes nothing. `--details` / `--no-details` override `SCRAPER_FETCH_DETAILS`. `--save-raw` also archives every downloaded JSON (see below). `--from-raw DIR` processes an archive instead of the network. |
| `dump [CODE ...]` | Download each catalog to the raw archive and validate it, without opening the database. |
| `report [CODE ...] [--day YYYY-MM-DD] [--top N]` | Offline: compare a day of stored prices (default: the latest) with the previous day that has prices. See "Price report" below. |
| `enrich [CODE ...] [--limit N]` | Fetch details (EAN, brand, ingredients) for every product never enriched, or at most N per chain. Commits every 100 products, so an interrupted backfill resumes where it stopped; stops if a whole chunk fails. Prints the reason for each failed product (up to 20; every one goes to the log as `Detail enrichment failure`). Not recorded in `scrape_runs`. |
| `history CODE [--limit N]` | Show the latest runs and their counters, with price changes split into `up` / `down` (regular price) and `promo+` / `promo-` (promotion started / ended); `-` for runs recorded before the split existed. |

### Price report

`report` answers "what changed in prices since the last time?" from the database alone: it makes
no request, so run it as often as you like, typically right after `run`.

```powershell
uv run supermarket-scraper report                       # every chain, latest day vs the one before
uv run supermarket-scraper report mercadona             # one chain
uv run supermarket-scraper report --day 2026-10-21      # that day vs the day with prices before it
uv run supermarket-scraper report --top 25              # list up to 25 products per section
```

It compares **days, not runs**. The latest day with prices (or `--day`) is compared with the
closest earlier day that has prices, whatever the gap: if no run happened on the 22nd, the 23rd is
compared with the 21st. Several runs on the same day count as one, since each overwrites that
day's snapshot. `--top` (default `10`) only limits how many products each section lists; the
counts always cover the whole catalog. A day without prices exits with `64` and a one-line message.

```
[mercadona] 2026-10-23 vs 2026-10-21
  products priced  4275
  with GTIN        4275 (100.0 %)
  new arrivals     102 (chain badge)
  promotions       134 active
  unknown ref unit 1
  price changes    45 (15 up, 11 down, 12 promo started, 7 promo ended)
  top increases
    +16.67 %  0.36 -> 0.42  Limón (Pieza aprox. 160 g)
    +15.00 %  0.20 -> 0.23  Pimiento verde freír (Pieza aprox. 90 g)
  top decreases
    -23.73 %  0.59 -> 0.45  Pera Limonera (Pieza aprox. 160 g)
  promotions started
    -24.68 %  9.24 -> 6.96  Cerveza clásica Mahou (12 x 330 ml)
  new products     14
    Aceite de oliva virgen extra Hacendado (Garrafa 5 l)
    ... and 13 more
  delisted         21
    Barra de pan espiga (400 g)
    ... and 20 more
```

| Line | Meaning |
|---|---|
| `products priced` | Products with a price snapshot on the reported day. |
| `with GTIN` | How many of them have a valid EAN, the key for comparing across chains. |
| `new arrivals` | Products carrying the chain's own "new" badge (`is_new_arrival`). |
| `promotions` | Products whose price that day is a promotion. |
| `unknown ref unit` | Prices whose reference unit could not be parsed, so they cannot be compared by `reference_amount`. Should stay near zero; a jump means the chain started quoting a new unit. |
| `price changes` | Products priced on both days whose shelf price changed, split as in `history`: regular increases and decreases, promotions started and ended. |
| `top increases` / `top decreases` | Regular price moves (not promotions) with the largest percentage, shelf price before and after. |
| `promotions started` | New promotions, deepest discount first. |
| `new products` / `delisted` | Priced on the reported day but not the previous one, and the reverse. This compares the two days only, so it differs from the `delisted_at` flag, which waits for a complete scrape. |

`with GTIN` and `new arrivals` use each product's current data, because neither is kept per day.
Without a previous day (the first day ever, or `--day` set to it), only the counts are shown.

### Raw JSON archive

`dump` and `run --save-raw` save every successful response, byte for byte, under
`SCRAPER_RAW_DIR/<code>/<YYYY-MM-DD>/` (default `data/raw/`, git-ignored). File names mirror the URL
path (`/api/categories/112/` is `api/categories/112.json`); query parameters are not part of the
name. `manifest.json` records the supermarket and the capture instant. A full Mercadona catalog is
154 files and about 5.5 MB a day (measured on 23 October 2026); `--save-raw` also keeps the product details it fetched. A second
capture on the same day overwrites files with the same name and leaves the others.

`run --from-raw data/raw/mercadona/2026-10-23` processes such an archive with no network at all:
prices are stored under the capture day (the clock is frozen at the manifest's instant), pacing is
off, and product details are skipped. It accepts `--dry-run`, which validates an archive against
the current schemas without writing; it does not accept supermarket codes or `--save-raw`. Use it
to reload a day after a bug fix, or to reproduce an API change offline. The usual flow if you
prefer to download first and process later is `dump`, then `run --from-raw`.

Exit codes: `0` success, `1` partial (some pages or details failed, data stored), `2` failed
(including a failed schema migration, in which case nothing is scraped), `64` usage or
configuration error. Schedulers and monitors can alert on anything other than `0`.

### Configuration

All settings come from environment variables or `.env`. See `.env.example` for the documented
list. The most important ones:

| Variable | Default | Notes |
|---|---|---|
| `SCRAPER_DATABASE_URL` | `sqlite:///data/supermarkets.db` | Any SQLAlchemy URL. |
| `SCRAPER_RAW_DIR` | `data/raw` | Where `dump` and `run --save-raw` archive raw JSON. |
| `SCRAPER_TIMEZONE` | `Europe/Madrid` | Decides which calendar day a snapshot belongs to. |
| `SCRAPER_FETCH_DETAILS` / `SCRAPER_DETAILS_LIMIT` | `true` / `500` | EAN and brand enrichment, for never-enriched products only, at most N per run. The whole Mercadona catalog is enriched over the first ~10 days, then only new arrivals. `enrich` does the backlog in one go (~3,300 products, about 15 minutes at the default pacing). |
| `SCRAPER_HTTP_MAX_CONCURRENCY` / `SCRAPER_HTTP_MIN_INTERVAL_SECONDS` | `4` / `0.25` | Politeness. Keep them conservative. |
| `SCRAPER_MAX_DELIST_RATIO` | `0.20` | Safety net described above. |
| `SCRAPER_LOG_FORMAT` | `text` | `json` for log aggregators. |
| `MERCADONA_WAREHOUSE` | `mad1` | Prices are regional (`mad1` Madrid, `vlc1` Valencia, `bcn1` Barcelona…). The project tracks Madrid only. The warehouse is not stored with each price, so changing it on an existing database silently mixes regions in the history: start a new database instead. |

---

## 4. Daily execution

Mercadona updates prices overnight, so a run between 06:00 and 08:00 local time is a good choice.
A full Mercadona run makes about 150 requests and takes 1–3 minutes, plus up to 500 detail
requests during the first days. Each run applies pending schema migrations first, so the runners
below need no extra step after an update.

### GitHub Actions (in progress)

`.github/workflows/daily-scrape.yml` runs `run --save-raw` every day at 05:30 UTC against the
PostgreSQL database in the repository secret `SCRAPER_DATABASE_URL` (Neon), and uploads the raw
JSON as an artifact kept for 14 days. A manual run defaults to `--dry-run`. It has not run yet:
follow [docs/ejecucion-diaria.md](../docs/ejecucion-diaria.md) step by step. The Windows,
cron and systemd runners below stay available as alternatives; do not run two of them against
different databases.

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
uv run pytest --cov         # 160 tests, ~95% branch coverage
uv run pre-commit install   # format, lint and mypy on every commit, plus the commit message check
```

The code uses Python 3.12 generics and `@override` throughout. Docstrings are mandatory (Google
style, enforced by Ruff) and there are no inline comments.

### Versioning and releases

The version lives in `pyproject.toml` (`[project] version`) and follows
[Semantic Versioning](https://semver.org/). Nobody edits it by hand:
[Commitizen](https://commitizen-tools.github.io/commitizen/) derives it from the commit history,
which therefore must follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<optional scope>): <description>

feat(report): add the day-over-day price report
fix: parse reference prices quoted per 100 g
feat!: rename the products table        # "!" or a "BREAKING CHANGE:" footer
```

Allowed types: `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `build`, `ci`, `chore`, `style`,
`revert` and `bump`. After `pre-commit install` (it installs the `commit-msg` hook too), a commit
whose message does not match is rejected. Merge, revert and `fixup!` commits are let through.

| Commits since the last release | Next version while `0.x` | From `1.0.0` on |
|---|---|---|
| only `fix` / `refactor` / `perf` | patch: `0.2.0` → `0.2.1` | `1.2.0` → `1.2.1` |
| at least one `feat` | minor: `0.2.0` → `0.3.0` | `1.2.0` → `1.3.0` |
| a breaking change | minor: `0.2.0` → `0.3.0` (`major_version_zero`) | `1.2.0` → `2.0.0` |
| only `docs`, `test`, `chore`, `build`, `ci`, `style` | no release | no release |

To release, from `scraper/` and with **everything committed** (`cz bump` commits with `git commit -a`,
so any other pending change would end up in the release commit):

```powershell
git status                         # must be clean
uv run cz bump --dry-run           # preview: next version, tag and changelog entry
uv run cz bump                     # write it: see below
uv run cz bump --increment MAJOR   # force a level, e.g. 1.0.0 when the project is ready
```

`cz bump` updates the version in `pyproject.toml` and `uv.lock`, adds the release to
`CHANGELOG.md` (only the new section; earlier ones keep any manual edits), commits them as
`bump: version 0.1.0 → 0.2.0` and creates the annotated tag `v0.2.0`. It does not push: run
`git push --follow-tags` when the repository has a remote. The running program reports the same
number through `supermarket-scraper --version`, read from the installed package metadata, so there
is no second copy of the version in the code.

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

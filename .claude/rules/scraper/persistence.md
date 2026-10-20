---
paths:
  - "scraper/src/supermarket_scraper/infrastructure/persistence/**/*.py"
---

# Persistence (SQLAlchemy Core)

Switching SQLite -> PostgreSQL/MySQL must remain a change of `SCRAPER_DATABASE_URL` only.

## Portability

- Build statements with SQLAlchemy Core expressions. No raw SQL strings, no dialect-specific
  functions outside `upsert.py` and `column_types.py`.
- Inserts that may collide go through `upsert(...)`. A new engine gets its own `UpsertStrategy`
  via `register_upsert_strategy`; the portable fallback must keep working.
- Chunk large `IN (...)` lists with `itertools.batched` and `IN_CLAUSE_CHUNK`; SQLite and MySQL
  have bind-parameter limits.
- Prices, sizes and rates use `ExactDecimal()`; timestamps use `UtcDateTime()`; calendar days use `Date`.
- Constraint and index names come from `NAMING_CONVENTION`; do not hard-code different ones.

## Data integrity

- Never `DELETE` products, categories or price snapshots. Removal is `is_active=False` plus
  `delisted_at`. Only `product_categories` links are replaced.
- Price snapshots are unique per `(product_id, captured_on)`; a same-day rerun overwrites that row.
- Enrichment never erases data: brand, EAN and image use `coalesce(new, existing)`.
- Repositories map rows to domain objects and back. Rows, `Row` mappings and SQLAlchemy types
  never leave this package.

## Schema changes (Alembic)

The schema is versioned with Alembic. Migrations live in the package, in `persistence/migrations/`
(so the wheel and the Docker image ship them), and `upgrade_schema()` in `database.py` applies them
on `init-db`, `run` and `history`. `scraper/alembic.ini` is only for the developer command line.
`create_all` is no longer used outside tests that build throwaway tables.

When you add or change a column:

1. Update `tables.py`, the domain entity and the repository mapping (`_product_row`, `latest`, ...).
2. If the column must change on upsert, add it to `_PRODUCT_CATALOG_COLUMNS` or `_PRICE_COLUMNS`.
   Otherwise it is written on insert and silently never updated.
3. Make new columns on existing tables nullable or give them a server default.
4. From `scraper/`, generate the migration against a database at head, with the next sequential id:
   `uv run alembic revision --autogenerate --rev-id 0002 -m "Add x to products"`. Point
   `SCRAPER_DATABASE_URL` at a scratch copy, never at `data/supermarkets.db`. Post-write hooks run
   Ruff on the file; then review it by hand and remove the `# ###` marker comments.
5. Migrations never delete products, categories or price history: no `drop_table` and no
   `drop_column` on data that exists in production. A `downgrade()` that would lose data raises
   `NotImplementedError` instead, as the baseline `0001` does.
6. `test_migrations_build_exactly_the_schema_declared_in_tables` fails until the migration matches
   `tables.py`. Add an integration test for any data transformation the migration performs.

SQLite specifics: `env.py` renders operations in batch mode, which rebuilds a table to alter it.
Every migration path opens its connection through `migration_connection()` in `database.py`
(`upgrade_schema()` and the standalone branch of `env.py` both do). On SQLite it:

- turns `PRAGMA foreign_keys` off, because dropping `products` with enforcement on would cascade
  and delete `price_snapshots`;
- sets the driver's `isolation_level` to `None` and emits `BEGIN`, because pysqlite otherwise
  autocommits each `CREATE`/`DROP` and a failing migration would leave tables behind;
- runs `PRAGMA foreign_key_check` before committing and rolls the migration back if any row
  references a missing parent, since re-enabling foreign keys does not validate existing rows.

Never run migrations on a connection that did not come from `migration_connection()`. `env.py`
raises `TypeError` if `config.attributes["connection"]` is set to anything but a `Connection`,
instead of falling back to `SCRAPER_DATABASE_URL`.

`upgrade_schema()` raises `SchemaMigrationError` for any failure, including a pre-migration
database that holds only part of `BASELINE_TABLES`, and the CLI maps it to exit code `2`.

The Alembic command line does not adopt a pre-migration database (no `alembic_version` table);
`alembic upgrade head` on one fails with "table ... already exists". Run
`supermarket-scraper init-db` on it first.

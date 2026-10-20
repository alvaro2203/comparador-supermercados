---
name: add-supermarket
description: Onboard a new supermarket chain (Carrefour, DIA, Ahorramas, Alcampo, Eroski...) into the scraper as a self-registering package, with schemas, recorded fixtures, tests and docs. Use when the user asks to add, integrate or support a new supermarket.
argument-hint: "<code> [shop-url]"
---

# Add a supermarket: $ARGUMENTS

Goal: a new package under `scraper/src/supermarket_scraper/infrastructure/scrapers/<code>/` that
auto-registers, with no edits to existing source files (Open/Closed). Track progress with a task list.

## 1. Settle the identifiers

- `<code>`: lowercase slug matching `^[a-z][a-z0-9_]{1,31}$` (e.g. `carrefour`, `dia`, `ahorramas`).
  If `$ARGUMENTS` does not give one, ask.
- Confirm it is not already registered: `uv run supermarket-scraper list` (from `scraper/`).

## 2. Research the source

Delegate to the `supermarket-api-researcher` agent with the code and shop URL. Do not start coding
until its report says the source is feasible. If it reports blocking or login walls, stop and
discuss options with the user instead of working around them.

## 3. Turn samples into fixtures

From `scraper/.captures/<code>/`, create `scraper/tests/fixtures/<code>/` with trimmed payloads:
real field names and nesting, 1-2 categories, 3-5 products covering a normal item, a promotion,
an item sold by weight, an unavailable item, and missing optional fields. End files with a newline.

## 4. Implement the package

Create `__init__.py` (empty), `schemas.py` and `scraper.py`. Start from
[scraper_template.md](scraper_template.md) and the Mercadona package as the reference
implementation. Key points:

- `@register_scraper("<code>")` on a `BaseSupermarketScraper[PageRefT, RawProductT, RawDetailT]`
  subclass, or a direct `CatalogSource` implementation if the researcher said the model does not fit.
- `from_context` builds `AsyncJsonClient(base_url, context.http, transport=context.transport)`.
- Chain settings in `<Code>Settings(BaseSettings)` with `env_prefix="<CODE>_"`.
- Required Pydantic fields for id, name and shelf price; validation errors become `SourceSchemaError`.
- Map units with `MeasureUnit.parse`. If a unit alias is missing, adding it to `_UNIT_ALIASES` in
  `domain/value_objects.py` is the one acceptable edit outside the package; mention it to the user.
- Copy small schema helpers (loose decimals, blank-to-None) rather than importing them from another
  chain's package. If two chains share a lot, propose a shared module to the user first.

## 5. Tests

Add `scraper/tests/unit/test_<code>_scraper.py` mirroring `test_mercadona_scraper.py`:
tree and products normalized with exact `Decimal` prices; failing category isolated; invalid product
isolated; schema drift reported as failure; details cleaned and missing ones isolated; registry
discovers the code. Build the scraper with `FakeApi` (see the template). Add one integration test
running `SyncCatalogUseCase` over SQLite with the new source.

## 6. Configuration and docs

- Add every `<CODE>_*` setting to `scraper/.env.example`, with defaults and one-line explanations.
- README: add the chain to the intro and the configuration table; note its price-region setting.
- CLAUDE.md: update "Mercadona is the only integration" and the list of integrations.

## 7. Verify

1. From `scraper/`: `uv run ruff format .`, `uv run ruff check .`, `uv run mypy`,
   `uv run pytest --cov --cov-fail-under=90`.
2. Run the `architecture-reviewer` agent and fix every blocker and major finding.
3. Ask the user before any live call. With their go-ahead, run
   `uv run supermarket-scraper run <code> --dry-run --no-details` and report the counts.
   Never run without `--dry-run` as part of this skill.

## Done means

Package auto-registered, fixtures and tests added, all four checks green, reviewer approval,
docs updated, and a clear statement of whether the live dry run was performed and its result.

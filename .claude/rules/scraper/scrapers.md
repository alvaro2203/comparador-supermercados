---
paths:
  - "scraper/src/supermarket_scraper/infrastructure/scrapers/**/*.py"
  - "scraper/src/supermarket_scraper/infrastructure/http/**/*.py"
---

# Supermarket scrapers and HTTP

## Adding a chain (Open/Closed)

- Create `infrastructure/scrapers/<code>/` with `__init__.py`, `schemas.py` and `scraper.py`.
  Do not edit `base.py`, `registry.py`, `bootstrap.py` or another chain's package to make it work.
- `<code>` matches `SupermarketCode` (`^[a-z][a-z0-9_]{1,31}$`) and is passed to `@register_scraper`.
- Subclass `BaseSupermarketScraper[PageRefT, RawProductT, RawDetailT]` and implement every abstract
  method with `@override`. If the site does not fit index -> pages, implement `CatalogSource` directly.
- Provide `from_context(cls, context: ScraperContext) -> Self` that builds an `AsyncJsonClient`
  with `context.http` and `context.transport`. Passing `transport` through is what lets tests run offline.
- Chain settings live in the chain's own `BaseSettings` with prefix `<CODE>_`
  (see `MercadonaSettings`), and are documented in `.env.example`.

## Fetch, parse, normalize

- **fetch**: network only, through `self._http.get_json`. Never create an `httpx` client or call
  `httpx`/`requests` directly; that bypasses retries, pacing and the concurrency cap.
- **parse**: validate with Pydantic models from `schemas.py`, via a helper that converts
  `ValidationError` into `SourceSchemaError` with the failing field path.
- **normalize**: map to domain entities. Strip text, clean HTML, validate EANs, map units with
  `MeasureUnit.parse`, prices as `Decimal`.
- The base class isolates pages and products by catching `DomainError` only. Expected data
  problems must raise a `DomainError` subclass; anything else is a bug and should fail loudly.

## Schemas

- Base model: `ConfigDict(extra="ignore", frozen=True)`. Unknown fields are fine, missing ones are not.
- Fields the comparator relies on (id, name, shelf price) are required, so API drift surfaces as
  `SourceSchemaError` instead of a silently wrong price. Use loose coercion (`LooseDecimal`,
  `OptionalText`) only for genuinely optional data.
- Ids are `str` even when the API sends numbers.

## HTTP politeness

- Keep `HttpClientConfig` defaults conservative; never lower `min_interval_seconds` or raise
  `max_concurrency` in code. Users tune them through settings.
- Retry only transient statuses (408, 425, 429, 5xx) and transport errors; honour `Retry-After`.
  404 is `ResourceNotFoundError` and is never retried.
- Send the configured `User-Agent`. Do not spoof browsers, rotate identities or evade blocking.

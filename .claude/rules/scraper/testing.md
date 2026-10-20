---
paths:
  - "scraper/tests/**/*"
---

# Tests

## Rules

- No network, ever. HTTP goes through `FakeApi` (`httpx.MockTransport` keyed by URL path; an `int`
  value is returned as that status code). Use `no_sleep` so retries and pacing do not slow the suite.
- No real clock: use `FixedClock` and `clock.advance(days=...)` for multi-day scenarios.
- Databases are temporary SQLite files from the `engine` / `uow_factory` fixtures. Never point a
  test at `data/supermarkets.db`.
- Reuse `tests/conftest.py` fixtures (`fake_api`, `make_scraper`, `clock`, `engine`, `uow_factory`)
  instead of new mocks. Add shared helpers there, not copies in each file.
- Mutate responses per test through `fake_api.responses[...]`; the fixture is a deep copy, so
  changes don't leak between tests.
- Tests are type-checked by `mypy --strict` too: annotate fixtures and helpers; import test-only types
  under `TYPE_CHECKING` (`from tests.conftest import FakeApi`).
- Any warning fails the suite (`filterwarnings = ["error"]`). Fix the cause; do not filter it.
- Async tests are plain `async def` (`asyncio_mode = "auto"`), no marker.

## What to write

- Name tests after the behaviour: `test_partial_scrape_never_delists`, not `test_sync_2`.
- `tests/unit/` for parsing, normalization, domain invariants and the HTTP client.
  `tests/integration/` for anything that touches SQL or runs the CLI.
- Bug fixes come with a regression test that fails before the fix.
- Keep coverage at or above 90% (CI gate).

## Fixtures

- `tests/fixtures/<code>/*.json` mirror the real API shape. When adding or updating one, keep the
  real field names and nesting, trim to a few representative products, and include edge cases
  (promotion, weight-sold item, unpublished category, missing optional fields).
- When the live API changes, update the fixture and the schema together, and keep a test that
  proves the old breaking shape now raises `SourceSchemaError`.

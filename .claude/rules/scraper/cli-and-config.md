---
paths:
  - "scraper/src/supermarket_scraper/presentation/**/*.py"
  - "scraper/src/supermarket_scraper/bootstrap.py"
  - "scraper/src/supermarket_scraper/config.py"
  - "scraper/.env.example"
---

# CLI, composition root and configuration

## CLI (`presentation/cli.py`)

- Thin layer: parse arguments, build the container, call the use case, render the report, map the
  result to an `ExitCode`. No business rules and no SQL here.
- Exit codes are a contract with Task Scheduler, cron and systemd: `0` ok, `1` partial,
  `2` failed, `64` usage/config. Do not renumber; add new codes only at the end and document them.
- Results go to stdout; diagnostics and errors to stderr. Logs go to stderr through `configure_logging`.
- `DomainError` and invalid `Settings` become `ExitCode.USAGE` with a one-line message, not a traceback.
- `--dry-run` must never create the database file or write any row.
- New commands get an end-to-end test in `tests/integration/test_cli.py` that uses `FakeApi`
  through a patched `build_container`, never the network.

## Composition root (`bootstrap.py`)

- The only module that instantiates concrete adapters. Other modules receive them as arguments.
- It calls `discover_scrapers()`; never import a chain package by name to register it.

## Settings (`config.py`)

- Every option is a typed field with constraints (`Field(ge=..., le=...)`, `Literal[...]`, validators)
  so bad values fail at startup with a clear message.
- Global options use the `SCRAPER_` prefix; chain options live in that chain's own settings class.
- Every new option is added to `.env.example` with its default and a one-line explanation,
  and to the README configuration table.
- Never commit secrets. Database credentials come from `SCRAPER_DATABASE_URL` in `.env` or the environment.

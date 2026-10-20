---
paths:
  - "scraper/**/*.py"
---

# Python conventions for the scraper

These complement "Code conventions" in CLAUDE.md with the idioms the codebase already uses.
Match them so Ruff (`select = ["ALL"]`) and `mypy --strict` pass on the first run.

## Errors

- Build the message first, then raise: `msg = f"..."; raise SomeError(msg)` (Ruff EM/TRY).
- Raise from the domain hierarchy in `domain/exceptions.py`. Expected source problems are
  `ScraperError` subclasses (`SourceUnavailableError`, `SourceSchemaError`, `EmptyCatalogError`).
- Chain the cause when translating: `raise SourceSchemaError(msg) from exc`.
- Catch `Exception` only at a deliberate isolation boundary (the use case, the run audit) and
  always log it with `logger.exception`.

## Logging

- One module logger: `logger: Final = logging.getLogger(__name__)`.
- Constant message plus structured context: `logger.info("Catalog sync finished", extra={...})`.
  Never interpolate values into the message (Ruff G004); the JSON formatter emits `extra` as fields.
- Never `print`. Only `presentation/cli.py` writes to `sys.stdout` / `sys.stderr`.

## Types and data

- Constants are `Final`. Module-private names start with `_`.
- Value types are `@dataclass(frozen=True, slots=True)`; collections on them are `tuple`, not `list`.
- Accept abstract inputs (`Sequence`, `Mapping`, `Collection`), return concrete or abstract types
  consistently with the surrounding module.
- Money, sizes and rates are `Decimal`, never `float`. Datetimes are timezone-aware UTC.
- Use `Self` for fluent and factory returns, `StrEnum` for closed sets of string values.
- Use `pathlib.Path`, never `os.path`.

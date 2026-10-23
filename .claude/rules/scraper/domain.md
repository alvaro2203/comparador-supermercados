---
paths:
  - "scraper/src/supermarket_scraper/domain/**/*.py"
---

# Domain layer

`domain/` is the stable core that the future REST API will reuse. Changes here ripple everywhere.

## Purity

- Standard library only. No `pydantic`, `sqlalchemy`, `httpx`, logging handlers, environment
  access, file or network I/O.
- Never import from `application`, `infrastructure`, `presentation`, `bootstrap` or `config`.
- No current time: entities receive `datetime`/`date` as arguments. Time comes from the `Clock` port.

## Entities and value objects

- `@dataclass(frozen=True, slots=True)`. State changes return a new instance via `dataclasses.replace`
  (see `ScrapeRun.complete` / `fail`).
- Enforce invariants in `__post_init__` and raise `InvalidEntityError`. An instance that exists is valid.
- Domain types are supermarket-agnostic. Never add a field that only makes sense for one chain;
  map it to a generic concept (`SellingMethod`, `MeasureUnit`) or keep it inside that chain's scraper.
- `external_id` is the chain's own identifier, always `str`. Surrogate database ids never appear here.
- `ean` is the code as the chain publishes it (digits only). The cross-chain matching key is its
  `gtin` property, from `normalize_gtin`: left-padded to 14 digits, `None` if the check digit fails.
  Migration `0002` holds a frozen copy of that function. A `gtin` can belong to several products
  of one chain (variants such as whole and sliced bread). Treat any change to either as a
  breaking change for the comparator.
- Extend `MeasureUnit.parse` aliases (and `_SUB_UNITS` for `g`, `ml`, `cl`...) instead of
  special-casing units in a scraper. `Price.reference_amount` is per base unit (kg, l, unit, m,
  wash), always; a dozen is 12 units. For canned food it is per kg of drained weight, on purpose.
- Price changes are classified only through `Price.change_from` (`PriceChangeKind`) and counted with
  `PriceChangeCounts`. Products are shown to people through `Product.label`, never the bare name.

## Repository contracts

- `domain/repositories.py` holds abstract base classes only. A new persistence need is added here
  first, then implemented in `infrastructure/persistence/`.
- Methods take and return domain types or small frozen result dataclasses, never rows or dicts.
- Batch-oriented signatures (`Sequence[...]` in, counts out). A whole catalog is ~5,000 products;
  per-item round trips are not acceptable.
- Every repository is reachable through `UnitOfWork`; the use case never builds one directly.

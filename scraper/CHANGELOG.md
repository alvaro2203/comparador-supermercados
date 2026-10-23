## v0.2.0 (2026-10-24)

### Feat

- add and configure commitizen package for semantic release
- enhance supermarket scraper with raw JSON archiving and price reporting
- add migration to link delisted products to their successors
- Add GTIN normalization and enrichment functionality

### Fix

- add unit_name to products and update related logic in scraper and tests
- enhance MeasureUnit handling and update reference price logic in documentation and tests
- break down price changes by kind in scrape runs and update related entities
- implement successor linking for delisted products in product repository
- enhance Alembic migration integrity checks and documentation
- improve Alembic migration handling and error reporting

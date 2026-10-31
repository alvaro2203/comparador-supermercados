## v0.3.1 (2026-10-31)

### Fix

- **persistence**: recompute old reference prices that stored data con… (#6)

## v0.3.0 (2026-10-25)

### Feat

- **docs**: update daily execution guide and add GitHub Actions workflow documentation
- **cli**: add copy-db to move the history between databases
- add GitHub Actions workflow for daily scraping with configurable dry run

### Fix

- **mercadona**: read VAT from tax_percentage instead of iva (#2)

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

"""Copy a whole database into another one of the same schema, e.g. SQLite to PostgreSQL."""

from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import dataclass
from itertools import batched
from typing import TYPE_CHECKING, Final

from sqlalchemy import func, insert, select, update

from supermarket_scraper.domain.exceptions import DomainError
from supermarket_scraper.infrastructure.persistence.database import (
    migration_connection,
    schema_revision,
)
from supermarket_scraper.infrastructure.persistence.tables import (
    categories,
    price_snapshots,
    product_categories,
    products,
    scrape_runs,
    supermarkets,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy import Connection, Engine, Table

logger: Final = logging.getLogger(__name__)

COPY_ORDER: Final = (
    supermarkets,
    categories,
    products,
    product_categories,
    price_snapshots,
    scrape_runs,
)
BATCH_SIZE: Final = 1000
_SEQUENCE_TABLES: Final = (categories, products, price_snapshots)


class DataCopyError(DomainError):
    """Raised when a database cannot be copied safely, or the copy does not verify."""


class _DryRunRollbackError(Exception):
    """Raised after a verified dry run so the copy's transaction rolls back."""


@dataclass(frozen=True, slots=True)
class CopyReport:
    """Rows copied per table, and whether the copy was kept."""

    rows: Mapping[str, int]
    committed: bool


def copy_database(source: Engine, target: Engine, *, dry_run: bool = False) -> CopyReport:
    """Copy every row of ``source`` into the empty ``target``, keeping all identifiers.

    The source is only read. The target must already be at the same migration
    revision as the source and hold no rows. The copy is one transaction: row
    counts and the sum of every stored price are compared with the source before
    it commits, and any difference rolls the whole copy back. With ``dry_run``
    the copy is verified and then rolled back, leaving the target untouched.

    Raises:
        DataCopyError: The revisions differ, the target is not empty, or the copy
            does not match the source.
    """
    rows: dict[str, int] = {}
    with (
        suppress(_DryRunRollbackError),
        source.connect() as reader,
        migration_connection(target) as writer,
    ):
        _require_same_revision(reader, writer)
        _require_empty(writer)
        rows.update({table.name: _copy_table(reader, writer, table) for table in COPY_ORDER})
        _restore_product_links(reader, writer)
        _reset_sequences(writer)
        _verify(reader, writer, rows)
        if dry_run:
            raise _DryRunRollbackError
    logger.info("Database copied", extra={"rows": rows, "dry_run": dry_run})
    return CopyReport(rows=rows, committed=not dry_run)


def _require_same_revision(reader: Connection, writer: Connection) -> None:
    """Refuse to copy between two different schema versions."""
    source_revision = schema_revision(reader)
    target_revision = schema_revision(writer)
    if source_revision != target_revision:
        msg = (
            f"The source is at migration {source_revision} and the target at {target_revision}. "
            "Run init-db on both with the same code first"
        )
        raise DataCopyError(msg)


def _require_empty(writer: Connection) -> None:
    """Refuse to copy into a database that already holds data."""
    filled = [
        table.name
        for table in COPY_ORDER
        if writer.execute(select(func.count()).select_from(table)).scalar_one()
    ]
    if filled:
        msg = f"The target already has rows in {', '.join(filled)}; copy into an empty database"
        raise DataCopyError(msg)


def _copy_table(reader: Connection, writer: Connection, table: Table) -> int:
    """Copy one table in primary key order and return how many rows moved."""
    rows = reader.execute(select(table).order_by(*table.primary_key.columns)).mappings()
    copied = 0
    for batch in batched(rows, BATCH_SIZE):
        values = [dict(row) for row in batch]
        if table is products:
            values = [{**row, "replaced_by_product_id": None} for row in values]
        writer.execute(insert(table), values)
        copied += len(values)
    return copied


def _restore_product_links(reader: Connection, writer: Connection) -> None:
    """Set ``replaced_by_product_id`` once every product exists, since it points at a sibling."""
    links = reader.execute(
        select(products.c.id, products.c.replaced_by_product_id).where(
            products.c.replaced_by_product_id.is_not(None)
        )
    ).all()
    for product_id, successor_id in links:
        writer.execute(
            update(products)
            .where(products.c.id == product_id)
            .values(replaced_by_product_id=successor_id)
        )


def _reset_sequences(writer: Connection) -> None:
    """Move PostgreSQL id sequences past the copied ids, so new rows do not collide."""
    if writer.dialect.name != "postgresql":
        return
    for table in _SEQUENCE_TABLES:
        sequence = func.pg_get_serial_sequence(table.name, "id")
        highest = select(func.coalesce(func.max(table.c.id), 1)).scalar_subquery()
        writer.execute(select(func.setval(sequence, highest)))


def _verify(reader: Connection, writer: Connection, rows: Mapping[str, int]) -> None:
    """Compare row counts and money totals with the source, raising on any difference."""
    for table in COPY_ORDER:
        stored = writer.execute(select(func.count()).select_from(table)).scalar_one()
        expected = reader.execute(select(func.count()).select_from(table)).scalar_one()
        if len({stored, expected, rows[table.name]}) != 1:
            msg = f"{table.name}: the source has {expected} rows and the target {stored}"
            raise DataCopyError(msg)
    for column in (price_snapshots.c.amount, price_snapshots.c.reference_amount):
        expected_total = reader.execute(select(func.sum(column))).scalar_one()
        stored_total = writer.execute(select(func.sum(column))).scalar_one()
        if expected_total != stored_total:
            msg = f"price_snapshots.{column.name} adds up to {expected_total}, not {stored_total}"
            raise DataCopyError(msg)

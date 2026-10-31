"""Recompute reference prices stored before their format could be parsed.

Snapshots captured before ``MeasureUnit.parse_quantity`` understood ``100 g``, ``100 ml``,
``lv`` and ``dc`` (18 to 23 October 2026) kept ``reference_unit = 'unknown'`` and the chain's
figure per 100 g, per wash or per dozen. A row is corrected only when the right value can be
checked against data already stored:

* A later snapshot of the same product, at the same shelf price, has a known unit and a
  reference equal to the old one times a format factor (10 for 100 g or 100 ml, 1 for a wash,
  1/12 for a dozen). The row takes that snapshot's unit and reference.
* Otherwise, the old reference times 10 equals the row's own ``bulk_amount``, the chain's price
  per kg or l of the product's size format. The row takes the product's latest known unit (or,
  without one, its ``size_format``) when it is kg or l, and the reference times 10.

Every other row keeps ``unknown``. No column changes, and no other row is written.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-31 12:00:00.000000
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from itertools import batched
from typing import TYPE_CHECKING, Any, Final

import sqlalchemy as sa
from alembic import op

from supermarket_scraper.infrastructure.persistence import column_types

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: Final = "0006"
down_revision: Final[str | None] = "0005"
branch_labels: Final[str | Sequence[str] | None] = None
depends_on: Final[str | Sequence[str] | None] = None

_UNKNOWN: Final = "unknown"
_FORMAT_FACTORS: Final = (Decimal(10), Decimal(1), Decimal(1) / Decimal(12))
_PER_HUNDRED_FACTOR: Final = Decimal(10)
_TOLERANCE: Final = Decimal("0.01")
_BULK_UNITS: Final = frozenset({"kg", "l"})
_CHUNK: Final = 500

_snapshots: Final = sa.table(
    "price_snapshots",
    sa.column("id", sa.Integer),
    sa.column("product_id", sa.Integer),
    sa.column("captured_on", sa.Date),
    sa.column("amount", column_types.ExactDecimal()),
    sa.column("reference_amount", column_types.ExactDecimal()),
    sa.column("reference_unit", sa.String),
    sa.column("bulk_amount", column_types.ExactDecimal()),
)
_products: Final = sa.table("products", sa.column("id", sa.Integer), sa.column("size_format"))


def upgrade() -> None:
    """Correct every unknown reference price that later or bulk data confirms."""
    connection = op.get_bind()
    unknown = connection.execute(
        sa.select(_snapshots).where(
            _snapshots.c.reference_unit == _UNKNOWN, _snapshots.c.reference_amount > Decimal(0)
        )
    ).all()
    if not unknown:
        return
    product_ids = sorted({row.product_id for row in unknown})
    known: defaultdict[int, list[sa.Row[Any]]] = defaultdict(list)
    size_formats: dict[int, str | None] = {}
    for chunk in batched(product_ids, _CHUNK):
        for row in connection.execute(
            sa.select(_snapshots)
            .where(
                _snapshots.c.product_id.in_(chunk),
                _snapshots.c.reference_unit != _UNKNOWN,
            )
            .order_by(_snapshots.c.product_id, _snapshots.c.captured_on)
        ):
            known[row.product_id].append(row)
        formats = connection.execute(
            sa.select(_products.c.id, _products.c.size_format).where(_products.c.id.in_(chunk))
        )
        size_formats.update((row.id, row.size_format) for row in formats)
    fixes = [
        fix
        for row in unknown
        if (fix := _corrected(row, known[row.product_id], size_formats.get(row.product_id)))
    ]
    if fixes:
        connection.execute(
            _snapshots.update()
            .where(_snapshots.c.id == sa.bindparam("b_id"))
            .values(
                reference_unit=sa.bindparam("b_unit"),
                reference_amount=sa.bindparam("b_reference"),
            ),
            fixes,
        )


def _corrected(
    row: sa.Row[Any], known: Sequence[sa.Row[Any]], size_format: str | None
) -> dict[str, object] | None:
    """Return the update for one unknown row, or ``None`` when no stored data confirms it."""
    later = next(
        (
            snapshot
            for snapshot in known
            if snapshot.captured_on > row.captured_on and snapshot.amount == row.amount
        ),
        None,
    )
    if later is not None and any(
        abs(later.reference_amount - row.reference_amount * factor) <= _TOLERANCE
        for factor in _FORMAT_FACTORS
    ):
        return {
            "b_id": row.id,
            "b_unit": later.reference_unit,
            "b_reference": later.reference_amount,
        }
    per_base_unit = row.reference_amount * _PER_HUNDRED_FACTOR
    unit = known[-1].reference_unit if known else size_format
    if (
        row.bulk_amount is not None
        and abs(per_base_unit - row.bulk_amount) <= _TOLERANCE
        and unit in _BULK_UNITS
    ):
        return {"b_id": row.id, "b_unit": unit, "b_reference": per_base_unit}
    return None


def downgrade() -> None:
    """Keep the corrected values: the schema did not change and the old figures were wrong."""

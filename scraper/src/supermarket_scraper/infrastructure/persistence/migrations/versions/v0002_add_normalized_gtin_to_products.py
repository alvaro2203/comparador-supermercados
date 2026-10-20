"""Add normalized GTIN to products.

``gtin`` is the EAN left-padded to 14 digits, or NULL when the EAN is missing or
fails its check digit. Existing products are backfilled from their stored EAN.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-20 23:16:41.280173
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: Final = "0002"
down_revision: Final[str | None] = "0001"
branch_labels: Final[str | Sequence[str] | None] = None
depends_on: Final[str | Sequence[str] | None] = None

_GTIN_CANDIDATE: Final = re.compile(r"^[0-9]{8,14}$")
_GTIN_LENGTH: Final = 14


def _gtin(code: str) -> str | None:
    """Frozen copy of ``normalize_gtin`` as of this revision, so the backfill never drifts."""
    if not _GTIN_CANDIDATE.fullmatch(code):
        return None
    gtin = code.zfill(_GTIN_LENGTH)
    *body, check = (int(digit) for digit in gtin)
    weighted = sum(digit * (3 if position % 2 == 0 else 1) for position, digit in enumerate(body))
    return gtin if (10 - weighted % 10) % 10 == check else None


def upgrade() -> None:
    """Add the ``gtin`` column and index, then fill it from the EANs already stored."""
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.add_column(sa.Column("gtin", sa.String(length=14), nullable=True))
        batch_op.create_index("ix_products_gtin", ["gtin"], unique=False)

    products = sa.table(
        "products", sa.column("id", sa.Integer), sa.column("ean"), sa.column("gtin")
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(products.c.id, products.c.ean).where(products.c.ean.is_not(None))
    )
    backfill = [
        {"b_id": row.id, "b_gtin": gtin} for row in rows if (gtin := _gtin(row.ean)) is not None
    ]
    if backfill:
        connection.execute(
            products.update()
            .where(products.c.id == sa.bindparam("b_id"))
            .values(gtin=sa.bindparam("b_gtin")),
            backfill,
        )


def downgrade() -> None:
    """Drop ``gtin``, which loses nothing: it is derived from ``ean`` and is rebuilt on upgrade."""
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.drop_index("ix_products_gtin")
        batch_op.drop_column("gtin")

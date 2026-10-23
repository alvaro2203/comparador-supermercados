"""Link delisted products to their successor.

Adds ``products.replaced_by_product_id``: on a delisted product, the product that
relisted the same article (same GTIN) under a new external id. On SQLite, adding
the foreign key rebuilds ``products``; ``migration_connection`` keeps that rebuild
from cascading into ``price_snapshots``. Links are filled by the next run.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23 17:45:55.448668
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: Final = "0003"
down_revision: Final[str | None] = "0002"
branch_labels: Final[str | Sequence[str] | None] = None
depends_on: Final[str | Sequence[str] | None] = None


def upgrade() -> None:
    """Add the nullable self-referencing ``replaced_by_product_id`` column and its index."""
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.add_column(sa.Column("replaced_by_product_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            "ix_products_replaced_by_product_id", ["replaced_by_product_id"], unique=False
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_products_replaced_by_product_id_products"),
            "products",
            ["replaced_by_product_id"],
            ["id"],
        )


def downgrade() -> None:
    """Drop the links, which loses nothing: the next run recomputes them from ``gtin``."""
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_products_replaced_by_product_id_products"), type_="foreignkey"
        )
        batch_op.drop_index("ix_products_replaced_by_product_id")
        batch_op.drop_column("replaced_by_product_id")

"""Add unit name to products.

Adds the nullable ``products.unit_name``: what ``total_units`` counts (``lavados``,
``pastillas``...). Existing products get it on the next run, which refreshes it.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-23 23:13:40.218109
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: Final = "0005"
down_revision: Final[str | None] = "0004"
branch_labels: Final[str | Sequence[str] | None] = None
depends_on: Final[str | Sequence[str] | None] = None


def upgrade() -> None:
    """Add the nullable ``unit_name`` column."""
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.add_column(sa.Column("unit_name", sa.String(length=40), nullable=True))


def downgrade() -> None:
    """Drop ``unit_name``, which loses nothing: every run reads it again from the catalog."""
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.drop_column("unit_name")

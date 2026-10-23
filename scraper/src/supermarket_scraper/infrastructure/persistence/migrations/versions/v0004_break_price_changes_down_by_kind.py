"""Break price changes down by kind.

Adds four nullable counters to ``scrape_runs``: regular price increases and
decreases, and promotions started and ended. Runs recorded before this revision
keep NULL, meaning "not classified", which is different from zero.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-23 22:45:47.084501
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: Final = "0004"
down_revision: Final[str | None] = "0003"
branch_labels: Final[str | Sequence[str] | None] = None
depends_on: Final[str | Sequence[str] | None] = None


def upgrade() -> None:
    """Add the four nullable breakdown counters."""
    with op.batch_alter_table("scrape_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("price_increases", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("price_decreases", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("promotions_started", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("promotions_ended", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Refuse to drop the counters: each run's breakdown cannot be rebuilt afterwards."""
    msg = "Revision 0004 is irreversible: reverting it would delete the price change breakdown"
    raise NotImplementedError(msg)

"""${message.rstrip(".")}.

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}
from supermarket_scraper.infrastructure.persistence import column_types

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: Final = ${repr(up_revision)}
down_revision: Final[str | None] = ${repr(down_revision)}
branch_labels: Final[str | Sequence[str] | None] = ${repr(branch_labels)}
depends_on: Final[str | Sequence[str] | None] = ${repr(depends_on)}


def upgrade() -> None:
    """Apply this revision."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Revert this revision without deleting products, categories or price history."""
    ${downgrades if downgrades else "pass"}

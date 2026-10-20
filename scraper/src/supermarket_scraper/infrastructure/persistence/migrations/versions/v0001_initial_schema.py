"""Initial schema: every table as ``create_all`` built it before migrations existed.

Revision ID: 0001
Revises:
Create Date: 2026-10-18 03:14:07.192144
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import sqlalchemy as sa
from alembic import op

from supermarket_scraper.infrastructure.persistence import column_types

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: Final = "0001"
down_revision: Final[str | None] = None
branch_labels: Final[str | Sequence[str] | None] = None
depends_on: Final[str | Sequence[str] | None] = None


def upgrade() -> None:
    """Create every table, constraint and index of the baseline schema."""
    op.create_table(
        "supermarkets",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("website", sa.String(length=255), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("created_at", column_types.UtcDateTime(), nullable=False),
        sa.Column("updated_at", column_types.UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_supermarkets")),
    )
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("supermarket_code", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("parent_external_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", column_types.UtcDateTime(), nullable=False),
        sa.Column("last_seen_at", column_types.UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["supermarket_code"],
            ["supermarkets.code"],
            name=op.f("fk_categories_supermarket_code_supermarkets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint(
            "supermarket_code",
            "external_id",
            name=op.f("uq_categories_supermarket_code_external_id"),
        ),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("supermarket_code", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("slug", sa.String(length=500), nullable=True),
        sa.Column("brand", sa.String(length=255), nullable=True),
        sa.Column("ean", sa.String(length=32), nullable=True),
        sa.Column("packaging", sa.String(length=120), nullable=True),
        sa.Column("unit_size", column_types.ExactDecimal(precision=14, scale=4), nullable=True),
        sa.Column("size_format", sa.String(length=32), nullable=True),
        sa.Column("is_pack", sa.Boolean(), nullable=False),
        sa.Column("pack_size", column_types.ExactDecimal(precision=14, scale=4), nullable=True),
        sa.Column("total_units", sa.Integer(), nullable=True),
        sa.Column("selling_method", sa.String(length=16), nullable=False),
        sa.Column("is_approximate_size", sa.Boolean(), nullable=False),
        sa.Column("thumbnail_url", sa.String(length=1000), nullable=True),
        sa.Column("share_url", sa.String(length=1000), nullable=True),
        sa.Column("is_available", sa.Boolean(), nullable=False),
        sa.Column("is_new_arrival", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=True),
        sa.Column("legal_name", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("ingredients", sa.Text(), nullable=True),
        sa.Column("allergens", sa.Text(), nullable=True),
        sa.Column("image_url", sa.String(length=1000), nullable=True),
        sa.Column("details_fetched_at", column_types.UtcDateTime(), nullable=True),
        sa.Column("first_seen_at", column_types.UtcDateTime(), nullable=False),
        sa.Column("last_seen_at", column_types.UtcDateTime(), nullable=False),
        sa.Column("delisted_at", column_types.UtcDateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["supermarket_code"],
            ["supermarkets.code"],
            name=op.f("fk_products_supermarket_code_supermarkets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.UniqueConstraint(
            "supermarket_code", "external_id", name=op.f("uq_products_supermarket_code_external_id")
        ),
    )
    op.create_index("ix_products_ean", "products", ["ean"], unique=False)
    op.create_index(
        "ix_products_supermarket_active",
        "products",
        ["supermarket_code", "is_active"],
        unique=False,
    )

    op.create_table(
        "scrape_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("supermarket_code", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", column_types.UtcDateTime(), nullable=False),
        sa.Column("finished_at", column_types.UtcDateTime(), nullable=True),
        sa.Column("categories_count", sa.Integer(), nullable=False),
        sa.Column("products_count", sa.Integer(), nullable=False),
        sa.Column("new_products", sa.Integer(), nullable=False),
        sa.Column("delisted_products", sa.Integer(), nullable=False),
        sa.Column("price_changes", sa.Integer(), nullable=False),
        sa.Column("details_enriched", sa.Integer(), nullable=False),
        sa.Column("errors_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["supermarket_code"],
            ["supermarkets.code"],
            name=op.f("fk_scrape_runs_supermarket_code_supermarkets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scrape_runs")),
    )
    op.create_index(
        "ix_scrape_runs_supermarket_started",
        "scrape_runs",
        ["supermarket_code", "started_at"],
        unique=False,
    )

    op.create_table(
        "price_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("captured_on", sa.Date(), nullable=False),
        sa.Column("captured_at", column_types.UtcDateTime(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount", column_types.ExactDecimal(precision=14, scale=4), nullable=False),
        sa.Column(
            "reference_amount", column_types.ExactDecimal(precision=14, scale=4), nullable=True
        ),
        sa.Column("reference_unit", sa.String(length=16), nullable=False),
        sa.Column(
            "previous_amount", column_types.ExactDecimal(precision=14, scale=4), nullable=True
        ),
        sa.Column("bulk_amount", column_types.ExactDecimal(precision=14, scale=4), nullable=True),
        sa.Column("vat_rate", column_types.ExactDecimal(precision=14, scale=4), nullable=True),
        sa.Column("is_promotion", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_price_snapshots_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_price_snapshots")),
        sa.UniqueConstraint(
            "product_id", "captured_on", name=op.f("uq_price_snapshots_product_id_captured_on")
        ),
    )
    op.create_index(
        "ix_price_snapshots_captured_on", "price_snapshots", ["captured_on"], unique=False
    )

    op.create_table(
        "product_categories",
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name=op.f("fk_product_categories_category_id_categories"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_categories_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("product_id", "category_id", name=op.f("pk_product_categories")),
    )


def downgrade() -> None:
    """Refuse to revert the baseline, which would drop every table and the price history."""
    msg = "The baseline revision is irreversible: reverting it would delete all stored data"
    raise NotImplementedError(msg)

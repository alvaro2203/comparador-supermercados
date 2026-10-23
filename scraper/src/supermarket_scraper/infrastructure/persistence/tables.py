from __future__ import annotations

from typing import Final

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from supermarket_scraper.infrastructure.persistence.column_types import ExactDecimal, UtcDateTime

NAMING_CONVENTION: Final = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata: Final = MetaData(naming_convention=NAMING_CONVENTION)

supermarkets: Final = Table(
    "supermarkets",
    metadata,
    Column("code", String(32), primary_key=True),
    Column("name", String(120), nullable=False),
    Column("website", String(255), nullable=False),
    Column("country_code", String(2), nullable=False),
    Column("created_at", UtcDateTime(), nullable=False),
    Column("updated_at", UtcDateTime(), nullable=False),
)

categories: Final = Table(
    "categories",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "supermarket_code",
        String(32),
        ForeignKey("supermarkets.code", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("external_id", String(64), nullable=False),
    Column("parent_external_id", String(64), nullable=True),
    Column("name", String(255), nullable=False),
    Column("level", Integer, nullable=False),
    Column("position", Integer, nullable=False, default=0),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("first_seen_at", UtcDateTime(), nullable=False),
    Column("last_seen_at", UtcDateTime(), nullable=False),
    UniqueConstraint("supermarket_code", "external_id"),
)

products: Final = Table(
    "products",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "supermarket_code",
        String(32),
        ForeignKey("supermarkets.code", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("external_id", String(64), nullable=False),
    Column("name", String(500), nullable=False),
    Column("slug", String(500), nullable=True),
    Column("brand", String(255), nullable=True),
    Column("ean", String(32), nullable=True),
    Column("gtin", String(14), nullable=True),
    Column("packaging", String(120), nullable=True),
    Column("unit_size", ExactDecimal(), nullable=True),
    Column("size_format", String(32), nullable=True),
    Column("is_pack", Boolean, nullable=False, default=False),
    Column("pack_size", ExactDecimal(), nullable=True),
    Column("total_units", Integer, nullable=True),
    Column("unit_name", String(40), nullable=True),
    Column("selling_method", String(16), nullable=False),
    Column("is_approximate_size", Boolean, nullable=False, default=False),
    Column("thumbnail_url", String(1000), nullable=True),
    Column("share_url", String(1000), nullable=True),
    Column("is_available", Boolean, nullable=False, default=True),
    Column("is_new_arrival", Boolean, nullable=False, default=False),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("origin", Text, nullable=True),
    Column("legal_name", Text, nullable=True),
    Column("description", Text, nullable=True),
    Column("ingredients", Text, nullable=True),
    Column("allergens", Text, nullable=True),
    Column("image_url", String(1000), nullable=True),
    Column("details_fetched_at", UtcDateTime(), nullable=True),
    Column("first_seen_at", UtcDateTime(), nullable=False),
    Column("last_seen_at", UtcDateTime(), nullable=False),
    Column("delisted_at", UtcDateTime(), nullable=True),
    Column("replaced_by_product_id", Integer, ForeignKey("products.id"), nullable=True),
    UniqueConstraint("supermarket_code", "external_id"),
    Index("ix_products_ean", "ean"),
    Index("ix_products_gtin", "gtin"),
    Index("ix_products_replaced_by_product_id", "replaced_by_product_id"),
    Index("ix_products_supermarket_active", "supermarket_code", "is_active"),
)

product_categories: Final = Table(
    "product_categories",
    metadata,
    Column(
        "product_id",
        Integer,
        ForeignKey("products.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "category_id",
        Integer,
        ForeignKey("categories.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

price_snapshots: Final = Table(
    "price_snapshots",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "product_id",
        Integer,
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("captured_on", Date, nullable=False),
    Column("captured_at", UtcDateTime(), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("amount", ExactDecimal(), nullable=False),
    Column("reference_amount", ExactDecimal(), nullable=True),
    Column("reference_unit", String(16), nullable=False),
    Column("previous_amount", ExactDecimal(), nullable=True),
    Column("bulk_amount", ExactDecimal(), nullable=True),
    Column("vat_rate", ExactDecimal(), nullable=True),
    Column("is_promotion", Boolean, nullable=False, default=False),
    UniqueConstraint("product_id", "captured_on"),
    Index("ix_price_snapshots_captured_on", "captured_on"),
)

scrape_runs: Final = Table(
    "scrape_runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "supermarket_code",
        String(32),
        ForeignKey("supermarkets.code", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("status", String(16), nullable=False),
    Column("started_at", UtcDateTime(), nullable=False),
    Column("finished_at", UtcDateTime(), nullable=True),
    Column("categories_count", Integer, nullable=False, default=0),
    Column("products_count", Integer, nullable=False, default=0),
    Column("new_products", Integer, nullable=False, default=0),
    Column("delisted_products", Integer, nullable=False, default=0),
    Column("price_changes", Integer, nullable=False, default=0),
    Column("price_increases", Integer, nullable=True),
    Column("price_decreases", Integer, nullable=True),
    Column("promotions_started", Integer, nullable=True),
    Column("promotions_ended", Integer, nullable=True),
    Column("details_enriched", Integer, nullable=False, default=0),
    Column("errors_count", Integer, nullable=False, default=0),
    Column("error_message", Text, nullable=True),
    Index("ix_scrape_runs_supermarket_started", "supermarket_code", "started_at"),
)

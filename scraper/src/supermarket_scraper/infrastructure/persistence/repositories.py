from __future__ import annotations

from collections import defaultdict
from itertools import batched
from typing import TYPE_CHECKING, Final, override

from sqlalchemy import Table, and_, bindparam, case, delete, func, insert, select, update

from supermarket_scraper.domain.entities import (
    PriceChangeCounts,
    RunStats,
    RunStatus,
    ScrapeRun,
    Supermarket,
)
from supermarket_scraper.domain.repositories import (
    CategoryRepository,
    PriceRecordResult,
    PriceRepository,
    ProductRepository,
    ProductUpsertResult,
    ScrapeRunRepository,
    SupermarketRepository,
)
from supermarket_scraper.domain.value_objects import Price, SupermarketCode
from supermarket_scraper.infrastructure.persistence import tables as schema
from supermarket_scraper.infrastructure.persistence.upsert import upsert

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence
    from datetime import date, datetime

    from sqlalchemy import Connection
    from sqlalchemy.sql.elements import ColumnElement

    from supermarket_scraper.domain.entities import (
        Category,
        PriceObservation,
        Product,
        ProductDetails,
    )
    from supermarket_scraper.domain.value_objects import PriceChangeKind
    from supermarket_scraper.infrastructure.persistence.upsert import Row

IN_CLAUSE_CHUNK: Final = 500

_PRODUCT_CATALOG_COLUMNS: Final = (
    "name",
    "slug",
    "packaging",
    "unit_size",
    "size_format",
    "is_pack",
    "pack_size",
    "total_units",
    "unit_name",
    "selling_method",
    "is_approximate_size",
    "thumbnail_url",
    "share_url",
    "is_available",
    "is_new_arrival",
    "is_active",
    "last_seen_at",
    "delisted_at",
    "replaced_by_product_id",
)

_PRICE_COLUMNS: Final = (
    "captured_at",
    "currency",
    "amount",
    "reference_amount",
    "reference_unit",
    "previous_amount",
    "bulk_amount",
    "vat_rate",
    "is_promotion",
)


class SqlSupermarketRepository(SupermarketRepository):
    """SQLAlchemy Core implementation of ``SupermarketRepository``."""

    def __init__(self, connection: Connection, now: datetime) -> None:
        """Bind the repository to an open connection."""
        self._connection = connection
        self._now = now

    @override
    def upsert(self, supermarket: Supermarket) -> None:
        """Insert the supermarket or refresh its descriptive attributes."""
        row: Row = {
            "code": str(supermarket.code),
            "name": supermarket.name,
            "website": supermarket.website,
            "country_code": supermarket.country_code,
            "created_at": self._now,
            "updated_at": self._now,
        }
        upsert(
            self._connection,
            schema.supermarkets,
            [row],
            conflict_columns=("code",),
            update_columns=("name", "website", "country_code", "updated_at"),
        )

    @override
    def list_all(self) -> Sequence[Supermarket]:
        """Return every registered supermarket ordered by code."""
        table = schema.supermarkets
        result = self._connection.execute(
            select(table.c.code, table.c.name, table.c.website, table.c.country_code).order_by(
                table.c.code
            )
        )
        return [
            Supermarket(
                code=SupermarketCode(row.code),
                name=row.name,
                website=row.website,
                country_code=row.country_code,
            )
            for row in result
        ]


class SqlCategoryRepository(CategoryRepository):
    """SQLAlchemy Core implementation of ``CategoryRepository``."""

    def __init__(self, connection: Connection) -> None:
        """Bind the repository to an open connection."""
        self._connection = connection

    @override
    def sync(
        self, supermarket: SupermarketCode, categories: Sequence[Category], seen_at: datetime
    ) -> int:
        """Upsert categories, reactivating any that had disappeared."""
        rows: list[Row] = [
            {
                "supermarket_code": str(supermarket),
                "external_id": category.external_id,
                "parent_external_id": category.parent_external_id,
                "name": category.name,
                "level": category.level,
                "position": category.position,
                "is_active": True,
                "first_seen_at": seen_at,
                "last_seen_at": seen_at,
            }
            for category in categories
        ]
        upsert(
            self._connection,
            schema.categories,
            rows,
            conflict_columns=("supermarket_code", "external_id"),
            update_columns=(
                "parent_external_id",
                "name",
                "level",
                "position",
                "is_active",
                "last_seen_at",
            ),
        )
        return len(rows)

    @override
    def deactivate_missing(
        self, supermarket: SupermarketCode, seen_external_ids: Collection[str], at: datetime
    ) -> int:
        """Deactivate categories that were not part of the latest catalog."""
        table = schema.categories
        active = _external_id_map(self._connection, table, supermarket, only_active=True)
        missing = [external_id for external_id in active if external_id not in seen_external_ids]
        for chunk in batched(missing, IN_CLAUSE_CHUNK):
            self._connection.execute(
                update(table)
                .where(table.c.supermarket_code == str(supermarket), table.c.external_id.in_(chunk))
                .values(is_active=False)
            )
        return len(missing)


class SqlProductRepository(ProductRepository):
    """SQLAlchemy Core implementation of ``ProductRepository``."""

    def __init__(self, connection: Connection) -> None:
        """Bind the repository to an open connection."""
        self._connection = connection

    @override
    def sync(
        self, supermarket: SupermarketCode, products: Sequence[Product], seen_at: datetime
    ) -> ProductUpsertResult:
        """Upsert products, refresh their category links and report the new ones."""
        known = _external_id_map(self._connection, schema.products, supermarket)
        upsert(
            self._connection,
            schema.products,
            [_product_row(product, seen_at) for product in products],
            conflict_columns=("supermarket_code", "external_id"),
            update_columns=_PRODUCT_CATALOG_COLUMNS,
        )
        self._update_identity_attributes(supermarket, products)
        self._relink_categories(supermarket, products)
        new_ids = tuple(
            product.external_id for product in products if product.external_id not in known
        )
        return ProductUpsertResult(
            inserted=len(new_ids),
            updated=len(products) - len(new_ids),
            new_external_ids=new_ids,
        )

    @override
    def count_active(self, supermarket: SupermarketCode) -> int:
        """Count products currently listed by the supermarket."""
        table = schema.products
        statement = (
            select(func.count())
            .select_from(table)
            .where(table.c.supermarket_code == str(supermarket), table.c.is_active)
        )
        return int(self._connection.execute(statement).scalar_one())

    @override
    def delist_missing(
        self, supermarket: SupermarketCode, seen_external_ids: Collection[str], at: datetime
    ) -> int:
        """Mark products absent from the latest catalog as delisted."""
        table = schema.products
        active = _external_id_map(self._connection, table, supermarket, only_active=True)
        missing = [external_id for external_id in active if external_id not in seen_external_ids]
        for chunk in batched(missing, IN_CLAUSE_CHUNK):
            self._connection.execute(
                update(table)
                .where(table.c.supermarket_code == str(supermarket), table.c.external_id.in_(chunk))
                .values(is_active=False, is_available=False, delisted_at=at)
            )
        return len(missing)

    @override
    def external_ids_missing_details(
        self, supermarket: SupermarketCode, limit: int
    ) -> Sequence[str]:
        """Return active products never enriched, oldest first."""
        table = schema.products
        statement = (
            select(table.c.external_id)
            .where(
                table.c.supermarket_code == str(supermarket),
                table.c.is_active,
                table.c.details_fetched_at.is_(None),
            )
            .order_by(table.c.first_seen_at, table.c.id)
            .limit(limit)
        )
        return list(self._connection.execute(statement).scalars())

    @override
    def apply_details(self, details: Sequence[ProductDetails], at: datetime) -> int:
        """Store detail attributes, keeping previous values when the source omits them."""
        if not details:
            return 0
        table = schema.products
        statement = (
            update(table)
            .where(
                table.c.supermarket_code == bindparam("b_supermarket"),
                table.c.external_id == bindparam("b_external_id"),
            )
            .values(
                ean=func.coalesce(bindparam("b_ean", type_=table.c.ean.type), table.c.ean),
                gtin=_gtin_following_ean(table),
                brand=func.coalesce(bindparam("b_brand", type_=table.c.brand.type), table.c.brand),
                origin=bindparam("b_origin"),
                legal_name=bindparam("b_legal_name"),
                description=bindparam("b_description"),
                ingredients=bindparam("b_ingredients"),
                allergens=bindparam("b_allergens"),
                image_url=func.coalesce(
                    bindparam("b_image_url", type_=table.c.image_url.type), table.c.image_url
                ),
                details_fetched_at=bindparam("b_fetched_at"),
            )
        )
        parameters = [
            {
                "b_supermarket": str(item.supermarket),
                "b_external_id": item.external_id,
                "b_ean": item.ean,
                "b_gtin": item.gtin,
                "b_brand": item.brand,
                "b_origin": item.origin,
                "b_legal_name": item.legal_name,
                "b_description": item.description,
                "b_ingredients": item.ingredients,
                "b_allergens": item.allergens,
                "b_image_url": item.image_url,
                "b_fetched_at": at,
            }
            for item in details
        ]
        self._connection.execute(statement, parameters)
        return len(parameters)

    @override
    def link_successors(self, supermarket: SupermarketCode) -> int:
        """Point unlinked delisted products at their single relisted successor."""
        delisted = schema.products
        relisted = schema.products.alias("relisted")
        statement = (
            select(delisted.c.id, relisted.c.id.label("successor_id"))
            .join(
                relisted,
                and_(
                    relisted.c.supermarket_code == delisted.c.supermarket_code,
                    relisted.c.gtin == delisted.c.gtin,
                    relisted.c.is_active,
                    relisted.c.first_seen_at > delisted.c.last_seen_at,
                ),
            )
            .where(
                delisted.c.supermarket_code == str(supermarket),
                delisted.c.is_active.is_(False),
                delisted.c.gtin.is_not(None),
                delisted.c.replaced_by_product_id.is_(None),
            )
        )
        candidates: defaultdict[int, list[int]] = defaultdict(list)
        for row in self._connection.execute(statement):
            candidates[row.id].append(row.successor_id)
        parameters = [
            {"b_id": product_id, "b_successor_id": successor_ids[0]}
            for product_id, successor_ids in candidates.items()
            if len(successor_ids) == 1
        ]
        if parameters:
            self._connection.execute(
                update(delisted)
                .where(delisted.c.id == bindparam("b_id"))
                .values(replaced_by_product_id=bindparam("b_successor_id")),
                parameters,
            )
        return len(parameters)

    def _update_identity_attributes(
        self, supermarket: SupermarketCode, items: Sequence[Product]
    ) -> None:
        """Write brand/EAN only when the listing provides them, never erasing enriched data."""
        parameters = [
            {
                "b_supermarket": str(supermarket),
                "b_external_id": product.external_id,
                "b_brand": product.brand,
                "b_ean": product.ean,
                "b_gtin": product.gtin,
            }
            for product in items
            if product.brand is not None or product.ean is not None
        ]
        if not parameters:
            return
        table = schema.products
        statement = (
            update(table)
            .where(
                table.c.supermarket_code == bindparam("b_supermarket"),
                table.c.external_id == bindparam("b_external_id"),
            )
            .values(
                brand=func.coalesce(bindparam("b_brand", type_=table.c.brand.type), table.c.brand),
                ean=func.coalesce(bindparam("b_ean", type_=table.c.ean.type), table.c.ean),
                gtin=_gtin_following_ean(table),
            )
        )
        self._connection.execute(statement, parameters)

    def _relink_categories(self, supermarket: SupermarketCode, items: Sequence[Product]) -> None:
        """Replace the category links of the given products with the latest ones."""
        links_table = schema.product_categories
        product_ids = _external_id_map(self._connection, schema.products, supermarket)
        category_ids = _external_id_map(self._connection, schema.categories, supermarket)
        touched = [product_ids[product.external_id] for product in items]
        for chunk in batched(touched, IN_CLAUSE_CHUNK):
            self._connection.execute(delete(links_table).where(links_table.c.product_id.in_(chunk)))
        links = [
            {"product_id": product_ids[product.external_id], "category_id": category_ids[ref]}
            for product in items
            for ref in dict.fromkeys(product.category_external_ids)
            if ref in category_ids
        ]
        for link_chunk in batched(links, IN_CLAUSE_CHUNK):
            self._connection.execute(insert(links_table), list(link_chunk))


class SqlPriceRepository(PriceRepository):
    """SQLAlchemy Core implementation of ``PriceRepository``."""

    def __init__(self, connection: Connection) -> None:
        """Bind the repository to an open connection."""
        self._connection = connection

    @override
    def record(self, observations: Sequence[PriceObservation]) -> PriceRecordResult:
        """Upsert one snapshot per product and day and classify changes versus the prior day."""
        grouped: defaultdict[tuple[SupermarketCode, date], list[PriceObservation]] = defaultdict(
            list
        )
        for observation in observations:
            grouped[observation.supermarket, observation.captured_on].append(observation)
        recorded = 0
        kinds: list[PriceChangeKind] = []
        for (supermarket, day), batch in grouped.items():
            product_ids = _external_id_map(self._connection, schema.products, supermarket)
            previous = self._previous_prices(supermarket, day)
            rows: list[Row] = []
            for observation in batch:
                product_id = product_ids.get(observation.product_external_id)
                if product_id is None:
                    continue
                price = observation.price
                before = previous.get(product_id)
                kind = price.change_from(before) if before is not None else None
                if kind is not None:
                    kinds.append(kind)
                rows.append(
                    {
                        "product_id": product_id,
                        "captured_on": observation.captured_on,
                        "captured_at": observation.captured_at,
                        "currency": price.currency,
                        "amount": price.amount,
                        "reference_amount": price.reference_amount,
                        "reference_unit": price.reference_unit.value,
                        "previous_amount": price.previous_amount,
                        "bulk_amount": price.bulk_amount,
                        "vat_rate": price.vat_rate,
                        "is_promotion": price.is_promotion,
                    }
                )
            upsert(
                self._connection,
                schema.price_snapshots,
                rows,
                conflict_columns=("product_id", "captured_on"),
                update_columns=_PRICE_COLUMNS,
            )
            recorded += len(rows)
        return PriceRecordResult(recorded=recorded, kinds=PriceChangeCounts.tally(kinds))

    def _previous_prices(self, supermarket: SupermarketCode, day: date) -> dict[int, Price]:
        """Return the latest shelf price before ``day`` for every product of a supermarket."""
        prices = schema.price_snapshots
        products = schema.products
        latest = (
            select(prices.c.product_id, func.max(prices.c.captured_on).label("captured_on"))
            .join(products, products.c.id == prices.c.product_id)
            .where(products.c.supermarket_code == str(supermarket), prices.c.captured_on < day)
            .group_by(prices.c.product_id)
            .subquery()
        )
        statement = select(
            prices.c.product_id, prices.c.currency, prices.c.amount, prices.c.is_promotion
        ).join(
            latest,
            and_(
                prices.c.product_id == latest.c.product_id,
                prices.c.captured_on == latest.c.captured_on,
            ),
        )
        return {
            row.product_id: Price(
                amount=row.amount, currency=row.currency, is_promotion=bool(row.is_promotion)
            )
            for row in self._connection.execute(statement)
        }


class SqlScrapeRunRepository(ScrapeRunRepository):
    """SQLAlchemy Core implementation of ``ScrapeRunRepository``."""

    def __init__(self, connection: Connection) -> None:
        """Bind the repository to an open connection."""
        self._connection = connection

    @override
    def save(self, run: ScrapeRun) -> None:
        """Insert or update the run identified by ``run.id``."""
        row: Row = {
            "id": run.id,
            "supermarket_code": str(run.supermarket),
            "status": run.status.value,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "categories_count": run.stats.categories,
            "products_count": run.stats.products,
            "new_products": run.stats.new_products,
            "delisted_products": run.stats.delisted_products,
            "price_changes": run.stats.price_changes,
            **_price_change_kind_columns(run.stats.price_change_kinds),
            "details_enriched": run.stats.details_enriched,
            "errors_count": run.stats.errors,
            "error_message": run.error_message,
        }
        upsert(
            self._connection,
            schema.scrape_runs,
            [row],
            conflict_columns=("id",),
            update_columns=tuple(column for column in row if column != "id"),
        )

    @override
    def latest(self, supermarket: SupermarketCode, limit: int) -> Sequence[ScrapeRun]:
        """Return the newest runs of a supermarket."""
        table = schema.scrape_runs
        statement = (
            select(table)
            .where(table.c.supermarket_code == str(supermarket))
            .order_by(table.c.started_at.desc())
            .limit(limit)
        )
        return [
            ScrapeRun(
                id=row.id,
                supermarket=SupermarketCode(row.supermarket_code),
                started_at=row.started_at,
                status=RunStatus(row.status),
                finished_at=row.finished_at,
                stats=RunStats(
                    categories=row.categories_count,
                    products=row.products_count,
                    new_products=row.new_products,
                    delisted_products=row.delisted_products,
                    price_changes=row.price_changes,
                    price_change_kinds=_price_change_kinds(
                        row.price_increases,
                        row.price_decreases,
                        row.promotions_started,
                        row.promotions_ended,
                    ),
                    details_enriched=row.details_enriched,
                    errors=row.errors_count,
                ),
                error_message=row.error_message,
            )
            for row in self._connection.execute(statement)
        ]


def _product_row(product: Product, seen_at: datetime) -> Row:
    """Translate a domain product into a ``products`` row."""
    packaging = product.packaging
    return {
        "supermarket_code": str(product.supermarket),
        "external_id": product.external_id,
        "name": product.name,
        "slug": product.slug,
        "packaging": packaging.packaging,
        "unit_size": packaging.unit_size,
        "size_format": packaging.size_format,
        "is_pack": packaging.is_pack,
        "pack_size": packaging.pack_size,
        "total_units": packaging.total_units,
        "unit_name": packaging.unit_name,
        "selling_method": packaging.selling_method.value,
        "is_approximate_size": packaging.is_approximate_size,
        "thumbnail_url": product.thumbnail_url,
        "share_url": product.share_url,
        "is_available": product.is_available,
        "is_new_arrival": product.is_new_arrival,
        "is_active": True,
        "first_seen_at": seen_at,
        "last_seen_at": seen_at,
        "delisted_at": None,
        "replaced_by_product_id": None,
    }


def _price_change_kind_columns(kinds: PriceChangeCounts | None) -> Row:
    """Spread the price change breakdown over its ``scrape_runs`` columns (NULL if unknown)."""
    return {
        "price_increases": kinds.increased if kinds else None,
        "price_decreases": kinds.decreased if kinds else None,
        "promotions_started": kinds.promotions_started if kinds else None,
        "promotions_ended": kinds.promotions_ended if kinds else None,
    }


def _price_change_kinds(
    increased: int | None,
    decreased: int | None,
    promotions_started: int | None,
    promotions_ended: int | None,
) -> PriceChangeCounts | None:
    """Rebuild the breakdown of a run, or ``None`` for runs recorded before it existed."""
    if increased is None or decreased is None or promotions_started is None:
        return None
    if promotions_ended is None:
        return None
    return PriceChangeCounts(
        increased=increased,
        decreased=decreased,
        promotions_started=promotions_started,
        promotions_ended=promotions_ended,
    )


def _gtin_following_ean(table: Table) -> ColumnElement[str | None]:
    """Keep ``gtin`` in step with ``ean``: replace it whenever a new EAN is written, else keep it.

    The ``ean`` column is coalesced, so a missing EAN never erases a known one. The
    GTIN must follow the same rule, including becoming NULL when the new EAN is invalid.
    """
    return case(
        (bindparam("b_ean", type_=table.c.ean.type).is_not(None), bindparam("b_gtin")),
        else_=table.c.gtin,
    )


def _external_id_map(
    connection: Connection,
    table: Table,
    supermarket: SupermarketCode,
    *,
    only_active: bool = False,
) -> dict[str, int]:
    """Map external ids to surrogate ids for every row of a supermarket in a catalog table."""
    statement = select(table.c.external_id, table.c.id).where(
        table.c.supermarket_code == str(supermarket)
    )
    if only_active:
        statement = statement.where(table.c.is_active)
    return {row.external_id: row.id for row in connection.execute(statement)}

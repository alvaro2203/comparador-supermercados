from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from supermarket_scraper.domain.entities import Product, ProductDetails, Supermarket
from supermarket_scraper.infrastructure.persistence import tables as schema
from supermarket_scraper.infrastructure.scrapers.mercadona.scraper import MERCADONA_CODE

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy import Engine

    from supermarket_scraper.domain.repositories import UnitOfWork
    from tests.conftest import FixedClock

OIL_EAN = "8480000108142"
MERCADONA = Supermarket(
    code=MERCADONA_CODE, name="Mercadona", website="https://tienda.mercadona.es"
)


def catalog_day(
    uow_factory: Callable[[], UnitOfWork], clock: FixedClock, eans: dict[str, str]
) -> int:
    """Store one day's catalog (the given products with their EANs) and return links made."""
    with uow_factory() as uow:
        uow.supermarkets.upsert(MERCADONA)
        uow.products.sync(
            MERCADONA_CODE,
            [Product(supermarket=MERCADONA_CODE, external_id=key, name="Aceite") for key in eans],
            clock.now(),
        )
        uow.products.delist_missing(MERCADONA_CODE, set(eans), clock.now())
        uow.products.apply_details(
            [
                ProductDetails(supermarket=MERCADONA_CODE, external_id=key, ean=ean)
                for key, ean in eans.items()
            ],
            clock.now(),
        )
        linked = uow.products.link_successors(MERCADONA_CODE)
        uow.commit()
    clock.advance()
    return linked


def successors(engine: Engine) -> dict[str, str | None]:
    old = schema.products
    new = schema.products.alias("new")
    statement = select(old.c.external_id, new.c.external_id.label("successor")).outerjoin(
        new, new.c.id == old.c.replaced_by_product_id
    )
    with engine.connect() as connection:
        return {row.external_id: row.successor for row in connection.execute(statement)}


def test_relisted_article_under_a_new_id_is_linked_to_it(
    uow_factory: Callable[[], UnitOfWork], clock: FixedClock, engine: Engine
) -> None:
    assert catalog_day(uow_factory, clock, {"old": OIL_EAN}) == 0
    assert catalog_day(uow_factory, clock, {"new": OIL_EAN}) == 1

    assert successors(engine) == {"old": "new", "new": None}


def test_linking_is_idempotent_and_waits_for_the_successor_gtin(
    uow_factory: Callable[[], UnitOfWork], clock: FixedClock, engine: Engine
) -> None:
    catalog_day(uow_factory, clock, {"old": OIL_EAN})
    with uow_factory() as uow:
        uow.products.sync(
            MERCADONA_CODE,
            [Product(supermarket=MERCADONA_CODE, external_id="new", name="Aceite")],
            clock.now(),
        )
        uow.products.delist_missing(MERCADONA_CODE, {"new"}, clock.now())
        assert uow.products.link_successors(MERCADONA_CODE) == 0
        uow.products.apply_details(
            [ProductDetails(supermarket=MERCADONA_CODE, external_id="new", ean=OIL_EAN)],
            clock.now() + timedelta(days=3),
        )
        assert uow.products.link_successors(MERCADONA_CODE) == 1
        assert uow.products.link_successors(MERCADONA_CODE) == 0
        uow.commit()

    assert successors(engine)["old"] == "new"


def test_sibling_sharing_the_gtin_is_not_a_successor(
    uow_factory: Callable[[], UnitOfWork], clock: FixedClock, engine: Engine
) -> None:
    catalog_day(uow_factory, clock, {"9638.1": OIL_EAN, "9638.2": OIL_EAN})
    catalog_day(uow_factory, clock, {"9638.2": OIL_EAN})

    assert successors(engine) == {"9638.1": None, "9638.2": None}


def test_ambiguous_successors_are_not_linked(
    uow_factory: Callable[[], UnitOfWork], clock: FixedClock, engine: Engine
) -> None:
    catalog_day(uow_factory, clock, {"old": OIL_EAN})
    assert catalog_day(uow_factory, clock, {"new.1": OIL_EAN, "new.2": OIL_EAN}) == 0

    assert successors(engine)["old"] is None


def test_reactivated_product_loses_its_successor_link(
    uow_factory: Callable[[], UnitOfWork], clock: FixedClock, engine: Engine
) -> None:
    catalog_day(uow_factory, clock, {"old": OIL_EAN})
    catalog_day(uow_factory, clock, {"new": OIL_EAN})
    assert catalog_day(uow_factory, clock, {"old": OIL_EAN, "new": OIL_EAN}) == 0

    assert successors(engine) == {"old": None, "new": None}


def test_different_gtin_is_never_a_successor(
    uow_factory: Callable[[], UnitOfWork], clock: FixedClock, engine: Engine
) -> None:
    catalog_day(uow_factory, clock, {"papilla-725g": "8480000108142"})
    catalog_day(uow_factory, clock, {"papilla-480g": "8445292870009"})

    assert successors(engine)["papilla-725g"] is None


def test_gtin_is_not_unique_within_a_chain(
    uow_factory: Callable[[], UnitOfWork], clock: FixedClock, engine: Engine
) -> None:
    catalog_day(uow_factory, clock, {"9638.1": OIL_EAN, "9638.2": OIL_EAN})

    products = schema.products
    with engine.connect() as connection:
        rows = connection.execute(
            select(products.c.external_id).where(products.c.gtin == "0" + OIL_EAN)
        ).all()
    assert sorted(row.external_id for row in rows) == ["9638.1", "9638.2"]

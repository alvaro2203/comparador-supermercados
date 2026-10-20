from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self, override

from supermarket_scraper.domain.repositories import UnitOfWork
from supermarket_scraper.infrastructure.persistence.repositories import (
    SqlCategoryRepository,
    SqlPriceRepository,
    SqlProductRepository,
    SqlScrapeRunRepository,
    SqlSupermarketRepository,
)

if TYPE_CHECKING:
    from types import TracebackType

    from sqlalchemy import Connection, Engine, RootTransaction


class SqlAlchemyUnitOfWork(UnitOfWork):
    """Unit of work backed by a single SQLAlchemy connection and transaction."""

    def __init__(self, engine: Engine) -> None:
        """Remember the engine; the connection is opened on ``__enter__``."""
        self._engine = engine
        self._connection: Connection | None = None
        self._transaction: RootTransaction | None = None

    @override
    def __enter__(self) -> Self:
        """Open a connection, begin a transaction and bind every repository to it."""
        connection = self._engine.connect()
        self._connection = connection
        self._transaction = connection.begin()
        self.supermarkets = SqlSupermarketRepository(connection, datetime.now(UTC))
        self.categories = SqlCategoryRepository(connection)
        self.products = SqlProductRepository(connection)
        self.prices = SqlPriceRepository(connection)
        self.runs = SqlScrapeRunRepository(connection)
        return self

    @override
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Roll back anything left uncommitted and close the connection."""
        try:
            self.rollback()
        finally:
            if self._connection is not None:
                self._connection.close()
            self._connection = None
            self._transaction = None

    @override
    def commit(self) -> None:
        """Commit the active transaction."""
        if self._transaction is not None and self._transaction.is_active:
            self._transaction.commit()

    @override
    def rollback(self) -> None:
        """Roll back the active transaction if it is still open."""
        if self._transaction is not None and self._transaction.is_active:
            self._transaction.rollback()

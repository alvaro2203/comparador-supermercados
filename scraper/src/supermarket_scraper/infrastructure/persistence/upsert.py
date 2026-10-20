from __future__ import annotations

from abc import ABC, abstractmethod
from itertools import batched
from typing import TYPE_CHECKING, Final, override

from sqlalchemy import and_, insert, select, update
from sqlalchemy.dialects import mysql, postgresql, sqlite

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy import Connection, Table

type Row = Mapping[str, object]

CHUNK_SIZE: Final = 400


class UpsertStrategy(ABC):
    """Dialect-specific way of performing an ``INSERT ... ON CONFLICT UPDATE``."""

    @abstractmethod
    def execute(
        self,
        connection: Connection,
        table: Table,
        rows: Sequence[Row],
        conflict_columns: Sequence[str],
        update_columns: Sequence[str],
    ) -> None:
        """Insert ``rows`` or update ``update_columns`` when ``conflict_columns`` collide."""


class SqliteUpsert(UpsertStrategy):
    """Native ``ON CONFLICT`` clause for SQLite."""

    @override
    def execute(
        self,
        connection: Connection,
        table: Table,
        rows: Sequence[Row],
        conflict_columns: Sequence[str],
        update_columns: Sequence[str],
    ) -> None:
        """Run a single multi-row upsert statement."""
        statement = sqlite.insert(table).values(list(rows))
        if update_columns:
            statement = statement.on_conflict_do_update(
                index_elements=list(conflict_columns),
                set_={column: statement.excluded[column] for column in update_columns},
            )
        else:
            statement = statement.on_conflict_do_nothing(index_elements=list(conflict_columns))
        connection.execute(statement)


class PostgresUpsert(UpsertStrategy):
    """Native ``ON CONFLICT`` clause for PostgreSQL."""

    @override
    def execute(
        self,
        connection: Connection,
        table: Table,
        rows: Sequence[Row],
        conflict_columns: Sequence[str],
        update_columns: Sequence[str],
    ) -> None:
        """Run a single multi-row upsert statement."""
        statement = postgresql.insert(table).values(list(rows))
        if update_columns:
            statement = statement.on_conflict_do_update(
                index_elements=list(conflict_columns),
                set_={column: statement.excluded[column] for column in update_columns},
            )
        else:
            statement = statement.on_conflict_do_nothing(index_elements=list(conflict_columns))
        connection.execute(statement)


class MysqlUpsert(UpsertStrategy):
    """Native ``ON DUPLICATE KEY UPDATE`` clause for MySQL and MariaDB."""

    @override
    def execute(
        self,
        connection: Connection,
        table: Table,
        rows: Sequence[Row],
        conflict_columns: Sequence[str],
        update_columns: Sequence[str],
    ) -> None:
        """Run a single multi-row upsert statement."""
        statement = mysql.insert(table).values(list(rows))
        columns = list(update_columns) or [conflict_columns[0]]
        connection.execute(
            statement.on_duplicate_key_update(
                {column: statement.inserted[column] for column in columns}
            )
        )


class PortableUpsert(UpsertStrategy):
    """Engine-agnostic fallback based on plain ``SELECT``, ``UPDATE`` and ``INSERT``."""

    @override
    def execute(
        self,
        connection: Connection,
        table: Table,
        rows: Sequence[Row],
        conflict_columns: Sequence[str],
        update_columns: Sequence[str],
    ) -> None:
        """Update existing rows one by one and bulk insert the missing ones."""
        missing: list[Row] = []
        for row in rows:
            match = and_(*(table.c[column] == row[column] for column in conflict_columns))
            exists = connection.execute(select(1).select_from(table).where(match)).first()
            if exists is None:
                missing.append(row)
            elif update_columns:
                values = {column: row[column] for column in update_columns}
                connection.execute(update(table).where(match).values(values))
        if missing:
            connection.execute(insert(table), list(missing))


_STRATEGIES: Final[dict[str, UpsertStrategy]] = {
    "sqlite": SqliteUpsert(),
    "postgresql": PostgresUpsert(),
    "mysql": MysqlUpsert(),
    "mariadb": MysqlUpsert(),
}
_FALLBACK: Final = PortableUpsert()


def register_upsert_strategy(dialect_name: str, strategy: UpsertStrategy) -> None:
    """Plug a native upsert implementation for an additional database engine."""
    _STRATEGIES[dialect_name] = strategy


def upsert(
    connection: Connection,
    table: Table,
    rows: Sequence[Row],
    *,
    conflict_columns: Sequence[str],
    update_columns: Sequence[str],
) -> None:
    """Upsert rows in chunks using the best strategy known for the connection's dialect."""
    if not rows:
        return
    strategy = _STRATEGIES.get(connection.dialect.name, _FALLBACK)
    for chunk in batched(rows, CHUNK_SIZE):
        strategy.execute(connection, table, chunk, conflict_columns, update_columns)

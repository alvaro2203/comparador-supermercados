from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, override

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from supermarket_scraper.application.ports import Clock
from supermarket_scraper.infrastructure.http.client import AsyncJsonClient, HttpClientConfig
from supermarket_scraper.infrastructure.persistence.database import build_engine, upgrade_schema
from supermarket_scraper.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from supermarket_scraper.infrastructure.scrapers.mercadona.scraper import (
    MercadonaScraper,
    MercadonaSettings,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from sqlalchemy import Engine

    from supermarket_scraper.domain.repositories import UnitOfWork

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://tienda.test"
TEST_DATABASE_URL_ENV = "SCRAPER_TEST_DATABASE_URL"
LOCAL_TEST_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class FixedClock(Clock):
    """Deterministic clock that can be moved forward by whole days."""

    def __init__(self, at: datetime) -> None:
        self.at = at

    @override
    def now(self) -> datetime:
        return self.at

    @override
    def today(self) -> date:
        return self.at.date()

    def advance(self, days: int = 1) -> None:
        self.at += timedelta(days=days)


@dataclass
class FakeApi:
    """In-memory HTTP backend keyed by request path."""

    responses: dict[str, object | int] = field(default_factory=dict)
    calls: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        response = self.responses.get(request.url.path, 404)
        if isinstance(response, int):
            return httpx.Response(response, json={"detail": "error"})
        return httpx.Response(200, json=response)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


async def no_sleep(_seconds: float) -> None:
    return None


def load_fixture(name: str) -> object:
    data: object = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def mercadona_responses() -> dict[str, object | int]:
    return {
        "/api/categories/": load_fixture("mercadona/categories.json"),
        "/api/categories/112/": load_fixture("mercadona/category_112.json"),
        "/api/categories/115/": load_fixture("mercadona/category_115.json"),
        "/api/products/4241/": load_fixture("mercadona/product_4241.json"),
    }


@pytest.fixture
def fake_api() -> FakeApi:
    return FakeApi(responses=copy.deepcopy(mercadona_responses()))


@pytest.fixture
def http_config() -> HttpClientConfig:
    return HttpClientConfig(max_retries=2, min_interval_seconds=0, backoff_base_seconds=0.01)


@pytest.fixture
def make_scraper(
    fake_api: FakeApi, http_config: HttpClientConfig
) -> Callable[[], MercadonaScraper]:
    def factory() -> MercadonaScraper:
        client = AsyncJsonClient(
            BASE_URL, http_config, transport=fake_api.transport(), sleep=no_sleep
        )
        return MercadonaScraper(client, MercadonaSettings(base_url=BASE_URL, warehouse="vlc1"))

    return factory


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 10, 1, 6, 0, tzinfo=UTC))


def local_test_database_url(environ: Mapping[str, str]) -> str | None:
    """Return the database URL tests should use instead of SQLite, or ``None`` for SQLite.

    The database is emptied before every test, so only a throwaway database on this
    machine is accepted; anything else (Neon, a staging host) raises.
    """
    raw = environ.get(TEST_DATABASE_URL_ENV)
    if not raw:
        return None
    host = make_url(raw).host
    if host not in LOCAL_TEST_HOSTS:
        msg = (
            f"{TEST_DATABASE_URL_ENV} points at {host!r}. Tests wipe the database they use, "
            f"so it must be on one of: {', '.join(sorted(LOCAL_TEST_HOSTS))}"
        )
        raise RuntimeError(msg)
    return raw


def migrated_engine(url: str) -> Iterator[Engine]:
    """Yield an engine on ``url`` with an empty database at the latest migration."""
    db_engine = build_engine(url)
    if db_engine.dialect.name == "postgresql":
        with db_engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    upgrade_schema(db_engine)
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def sqlite_engine(tmp_path: Path) -> Iterator[Engine]:
    """A temporary SQLite database, for tests of behaviour that only SQLite has."""
    yield from migrated_engine(f"sqlite:///{tmp_path / 'test.db'}")


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    """The database under test: SQLite, or the local PostgreSQL named by the environment."""
    url = local_test_database_url(os.environ) or f"sqlite:///{tmp_path / 'test.db'}"
    yield from migrated_engine(url)


@pytest.fixture
def uow_factory(engine: Engine) -> Callable[[], UnitOfWork]:
    return partial(SqlAlchemyUnitOfWork, engine)

from __future__ import annotations

import pytest

from tests.conftest import TEST_DATABASE_URL_ENV, local_test_database_url


def test_without_the_variable_tests_use_sqlite() -> None:
    assert local_test_database_url({}) is None
    assert local_test_database_url({TEST_DATABASE_URL_ENV: ""}) is None


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_a_database_on_this_machine_is_accepted(host: str) -> None:
    url = f"postgresql+psycopg://postgres:test@{host}:5432/scraper"

    assert local_test_database_url({TEST_DATABASE_URL_ENV: url}) == url


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://owner:secret@ep-quiet-rain-123456.eu-central-1.aws.neon.tech/neondb",
        "postgresql+psycopg://owner:secret@db.example.com:5432/scraper",
        "postgresql+psycopg://owner:secret@/scraper",
    ],
)
def test_a_database_elsewhere_is_refused_without_leaking_its_password(url: str) -> None:
    with pytest.raises(RuntimeError, match="wipe the database") as refusal:
        local_test_database_url({TEST_DATABASE_URL_ENV: url})

    assert "secret" not in str(refusal.value)

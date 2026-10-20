from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from supermarket_scraper.bootstrap import Container, build_container
from supermarket_scraper.infrastructure.persistence.database import build_engine
from supermarket_scraper.infrastructure.persistence.tables import supermarkets
from supermarket_scraper.presentation import cli
from supermarket_scraper.presentation.cli import ExitCode, main
from tests.conftest import BASE_URL

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from supermarket_scraper.config import Settings
    from tests.conftest import FakeApi


def use_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SCRAPER_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")


def route_scrapers_to(monkeypatch: pytest.MonkeyPatch, fake_api: FakeApi) -> None:
    monkeypatch.setenv("MERCADONA_BASE_URL", BASE_URL)
    monkeypatch.setenv("SCRAPER_HTTP_MIN_INTERVAL_SECONDS", "0")

    def build_offline(settings: Settings) -> Container:
        container = build_container(settings)
        context = dataclasses.replace(container.scraper_context, transport=fake_api.transport())
        return dataclasses.replace(container, scraper_context=context)

    monkeypatch.setattr(cli, "build_container", build_offline)


def test_init_db_list_and_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_database(monkeypatch, tmp_path)

    assert main(["init-db"]) == ExitCode.OK
    assert (tmp_path / "cli.db").exists()

    assert main(["list"]) == ExitCode.OK
    assert "mercadona" in capsys.readouterr().out

    assert main(["history", "mercadona"]) == ExitCode.OK
    assert "No runs recorded" in capsys.readouterr().out


def test_unknown_supermarket_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_database(monkeypatch, tmp_path)
    assert main(["run", "lidl"]) == ExitCode.USAGE


def test_malformed_supermarket_code_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_database(monkeypatch, tmp_path)
    assert main(["history", "Not A Code"]) == ExitCode.USAGE


def test_invalid_configuration_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_database(monkeypatch, tmp_path)
    monkeypatch.setenv("SCRAPER_TIMEZONE", "Mars/Olympus_Mons")

    assert main(["list"]) == ExitCode.USAGE
    assert "Unknown IANA timezone" in capsys.readouterr().err


def test_run_scrapes_persists_and_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_api: FakeApi,
) -> None:
    use_database(monkeypatch, tmp_path)
    route_scrapers_to(monkeypatch, fake_api)

    assert main(["run", "mercadona", "--no-details"]) == ExitCode.OK
    output = capsys.readouterr().out
    assert "[mercadona] SUCCEEDED" in output
    assert "new products     4" in output

    assert main(["run", "mercadona", "--details"]) == ExitCode.PARTIAL
    assert "details enriched 1" in capsys.readouterr().out

    assert main(["history", "mercadona", "--limit", "5"]) == ExitCode.OK
    history = capsys.readouterr().out
    assert history.count("succeeded") == 1
    assert history.count("partial") == 1


def test_dry_run_reports_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_api: FakeApi,
) -> None:
    use_database(monkeypatch, tmp_path)
    route_scrapers_to(monkeypatch, fake_api)

    assert main(["run", "mercadona", "--dry-run"]) == ExitCode.OK
    assert "(dry run)" in capsys.readouterr().out
    assert not (tmp_path / "cli.db").exists()


def test_unreachable_source_exits_with_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_api: FakeApi,
) -> None:
    use_database(monkeypatch, tmp_path)
    route_scrapers_to(monkeypatch, fake_api)
    monkeypatch.setenv("SCRAPER_HTTP_MAX_RETRIES", "0")
    fake_api.responses["/api/categories/"] = 503

    assert main(["run", "mercadona"]) == ExitCode.FAILED
    assert "FAILED" in capsys.readouterr().out


def test_migration_failure_exits_with_failure_before_scraping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_api: FakeApi,
) -> None:
    use_database(monkeypatch, tmp_path)
    route_scrapers_to(monkeypatch, fake_api)
    partial = build_engine(f"sqlite:///{tmp_path / 'cli.db'}")
    supermarkets.create(partial)
    partial.dispose()

    assert main(["init-db"]) == ExitCode.FAILED
    assert main(["run", "mercadona"]) == ExitCode.FAILED

    errors = capsys.readouterr().err
    assert errors.count("Database migration failed") == 2
    assert "Traceback" not in errors
    assert fake_api.calls == []

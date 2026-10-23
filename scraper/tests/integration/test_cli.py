from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import UTC, date, datetime
from functools import partial
from importlib.metadata import version
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy import select

from supermarket_scraper.application.sync_catalog import SyncCatalogUseCase, SyncOptions
from supermarket_scraper.bootstrap import Container, build_container
from supermarket_scraper.domain.entities import RunStats, ScrapeRun, Supermarket
from supermarket_scraper.domain.value_objects import SupermarketCode
from supermarket_scraper.infrastructure.persistence.database import build_engine, upgrade_schema
from supermarket_scraper.infrastructure.persistence.tables import price_snapshots, supermarkets
from supermarket_scraper.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from supermarket_scraper.presentation import cli
from supermarket_scraper.presentation.cli import ExitCode, main
from tests.conftest import BASE_URL, FixedClock

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from supermarket_scraper.config import Settings
    from supermarket_scraper.infrastructure.scrapers.mercadona.scraper import MercadonaScraper
    from tests.conftest import FakeApi


async def sync_once(
    use_case: SyncCatalogUseCase, make_scraper: Callable[[], MercadonaScraper]
) -> None:
    async with make_scraper() as source:
        await use_case.execute(source)


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


def test_version_prints_the_installed_package_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"supermarket-scraper {version('supermarket-scraper')}\n"


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


def test_enrich_backfills_details_after_a_run_without_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_api: FakeApi,
) -> None:
    use_database(monkeypatch, tmp_path)
    route_scrapers_to(monkeypatch, fake_api)
    assert main(["run", "mercadona", "--no-details"]) == ExitCode.OK
    capsys.readouterr()

    assert main(["enrich", "mercadona", "--limit", "1"]) == ExitCode.OK
    first = capsys.readouterr().out
    assert "[mercadona] SUCCEEDED details enrichment" in first
    assert "enriched         1" in first

    assert main(["enrich"]) == ExitCode.FAILED
    rest = capsys.readouterr().out
    assert "pending          3" in rest
    assert "failures         3" in rest
    assert "stopped early" in rest
    assert "product 4749: GET /api/products/4749/ returned 404" in rest

    assert main(["history", "mercadona"]) == ExitCode.OK
    assert capsys.readouterr().out.count("succeeded") == 1


def test_enrich_rejects_unknown_supermarkets_and_negative_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_api: FakeApi,
) -> None:
    use_database(monkeypatch, tmp_path)
    route_scrapers_to(monkeypatch, fake_api)

    assert main(["enrich", "lidl"]) == ExitCode.USAGE
    assert main(["enrich", "--limit", "-1"]) == ExitCode.USAGE
    assert "--limit must be zero or positive" in capsys.readouterr().err
    assert fake_api.calls == []


def test_enrich_report_lists_a_bounded_number_of_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_api: FakeApi,
) -> None:
    use_database(monkeypatch, tmp_path)
    route_scrapers_to(monkeypatch, fake_api)
    monkeypatch.setattr(cli, "MAX_LISTED_FAILURES", 2)
    assert main(["run", "mercadona", "--no-details"]) == ExitCode.OK
    capsys.readouterr()

    assert main(["enrich"]) == ExitCode.PARTIAL

    output = capsys.readouterr().out
    assert output.count("returned 404") == 2
    assert "and 1 more (see the log for all of them)" in output


def test_run_report_and_history_break_price_changes_down(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_api: FakeApi,
) -> None:
    use_database(monkeypatch, tmp_path)
    route_scrapers_to(monkeypatch, fake_api)
    engine = build_engine(f"sqlite:///{tmp_path / 'cli.db'}")
    upgrade_schema(engine)
    code = SupermarketCode("mercadona")
    legacy_at = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)
    with SqlAlchemyUnitOfWork(engine) as uow:
        uow.supermarkets.upsert(
            Supermarket(code=code, name="Mercadona", website="https://tienda.mercadona.es")
        )
        uow.runs.save(ScrapeRun.start(code, legacy_at).complete(legacy_at, RunStats()))
        uow.commit()
    engine.dispose()

    assert main(["run", "mercadona", "--no-details"]) == ExitCode.OK
    report = capsys.readouterr().out
    assert "    increases      0" in report
    assert "    promo ended    0" in report

    assert main(["history", "mercadona"]) == ExitCode.OK
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split()[-5:] == ["up", "down", "promo+", "promo-", "errors"]
    assert lines[2].split()[-5:] == ["0", "0", "0", "0", "0"]
    assert lines[3].split()[-5:] == ["-", "-", "-", "-", "0"]


def test_report_prints_the_day_against_the_previous_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_scraper: Callable[[], MercadonaScraper],
    fake_api: FakeApi,
) -> None:
    use_database(monkeypatch, tmp_path)
    clock = FixedClock(datetime(2026, 10, 1, 6, 0, tzinfo=UTC))
    engine = build_engine(f"sqlite:///{tmp_path / 'cli.db'}")
    upgrade_schema(engine)
    use_case = SyncCatalogUseCase(
        partial(SqlAlchemyUnitOfWork, engine), clock, SyncOptions(fetch_details=False)
    )
    asyncio.run(sync_once(use_case, make_scraper))
    page = cast(
        "dict[str, list[dict[str, list[dict[str, object]]]]]",
        fake_api.responses["/api/categories/112/"],
    )
    garrafa = page["categories"][0]["products"][1]
    cast("dict[str, object]", garrafa["price_instructions"]).update(unit_price="39.95")
    clock.advance()
    asyncio.run(sync_once(use_case, make_scraper))
    engine.dispose()

    assert main(["report", "mercadona", "--top", "5"]) == ExitCode.OK

    output = capsys.readouterr().out
    assert "[mercadona] 2026-10-02 vs 2026-10-01" in output
    assert "  price changes    1 (1 up, 0 down, 0 promo started, 0 promo ended)" in output
    assert "    +8.12 %  36.95 -> 39.95  Aceite de oliva 0,4º Hacendado (Garrafa 5 l)" in output
    assert "  new products     0" in output

    assert main(["report", "--day", "2026-10-01"]) == ExitCode.OK
    assert "2026-10-01 (no previous day to compare)" in capsys.readouterr().out


def test_report_usage_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_database(monkeypatch, tmp_path)

    assert main(["report", "mercadona"]) == ExitCode.USAGE
    assert "No prices stored for mercadona" in capsys.readouterr().err
    assert main(["report", "--day", "23/10/2026"]) == ExitCode.USAGE
    assert "--day must be a date like 2026-10-23" in capsys.readouterr().err
    assert main(["report", "lidl"]) == ExitCode.USAGE
    assert main(["report", "--top", "0"]) == ExitCode.USAGE


def raw_day_dirs(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "raw" / "mercadona").iterdir())


def test_dump_saves_the_raw_catalog_without_touching_the_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_api: FakeApi,
) -> None:
    use_database(monkeypatch, tmp_path)
    route_scrapers_to(monkeypatch, fake_api)
    monkeypatch.setenv("SCRAPER_RAW_DIR", str(tmp_path / "raw"))

    assert main(["dump"]) == ExitCode.OK

    [archive] = raw_day_dirs(tmp_path)
    assert (archive / "manifest.json").is_file()
    assert sorted(path.relative_to(archive).as_posix() for path in archive.rglob("*.json")) == [
        "api/categories.json",
        "api/categories/112.json",
        "api/categories/115.json",
        "manifest.json",
    ]
    assert not (tmp_path / "cli.db").exists()
    assert f"raw JSON saved to {archive}" in capsys.readouterr().out


def test_run_from_raw_loads_a_dump_offline_under_its_capture_day(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_api: FakeApi,
) -> None:
    use_database(monkeypatch, tmp_path)
    route_scrapers_to(monkeypatch, fake_api)
    monkeypatch.setenv("SCRAPER_RAW_DIR", str(tmp_path / "raw"))
    assert main(["dump", "mercadona"]) == ExitCode.OK
    [archive] = raw_day_dirs(tmp_path)
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    manifest["captured_at"] = "2026-09-30T22:30:00+00:00"
    (archive / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    requests_before = len(fake_api.calls)
    capsys.readouterr()

    assert main(["run", "--from-raw", str(archive)]) == ExitCode.OK

    assert len(fake_api.calls) == requests_before
    assert "new products     4" in capsys.readouterr().out
    engine = build_engine(f"sqlite:///{tmp_path / 'cli.db'}")
    with engine.connect() as connection:
        days = connection.execute(select(price_snapshots.c.captured_on).distinct()).scalars().all()
    engine.dispose()
    assert days == [date(2026, 10, 1)]


def test_run_save_raw_also_archives_product_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_api: FakeApi,
) -> None:
    use_database(monkeypatch, tmp_path)
    route_scrapers_to(monkeypatch, fake_api)
    monkeypatch.setenv("SCRAPER_RAW_DIR", str(tmp_path / "raw"))

    assert main(["run", "--save-raw"]) == ExitCode.PARTIAL

    [archive] = raw_day_dirs(tmp_path)
    assert (archive / "api" / "products" / "4241.json").is_file()
    assert (tmp_path / "cli.db").exists()


def test_from_raw_usage_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_api: FakeApi,
) -> None:
    use_database(monkeypatch, tmp_path)
    route_scrapers_to(monkeypatch, fake_api)

    assert main(["run", "--from-raw", str(tmp_path / "nowhere")]) == ExitCode.USAGE
    assert "manifest.json is missing" in capsys.readouterr().err
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "manifest.json").write_text(
        '{"supermarket": "mercadona", "captured_at": "2026-10-01T06:00:00+00:00"}',
        encoding="utf-8",
    )
    assert main(["run", "dia", "--from-raw", str(archive)]) == ExitCode.USAGE
    assert "--from-raw replays one archive" in capsys.readouterr().err
    assert main(["run", "--from-raw", str(archive), "--save-raw"]) == ExitCode.USAGE
    assert fake_api.calls == []

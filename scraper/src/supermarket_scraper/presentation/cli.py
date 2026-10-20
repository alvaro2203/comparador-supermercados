from __future__ import annotations

import argparse
import asyncio
import io
import logging
import sys
from enum import IntEnum
from typing import TYPE_CHECKING, Final

from pydantic import ValidationError

from supermarket_scraper.bootstrap import Container, build_container
from supermarket_scraper.config import Settings
from supermarket_scraper.domain.entities import RunStatus
from supermarket_scraper.domain.exceptions import DomainError, UnknownSupermarketError
from supermarket_scraper.domain.value_objects import SupermarketCode
from supermarket_scraper.infrastructure.logging_setup import configure_logging
from supermarket_scraper.infrastructure.persistence.database import upgrade_schema

if TYPE_CHECKING:
    from collections.abc import Sequence

    from supermarket_scraper.application.sync_catalog import SyncReport

logger: Final = logging.getLogger("supermarket_scraper.cli")


class ExitCode(IntEnum):
    """Process exit codes understood by schedulers and monitoring."""

    OK = 0
    PARTIAL = 1
    FAILED = 2
    USAGE = 64


def build_parser() -> argparse.ArgumentParser:
    """Define the command-line interface."""
    parser = argparse.ArgumentParser(
        prog="supermarket-scraper",
        description="Download supermarket catalogs and keep a daily price history.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init-db", help="Create or upgrade the database schema (idempotent).")
    commands.add_parser("list", help="List the supermarkets that can be scraped.")

    run = commands.add_parser("run", help="Scrape one or more supermarkets and store the results.")
    run.add_argument(
        "supermarkets",
        nargs="*",
        metavar="SUPERMARKET",
        help="Supermarket codes to scrape (default: all registered).",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape and validate without writing to the database.",
    )
    details = run.add_mutually_exclusive_group()
    details.add_argument(
        "--details",
        dest="fetch_details",
        action="store_true",
        default=None,
        help="Force fetching product details (EAN, brand, ingredients).",
    )
    details.add_argument(
        "--no-details",
        dest="fetch_details",
        action="store_false",
        help="Skip fetching product details.",
    )

    history = commands.add_parser("history", help="Show the latest runs of a supermarket.")
    history.add_argument("supermarket", metavar="SUPERMARKET")
    history.add_argument("--limit", type=int, default=10)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of the ``supermarket-scraper`` console script."""
    _force_utf8_streams()
    args = build_parser().parse_args(argv)
    try:
        settings = Settings()
    except ValidationError as exc:
        sys.stderr.write(f"Invalid configuration:\n{exc}\n")
        return ExitCode.USAGE
    configure_logging(settings.log_level, settings.log_format)
    container = build_container(settings)
    try:
        command: str = args.command
        if command == "init-db":
            upgrade_schema(container.engine)
            sys.stdout.write("Database schema is up to date.\n")
            return ExitCode.OK
        if command == "list":
            sys.stdout.write("\n".join(container.registry.codes()) + "\n")
            return ExitCode.OK
        if command == "history":
            return _history(container, args.supermarket, args.limit)
        supermarkets: list[str] = args.supermarkets
        dry_run: bool = args.dry_run
        fetch_details: bool | None = args.fetch_details
        return asyncio.run(_run(container, supermarkets, dry_run=dry_run, details=fetch_details))
    except DomainError as exc:
        sys.stderr.write(f"{exc}\n")
        return ExitCode.USAGE
    finally:
        container.engine.dispose()


async def _run(
    container: Container, codes: Sequence[str], *, dry_run: bool, details: bool | None
) -> int:
    """Scrape the requested supermarkets one after another and aggregate the exit code."""
    selected = list(codes) or list(container.registry.codes())
    for code in selected:
        if code not in container.registry.codes():
            msg = (
                f"Unknown supermarket {code!r}. Available: {', '.join(container.registry.codes())}"
            )
            raise UnknownSupermarketError(msg)
    if not dry_run:
        upgrade_schema(container.engine)
    use_case = container.sync_use_case(dry_run=dry_run, fetch_details=details)
    reports: list[SyncReport] = []
    for code in selected:
        async with container.registry.create(code, container.scraper_context) as source:
            reports.append(await use_case.execute(source))
    for report in reports:
        _print_report(report)
    statuses = {report.run.status for report in reports}
    if RunStatus.FAILED in statuses:
        return ExitCode.FAILED
    if RunStatus.PARTIAL in statuses:
        return ExitCode.PARTIAL
    return ExitCode.OK


def _history(container: Container, code: str, limit: int) -> int:
    """Print the latest runs stored for a supermarket."""
    upgrade_schema(container.engine)
    with container.uow_factory() as uow:
        runs = uow.runs.latest(SupermarketCode(code), limit)
    if not runs:
        sys.stdout.write(f"No runs recorded for {code}.\n")
        return ExitCode.OK
    header = (
        f"{'started (UTC)':<19} {'status':<10} {'products':>8} {'new':>6} "
        f"{'delisted':>8} {'changes':>8} {'errors':>6}"
    )
    lines = [header, "-" * len(header)]
    lines.extend(
        f"{run.started_at:%Y-%m-%d %H:%M:%S} {run.status.value:<10} {run.stats.products:>8} "
        f"{run.stats.new_products:>6} {run.stats.delisted_products:>8} "
        f"{run.stats.price_changes:>8} {run.stats.errors:>6}"
        for run in runs
    )
    sys.stdout.write("\n".join(lines) + "\n")
    return ExitCode.OK


def _print_report(report: SyncReport) -> None:
    """Write a one-block human summary of a run to stdout."""
    run = report.run
    stats = run.stats
    duration = (run.finished_at - run.started_at).total_seconds() if run.finished_at else 0.0
    mode = " (dry run)" if report.dry_run else ""
    lines = [
        f"[{run.supermarket}] {run.status.value.upper()}{mode} in {duration:.1f}s",
        f"  categories       {stats.categories}",
        f"  products         {stats.products}",
        f"  new products     {stats.new_products}",
        f"  delisted         {stats.delisted_products}",
        f"  price changes    {stats.price_changes}",
        f"  details enriched {stats.details_enriched}",
        f"  errors           {stats.errors}",
    ]
    if run.error_message:
        lines.append(f"  error            {run.error_message}")
    sys.stdout.write("\n".join(lines) + "\n")


def _force_utf8_streams() -> None:
    """Emit UTF-8 on every platform so accented names never break logging on Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper) and stream.encoding.lower() != "utf-8":
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import sys
from dataclasses import dataclass
from datetime import date
from enum import IntEnum
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Final

from pydantic import ValidationError
from sqlalchemy.engine import make_url

from supermarket_scraper.application.price_report import DEFAULT_TOP
from supermarket_scraper.bootstrap import Container, build_container
from supermarket_scraper.config import Settings
from supermarket_scraper.domain.entities import RunStatus
from supermarket_scraper.domain.exceptions import DomainError, UnknownSupermarketError
from supermarket_scraper.domain.value_objects import SupermarketCode
from supermarket_scraper.infrastructure.logging_setup import configure_logging
from supermarket_scraper.infrastructure.persistence.data_copy import copy_database
from supermarket_scraper.infrastructure.persistence.database import (
    SchemaMigrationError,
    build_engine,
    upgrade_schema,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from supermarket_scraper.application.enrich_details import EnrichReport
    from supermarket_scraper.application.price_report import PriceMove, PriceReport
    from supermarket_scraper.application.sync_catalog import SyncReport
    from supermarket_scraper.domain.entities import PriceChangeCounts, Product

logger: Final = logging.getLogger("supermarket_scraper.cli")

MAX_LISTED_FAILURES: Final = 20
DISTRIBUTION: Final = "supermarket-scraper"


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
    parser.add_argument("--version", action="version", version=f"%(prog)s {version(DISTRIBUTION)}")
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
    run.add_argument(
        "--save-raw",
        action="store_true",
        help="Also save every downloaded JSON under SCRAPER_RAW_DIR/<code>/<day>/.",
    )
    run.add_argument(
        "--from-raw",
        metavar="DIR",
        help="Process an archive saved by dump or --save-raw, offline, under its capture day.",
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

    enrich = commands.add_parser(
        "enrich",
        help="Fetch details (EAN, brand, ingredients) for every product still missing them.",
    )
    enrich.add_argument(
        "supermarkets",
        nargs="*",
        metavar="SUPERMARKET",
        help="Supermarket codes to enrich (default: all registered).",
    )
    enrich.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Enrich at most N products per supermarket (default: all pending).",
    )

    dump = commands.add_parser(
        "dump",
        help="Download catalogs as raw JSON to SCRAPER_RAW_DIR/<code>/<day>/ (no database).",
    )
    dump.add_argument(
        "supermarkets",
        nargs="*",
        metavar="SUPERMARKET",
        help="Supermarket codes to download (default: all registered).",
    )

    report = commands.add_parser(
        "report", help="Compare a day of stored prices with the previous one (no network)."
    )
    report.add_argument(
        "supermarkets",
        nargs="*",
        metavar="SUPERMARKET",
        help="Supermarket codes to report on (default: all registered).",
    )
    report.add_argument("--day", help="Day to report, YYYY-MM-DD (default: the latest).")
    report.add_argument(
        "--top", type=int, default=DEFAULT_TOP, help="Products listed per section (default: 10)."
    )

    copy_db = commands.add_parser(
        "copy-db",
        help="Copy the configured database into an empty one (for example SQLite to PostgreSQL).",
    )
    copy_db.add_argument(
        "--to",
        dest="target_url",
        required=True,
        metavar="URL",
        help="SQLAlchemy URL of the target.",
    )
    copy_db.add_argument(
        "--dry-run",
        action="store_true",
        help="Copy and verify, then roll back: the target keeps its empty tables only.",
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
        return _execute(container, args)
    except DomainError as exc:
        sys.stderr.write(f"{exc}\n")
        return ExitCode.USAGE
    except SchemaMigrationError as exc:
        logger.debug("Database migration failed", exc_info=exc)
        sys.stderr.write(f"Database migration failed: {exc}\n")
        return ExitCode.FAILED
    finally:
        container.engine.dispose()


@dataclass(frozen=True, slots=True)
class RunFlags:
    """Options of the ``run`` command."""

    dry_run: bool
    details: bool | None
    save_raw: bool
    from_raw: str | None


def _execute(container: Container, args: argparse.Namespace) -> int:
    """Dispatch the parsed command and return its exit code."""
    handlers: dict[str, Callable[[], int]] = {
        "init-db": lambda: _init_db(container),
        "list": lambda: _list(container),
        "report": lambda: _report(container, args.supermarkets, args.day, args.top),
        "history": lambda: _history(container, args.supermarket, args.limit),
        "copy-db": lambda: _copy_db(container, args.target_url, dry_run=args.dry_run),
        "enrich": lambda: asyncio.run(_enrich(container, args.supermarkets, args.limit)),
        "dump": lambda: asyncio.run(_dump(container, args.supermarkets)),
        "run": lambda: asyncio.run(
            _run(
                container,
                args.supermarkets,
                RunFlags(
                    dry_run=args.dry_run,
                    details=args.fetch_details,
                    save_raw=args.save_raw,
                    from_raw=args.from_raw,
                ),
            )
        ),
    }
    command: str = args.command
    return handlers[command]()


def _init_db(container: Container) -> int:
    """Create or upgrade the schema."""
    upgrade_schema(container.engine)
    sys.stdout.write("Database schema is up to date.\n")
    return ExitCode.OK


def _list(container: Container) -> int:
    """Print the registered supermarket codes."""
    sys.stdout.write("\n".join(container.registry.codes()) + "\n")
    return ExitCode.OK


async def _run(container: Container, codes: Sequence[str], flags: RunFlags) -> int:
    """Scrape (or replay) the requested supermarkets and aggregate the exit code."""
    details = flags.details
    if flags.from_raw is not None:
        if codes or flags.save_raw:
            sys.stderr.write(
                "--from-raw replays one archive: do not combine it with supermarket codes "
                "or --save-raw\n"
            )
            return ExitCode.USAGE
        code, container = container.replaying(Path(flags.from_raw))
        selected = _select_supermarkets(container, [code])
        details = False
    else:
        selected = _select_supermarkets(container, codes)
    if not flags.dry_run:
        upgrade_schema(container.engine)
    results = await _sync_all(
        container, selected, dry_run=flags.dry_run, details=details, save_raw=flags.save_raw
    )
    return _print_sync_results(results)


async def _dump(container: Container, codes: Sequence[str]) -> int:
    """Download the requested catalogs to the raw archive, validating them, without the database."""
    selected = _select_supermarkets(container, codes)
    results = await _sync_all(container, selected, dry_run=True, details=False, save_raw=True)
    return _print_sync_results(results)


async def _sync_all(
    container: Container,
    codes: Sequence[str],
    *,
    dry_run: bool,
    details: bool | None,
    save_raw: bool,
) -> list[tuple[SyncReport, Path | None]]:
    """Sync each supermarket in turn, recording its responses when ``save_raw`` is set."""
    results: list[tuple[SyncReport, Path | None]] = []
    for code in codes:
        scoped = container.recording(code) if save_raw else container
        use_case = scoped.sync_use_case(dry_run=dry_run, fetch_details=details)
        async with scoped.registry.create(code, scoped.scraper_context) as source:
            report = await use_case.execute(source)
        results.append((report, scoped.raw_archive_dir(code) if save_raw else None))
    return results


def _print_sync_results(results: Sequence[tuple[SyncReport, Path | None]]) -> int:
    """Print each run report, and where its raw JSON went; return the aggregated exit code."""
    for report, archive in results:
        _print_report(report)
        if archive is not None:
            sys.stdout.write(f"  raw JSON saved to {archive}\n")
    return _exit_code(report.run.status for report, _ in results)


async def _enrich(container: Container, codes: Sequence[str], limit: int | None) -> int:
    """Backfill product details for the requested supermarkets and aggregate the exit code."""
    if limit is not None and limit < 0:
        sys.stderr.write("--limit must be zero or positive\n")
        return ExitCode.USAGE
    selected = _select_supermarkets(container, codes)
    upgrade_schema(container.engine)
    use_case = container.enrich_use_case(limit=limit)
    reports: list[EnrichReport] = []
    for code in selected:
        async with container.registry.create(code, container.scraper_context) as source:
            reports.append(await use_case.execute(source))
    for report in reports:
        _print_enrich_report(report)
    return _exit_code(report.status for report in reports)


def _select_supermarkets(container: Container, codes: Sequence[str]) -> list[str]:
    """Return the requested codes, or every registered one, rejecting unknown codes."""
    available = container.registry.codes()
    selected = list(codes) or list(available)
    for code in selected:
        if code not in available:
            msg = f"Unknown supermarket {code!r}. Available: {', '.join(available)}"
            raise UnknownSupermarketError(msg)
    return selected


def _exit_code(statuses: Iterable[RunStatus]) -> ExitCode:
    """Map the outcomes of several supermarkets to the worst matching exit code."""
    seen = set(statuses)
    if RunStatus.FAILED in seen:
        return ExitCode.FAILED
    if RunStatus.PARTIAL in seen:
        return ExitCode.PARTIAL
    return ExitCode.OK


def _report(container: Container, codes: Sequence[str], day: str | None, top: int) -> int:
    """Print the price report of each requested supermarket. Reads the database only."""
    if top < 1:
        sys.stderr.write("--top must be at least 1\n")
        return ExitCode.USAGE
    try:
        target = date.fromisoformat(day) if day else None
    except ValueError:
        sys.stderr.write("--day must be a date like 2026-10-23\n")
        return ExitCode.USAGE
    selected = _select_supermarkets(container, codes)
    upgrade_schema(container.engine)
    use_case = container.report_use_case()
    for code in selected:
        _print_price_report(use_case.execute(SupermarketCode(code), day=target, top=top), top)
    return ExitCode.OK


def _print_price_report(report: PriceReport, top: int) -> None:
    """Write a price report as a block of aligned lines."""
    if report.previous_day is None:
        heading = f"[{report.supermarket}] {report.day} (no previous day to compare)"
    else:
        heading = f"[{report.supermarket}] {report.day} vs {report.previous_day}"
    gtin_share = report.products_with_gtin / report.active_products * 100
    lines = [
        heading,
        f"  products priced  {report.active_products}",
        f"  with GTIN        {report.products_with_gtin} ({gtin_share:.1f} %)",
        f"  new arrivals     {report.new_arrivals} (chain badge)",
        f"  promotions       {report.promotions_active} active",
        f"  unknown ref unit {report.unknown_reference_units}",
    ]
    kinds = report.price_change_kinds
    if kinds is not None:
        lines.append(
            f"  price changes    {kinds.total} ({kinds.increased} up, {kinds.decreased} down, "
            f"{kinds.promotions_started} promo started, {kinds.promotions_ended} promo ended)"
        )
        lines.extend(_move_section("top increases", report.top_increases))
        lines.extend(_move_section("top decreases", report.top_decreases))
        lines.extend(_move_section("promotions started", report.promotions_started))
        lines.extend(_product_section("new products", report.new_products, top))
        lines.extend(_product_section("delisted", report.delisted_products, top))
    sys.stdout.write("\n".join(lines) + "\n")


def _move_section(title: str, moves: Sequence[PriceMove]) -> list[str]:
    """Render price moves as ``+8.12 %  36.95 -> 39.95  label`` lines under a title."""
    if not moves:
        return []
    return [
        f"  {title}",
        *(
            f"    {move.change_percentage:+.2f} %  {move.before.amount:.2f} -> "
            f"{move.after.amount:.2f}  {move.product.label}"
            for move in moves
        ),
    ]


def _product_section(title: str, products: Sequence[Product], top: int) -> list[str]:
    """Render a count and up to ``top`` product labels."""
    lines = [f"  {title:<16} {len(products)}"]
    lines.extend(f"    {product.label}" for product in products[:top])
    if len(products) > top:
        lines.append(f"    ... and {len(products) - top} more")
    return lines


def _copy_db(container: Container, target_url: str, *, dry_run: bool) -> int:
    """Copy the configured database into an empty target and print the rows moved."""
    if make_url(target_url) == make_url(container.settings.database_url):
        sys.stderr.write("--to must be a different database from SCRAPER_DATABASE_URL\n")
        return ExitCode.USAGE
    target = build_engine(target_url)
    try:
        upgrade_schema(container.engine)
        upgrade_schema(target)
        report = copy_database(container.engine, target, dry_run=dry_run)
    finally:
        target.dispose()
    outcome = "verified and rolled back (dry run)" if dry_run else "copied and verified"
    lines = [
        f"Database {outcome}:",
        *(f"  {name:<20} {rows}" for name, rows in report.rows.items()),
    ]
    sys.stdout.write("\n".join(lines) + "\n")
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
        f"{'delisted':>8} {'changes':>8} {'up':>5} {'down':>5} {'promo+':>6} {'promo-':>6} "
        f"{'errors':>6}"
    )
    lines = [header, "-" * len(header)]
    lines.extend(
        f"{run.started_at:%Y-%m-%d %H:%M:%S} {run.status.value:<10} {run.stats.products:>8} "
        f"{run.stats.new_products:>6} {run.stats.delisted_products:>8} "
        f"{run.stats.price_changes:>8} {_breakdown_columns(run.stats.price_change_kinds)} "
        f"{run.stats.errors:>6}"
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
        *_breakdown_lines(stats.price_change_kinds),
        f"  details enriched {stats.details_enriched}",
        f"  errors           {stats.errors}",
    ]
    if run.error_message:
        lines.append(f"  error            {run.error_message}")
    sys.stdout.write("\n".join(lines) + "\n")


def _breakdown_columns(kinds: PriceChangeCounts | None) -> str:
    """Render the price change breakdown as history columns, dashes when not classified."""
    if kinds is None:
        return f"{'-':>5} {'-':>5} {'-':>6} {'-':>6}"
    return (
        f"{kinds.increased:>5} {kinds.decreased:>5} "
        f"{kinds.promotions_started:>6} {kinds.promotions_ended:>6}"
    )


def _breakdown_lines(kinds: PriceChangeCounts | None) -> list[str]:
    """Render the price change breakdown under the run report's total, if there is one."""
    if kinds is None:
        return []
    return [
        f"    increases      {kinds.increased}",
        f"    decreases      {kinds.decreased}",
        f"    promo started  {kinds.promotions_started}",
        f"    promo ended    {kinds.promotions_ended}",
    ]


def _print_enrich_report(report: EnrichReport) -> None:
    """Write a one-block human summary of a detail enrichment to stdout."""
    lines = [
        f"[{report.supermarket}] {report.status.value.upper()} details enrichment",
        f"  pending          {report.pending}",
        f"  enriched         {report.enriched}",
        f"  failures         {report.failures}",
    ]
    if report.stopped_early:
        lines.append("  stopped early    a whole chunk failed; run enrich again later")
    lines.extend(
        f"  - {failure.scope}: {failure.reason}" for failure in report.reasons[:MAX_LISTED_FAILURES]
    )
    unlisted = len(report.reasons) - MAX_LISTED_FAILURES
    if unlisted > 0:
        lines.append(f"  ... and {unlisted} more (see the log for all of them)")
    sys.stdout.write("\n".join(lines) + "\n")


def _force_utf8_streams() -> None:
    """Emit UTF-8 on every platform so accented names never break logging on Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper) and stream.encoding.lower() != "utf-8":
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


if __name__ == "__main__":
    raise SystemExit(main())

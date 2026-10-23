from __future__ import annotations

from datetime import UTC, date, datetime
from typing import override
from zoneinfo import ZoneInfo

from supermarket_scraper.application.ports import Clock


class SystemClock(Clock):
    """Wall clock that reports instants in UTC and calendar days in a business timezone."""

    def __init__(self, timezone: str) -> None:
        """Resolve the IANA timezone used to decide which day a snapshot belongs to."""
        self._zone = ZoneInfo(timezone)

    @override
    def now(self) -> datetime:
        """Return the current instant in UTC."""
        return datetime.now(UTC)

    @override
    def today(self) -> date:
        """Return today's date in the business timezone."""
        return datetime.now(self._zone).date()


class FrozenClock(Clock):
    """Clock stopped at one instant, used to replay an archive under its capture day."""

    def __init__(self, at: datetime, timezone: str) -> None:
        """Freeze the clock at ``at`` and decide days in the IANA ``timezone``."""
        self._at = at.astimezone(UTC)
        self._zone = ZoneInfo(timezone)

    @override
    def now(self) -> datetime:
        """Return the frozen instant in UTC."""
        return self._at

    @override
    def today(self) -> date:
        """Return the frozen instant's date in the business timezone."""
        return self._at.astimezone(self._zone).date()

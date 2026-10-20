from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Final, override

_STANDARD_ATTRIBUTES: Final = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys() | {"message", "asctime"}
)


def _extras(record: logging.LogRecord) -> dict[str, object]:
    """Return the custom attributes passed through ``extra=`` on a log call."""
    return {
        key: _jsonable(value)
        for key, value in vars(record).items()
        if key not in _STANDARD_ATTRIBUTES
    }


def _jsonable(value: object) -> object:
    """Convert dataclasses and other objects into JSON-friendly values."""
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


class JsonFormatter(logging.Formatter):
    """One JSON object per line, ready for log aggregators."""

    @override
    def format(self, record: logging.LogRecord) -> str:
        """Serialize the record and its extras."""
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_extras(record),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Human-readable formatter that appends extras as ``key=value`` pairs."""

    def __init__(self) -> None:
        """Use a compact timestamped layout."""
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")

    @override
    def format(self, record: logging.LogRecord) -> str:
        """Render the base line followed by the extras."""
        base = super().format(record)
        extras = " ".join(f"{key}={value}" for key, value in _extras(record).items())
        return f"{base} | {extras}" if extras else base


def configure_logging(level: str, fmt: str) -> None:
    """Install a single stderr handler on the root logger."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("alembic").setLevel(logging.WARNING)

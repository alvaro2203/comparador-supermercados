"""Raw JSON archive: record the responses a scraper receives, and replay them without network.

An archive is one directory per supermarket and day, ``<raw_dir>/<code>/<YYYY-MM-DD>/``. Each
successful response body is stored byte for byte at a path that mirrors its URL path
(``/api/categories/112/`` becomes ``api/categories/112.json``); query parameters are not part of
the name. ``manifest.json`` records which supermarket the archive belongs to and when it was
captured, so a replay stores its prices under the capture day rather than the day it runs.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Final, override

import httpx

from supermarket_scraper.domain.exceptions import DomainError

MANIFEST_NAME: Final = "manifest.json"
_SUCCESS: Final = range(200, 300)


class RawArchiveError(DomainError):
    """Raised when an archive directory or one of its paths is missing or malformed."""


@dataclass(frozen=True, slots=True)
class Manifest:
    """Identity of an archive: which supermarket it holds and when it was captured (UTC)."""

    supermarket: str
    captured_at: datetime


def archive_path(root: Path, url_path: str) -> Path:
    """Return the file inside ``root`` that stores the response for ``url_path``.

    Raises:
        RawArchiveError: The URL path is empty or tries to leave the archive.
    """
    parts = [part for part in PurePosixPath(url_path).parts if part != "/"]
    if not parts or any(part in {".", ".."} for part in parts):
        msg = f"Cannot archive the response of {url_path!r}"
        raise RawArchiveError(msg)
    *folders, name = parts
    return root.joinpath(*folders, f"{name}.json")


def write_manifest(root: Path, manifest: Manifest) -> None:
    """Create the archive directory and write its manifest."""
    root.mkdir(parents=True, exist_ok=True)
    document = {
        "supermarket": manifest.supermarket,
        "captured_at": manifest.captured_at.isoformat(),
    }
    (root / MANIFEST_NAME).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def read_manifest(root: Path) -> Manifest:
    """Read the manifest of an archive directory.

    Raises:
        RawArchiveError: The directory has no manifest, or it is malformed.
    """
    path = root / MANIFEST_NAME
    if not path.is_file():
        msg = f"{root} is not a raw archive: {MANIFEST_NAME} is missing"
        raise RawArchiveError(msg)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        return Manifest(
            supermarket=str(document["supermarket"]),
            captured_at=datetime.fromisoformat(document["captured_at"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        msg = f"Invalid manifest in {root}: {exc}"
        raise RawArchiveError(msg) from exc


class RecordingTransport(httpx.AsyncBaseTransport):
    """Forward requests to a real transport and save every successful body in an archive."""

    def __init__(self, inner: httpx.AsyncBaseTransport, root: Path) -> None:
        """Wrap ``inner`` and record into the archive directory ``root``."""
        self._inner = inner
        self._root = root

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Send the request and, if it succeeded, store its body before returning it."""
        response = await self._inner.handle_async_request(request)
        if response.status_code not in _SUCCESS:
            return response
        body = await response.aread()
        await asyncio.to_thread(_write_bytes, archive_path(self._root, request.url.path), body)
        return response

    @override
    async def aclose(self) -> None:
        """Close the wrapped transport."""
        await self._inner.aclose()


class ReplayTransport(httpx.AsyncBaseTransport):
    """Serve requests from an archive: 200 with the stored body, or 404 if it was not saved."""

    def __init__(self, root: Path) -> None:
        """Serve from the archive directory ``root``."""
        self._root = root

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Return the archived body for the request's URL path."""
        path = archive_path(self._root, request.url.path)
        if not path.is_file():
            return httpx.Response(404, json={"detail": "not in the raw archive"}, request=request)
        body = await asyncio.to_thread(path.read_bytes)
        return httpx.Response(
            200, content=body, headers={"Content-Type": "application/json"}, request=request
        )


def _write_bytes(path: Path, body: bytes) -> None:
    """Write ``body`` to ``path``, creating its folders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)

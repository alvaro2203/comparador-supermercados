from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from supermarket_scraper.infrastructure.clock import FrozenClock
from supermarket_scraper.infrastructure.http.archive import (
    Manifest,
    RawArchiveError,
    RecordingTransport,
    ReplayTransport,
    archive_path,
    read_manifest,
    write_manifest,
)

if TYPE_CHECKING:
    from pathlib import Path

BODY = b'{"results": [], "note": "kept byte for byte"}'


def upstream(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/products/1/":
        return httpx.Response(404, json={"detail": "missing"})
    return httpx.Response(200, content=BODY, headers={"Content-Type": "application/json"})


@pytest.mark.parametrize(
    ("url_path", "relative"),
    [
        ("/api/categories/", "api/categories.json"),
        ("/api/categories/112/", "api/categories/112.json"),
        ("/api/products/4241", "api/products/4241.json"),
    ],
)
def test_archive_path_mirrors_the_url(tmp_path: Path, url_path: str, relative: str) -> None:
    assert archive_path(tmp_path, url_path) == tmp_path / relative


@pytest.mark.parametrize("url_path", ["/", "/api/../../etc/passwd", "/api/.."])
def test_archive_path_refuses_paths_outside_the_archive(tmp_path: Path, url_path: str) -> None:
    with pytest.raises(RawArchiveError, match="Cannot archive"):
        archive_path(tmp_path, url_path)


async def test_recording_saves_successful_bodies_and_replay_serves_them(tmp_path: Path) -> None:
    recorder = RecordingTransport(httpx.MockTransport(upstream), tmp_path)
    async with httpx.AsyncClient(transport=recorder, base_url="https://shop.test") as client:
        ok = await client.get("/api/categories/112/", params={"wh": "mad1"})
        missing = await client.get("/api/products/1/")

    assert ok.content == BODY
    assert missing.status_code == 404
    assert (tmp_path / "api/categories/112.json").read_bytes() == BODY
    assert not (tmp_path / "api/products/1.json").exists()

    async with httpx.AsyncClient(
        transport=ReplayTransport(tmp_path), base_url="https://shop.test"
    ) as client:
        replayed = await client.get("/api/categories/112/", params={"wh": "vlc1"})
        absent = await client.get("/api/products/1/")
    assert replayed.status_code == 200
    assert replayed.content == BODY
    assert absent.status_code == 404


def test_manifest_round_trip(tmp_path: Path) -> None:
    manifest = Manifest(supermarket="mercadona", captured_at=datetime(2026, 10, 23, 21, tzinfo=UTC))

    write_manifest(tmp_path, manifest)

    assert read_manifest(tmp_path) == manifest
    stored = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert stored == {"supermarket": "mercadona", "captured_at": "2026-10-23T21:00:00+00:00"}


def test_reading_an_archive_without_a_valid_manifest_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(RawArchiveError, match=r"manifest.json"):
        read_manifest(tmp_path)

    (tmp_path / "manifest.json").write_text('{"supermarket": "mercadona"}', encoding="utf-8")
    with pytest.raises(RawArchiveError, match="Invalid manifest"):
        read_manifest(tmp_path)


def test_frozen_clock_reports_the_capture_day_in_the_business_timezone() -> None:
    clock = FrozenClock(datetime(2026, 10, 23, 22, 30, tzinfo=UTC), "Europe/Madrid")

    assert clock.now() == datetime(2026, 10, 23, 22, 30, tzinfo=UTC)
    assert clock.today() == date(2026, 10, 24)

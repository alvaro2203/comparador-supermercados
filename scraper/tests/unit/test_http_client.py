from __future__ import annotations

import httpx
import pytest

from supermarket_scraper.domain.exceptions import SourceSchemaError, SourceUnavailableError
from supermarket_scraper.infrastructure.http.client import (
    AsyncJsonClient,
    HttpClientConfig,
    ResourceNotFoundError,
)

CONFIG = HttpClientConfig(max_retries=2, min_interval_seconds=0, backoff_base_seconds=0.01)


class Recorder:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def client_for(responses: list[httpx.Response], recorder: Recorder) -> AsyncJsonClient:
    queue = list(responses)

    def handler(_request: httpx.Request) -> httpx.Response:
        return queue.pop(0)

    return AsyncJsonClient(
        "https://x.test", CONFIG, transport=httpx.MockTransport(handler), sleep=recorder.sleep
    )


async def test_retries_transient_errors_then_succeeds() -> None:
    recorder = Recorder()
    client = client_for(
        [
            httpx.Response(503),
            httpx.Response(429, headers={"Retry-After": "3"}),
            httpx.Response(200, json={"ok": True}),
        ],
        recorder,
    )
    async with client:
        assert await client.get_json("/a") == {"ok": True}
    assert recorder.sleeps[-1] == 3.0
    assert len(recorder.sleeps) == 2


async def test_gives_up_after_max_retries() -> None:
    client = client_for([httpx.Response(500)] * 3, Recorder())
    async with client:
        with pytest.raises(SourceUnavailableError):
            await client.get_json("/a")


async def test_not_found_is_not_retried() -> None:
    recorder = Recorder()
    client = client_for([httpx.Response(404)], recorder)
    async with client:
        with pytest.raises(ResourceNotFoundError):
            await client.get_json("/a")
    assert recorder.sleeps == []


async def test_invalid_json_raises_schema_error() -> None:
    client = client_for([httpx.Response(200, content=b"<html>")], Recorder())
    async with client:
        with pytest.raises(SourceSchemaError):
            await client.get_json("/a")


async def test_transport_errors_are_retried() -> None:
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 2:
            msg = "reset"
            raise httpx.ConnectError(msg)
        return httpx.Response(200, json=[1])

    recorder = Recorder()
    client = AsyncJsonClient(
        "https://x.test", CONFIG, transport=httpx.MockTransport(handler), sleep=recorder.sleep
    )
    async with client:
        assert await client.get_json("/a") == [1]
    assert len(attempts) == 2

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Final, Self

import httpx

from supermarket_scraper.domain.exceptions import SourceSchemaError, SourceUnavailableError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from types import TracebackType

logger: Final = logging.getLogger(__name__)

RETRYABLE_STATUSES: Final = frozenset({408, 425, 429, 500, 502, 503, 504})
HTTP_NOT_FOUND: Final = 404
_JITTER: Final = secrets.SystemRandom()


class ResourceNotFoundError(SourceUnavailableError):
    """Raised when the remote resource does not exist (HTTP 404)."""


@dataclass(frozen=True, slots=True)
class HttpClientConfig:
    """Politeness and resilience parameters shared by every scraper."""

    timeout_seconds: float = 20.0
    max_retries: int = 4
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 30.0
    max_concurrency: int = 4
    min_interval_seconds: float = 0.25
    user_agent: str = "supermarket-scraper/0.1 (+price comparison research)"


class AsyncJsonClient:
    """Rate-limited, retrying JSON-over-HTTP client built on ``httpx``."""

    def __init__(
        self,
        base_url: str,
        config: HttpClientConfig,
        *,
        headers: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Create the underlying connection pool with the given politeness settings."""
        self._config = config
        self._sleep = sleep
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self._pace_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=config.timeout_seconds,
            transport=transport,
            follow_redirects=True,
            headers={
                "User-Agent": config.user_agent,
                "Accept": "application/json",
                "Accept-Language": "es-ES,es;q=0.9",
                **(headers or {}),
            },
            limits=httpx.Limits(max_connections=config.max_concurrency),
        )

    async def get_json(self, path: str, params: Mapping[str, str] | None = None) -> object:
        """GET ``path`` and return the decoded JSON body, retrying transient failures."""
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await self._send(path, params)
            except httpx.TransportError as exc:
                if attempt > self._config.max_retries:
                    msg = f"GET {path} failed after {attempt} attempts: {exc!r}"
                    raise SourceUnavailableError(msg) from exc
                await self._backoff(path, attempt, None)
                continue
            if response.status_code == HTTP_NOT_FOUND:
                msg = f"GET {path} returned 404"
                raise ResourceNotFoundError(msg)
            if response.status_code in RETRYABLE_STATUSES:
                if attempt > self._config.max_retries:
                    msg = f"GET {path} kept returning {response.status_code}"
                    raise SourceUnavailableError(msg)
                await self._backoff(path, attempt, _retry_after_seconds(response))
                continue
            if response.is_error:
                msg = f"GET {path} returned non-retryable status {response.status_code}"
                raise SourceUnavailableError(msg)
            return _decode(path, response)

    async def aclose(self) -> None:
        """Close the connection pool."""
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        """Enter the async context."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the client when leaving the async context."""
        await self.aclose()

    async def _send(self, path: str, params: Mapping[str, str] | None) -> httpx.Response:
        """Perform one request respecting the concurrency cap and the minimum interval."""
        async with self._semaphore:
            await self._pace()
            return await self._client.get(path, params=dict(params or {}))

    async def _pace(self) -> None:
        """Space requests out so that the source is never hammered."""
        async with self._pace_lock:
            elapsed = time.monotonic() - self._last_request_at
            wait = self._config.min_interval_seconds - elapsed
            if wait > 0:
                await self._sleep(wait)
            self._last_request_at = time.monotonic()

    async def _backoff(self, path: str, attempt: int, retry_after: float | None) -> None:
        """Sleep with exponential backoff and equal jitter, honouring ``Retry-After``."""
        ceiling = min(
            self._config.backoff_max_seconds,
            self._config.backoff_base_seconds * (2 ** (attempt - 1)),
        )
        jitter = _JITTER.uniform(0, ceiling / 2)
        delay = retry_after if retry_after is not None else ceiling / 2 + jitter
        logger.warning(
            "Retrying request", extra={"path": path, "attempt": attempt, "delay": round(delay, 2)}
        )
        await self._sleep(min(delay, self._config.backoff_max_seconds))


def _decode(path: str, response: httpx.Response) -> object:
    """Decode a JSON body, translating malformed payloads into a schema error."""
    try:
        payload: object = json.loads(response.content)
    except json.JSONDecodeError as exc:
        msg = f"GET {path} did not return valid JSON"
        raise SourceSchemaError(msg) from exc
    return payload


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Parse the ``Retry-After`` header as seconds or HTTP date."""
    header = response.headers.get("Retry-After")
    if header is None:
        return None
    try:
        return max(float(header), 0.0)
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return None
    return max(moment.timestamp() - time.time(), 0.0)

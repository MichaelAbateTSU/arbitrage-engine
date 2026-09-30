import asyncio
import json
import random
import time
from decimal import Decimal
from typing import Any

import httpx
import structlog

log = structlog.get_logger()


class VenueError(RuntimeError):
    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def decode(text: str) -> Any:
    if len(text) > 8_000_000:
        raise VenueError("RESPONSE_TOO_LARGE")
    try:
        return json.loads(
            text,
            parse_float=Decimal,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (ValueError, RecursionError) as exc:
        raise VenueError("MALFORMED_JSON") from exc


class PublicHTTP:
    def __init__(self, client: httpx.AsyncClient, rate: int = 5) -> None:
        self.client = client
        self.interval = 1 / rate
        self.lock = asyncio.Lock()
        self.last = 0.0

    async def get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in range(4):
            async with self.lock:
                await asyncio.sleep(max(0, self.last + self.interval - time.monotonic()))
                self.last = time.monotonic()
            try:
                response = await self.client.get(url, params=params, timeout=15)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                log.warning("venue_request_failed", error_code=type(exc).__name__, attempt=attempt)
                if attempt == 3:
                    raise VenueError("VENUE_TIMEOUT") from exc
            else:
                if response.status_code not in (429, 500, 502, 503, 504):
                    if not response.is_success:
                        raise VenueError("VENUE_HTTP_ERROR", response.status_code)
                    return decode(response.text)
                log.warning("venue_request_retry", status=response.status_code, attempt=attempt)
                if attempt == 3:
                    raise VenueError("VENUE_RETRY_EXHAUSTED", response.status_code)
            await asyncio.sleep(min(20, 2**attempt) + random.uniform(0, 0.5))
        raise VenueError("VENUE_RETRY_EXHAUSTED")

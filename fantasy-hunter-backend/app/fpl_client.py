"""Client for the official (but unofficial and undocumented) FPL API.

Defensive by design, per the plan's non-functional requirements: TTL cache,
bounded concurrency, retries with exponential backoff, and last-known-good
fallback so a slow or flaky origin degrades rather than breaks us.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger(__name__)


class FPLUnavailable(RuntimeError):
    """Origin failed and we have no cached copy to fall back on."""


class _TTLCache:
    def __init__(self, ttl: float) -> None:
        self._ttl = ttl
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        hit = self._data.get(key)
        if hit and (time.monotonic() - hit[0]) < self._ttl:
            return hit[1]
        return None

    def get_stale(self, key: str) -> Any | None:
        hit = self._data.get(key)
        return hit[1] if hit else None

    def set(self, key: str, value: Any) -> None:
        self._data[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self._data.clear()


class FPLClient:
    def __init__(self, base_url: str | None = None, ttl: float | None = None) -> None:
        self.base_url = (base_url or settings.fpl_base_url).rstrip("/")
        self._cache = _TTLCache(ttl if ttl is not None else settings.cache_ttl_seconds)
        self._semaphore = asyncio.Semaphore(settings.http_max_concurrency)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "FPLClient":
        self._client = httpx.AsyncClient(
            timeout=settings.http_timeout_seconds,
            headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, *, attempts: int = 4) -> Any:
        cached = self._cache.get(path)
        if cached is not None:
            return cached

        if self._client is None:
            raise RuntimeError("FPLClient must be used as an async context manager")

        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                async with self._semaphore:
                    response = await self._client.get(f"{self.base_url}{path}")
                if response.status_code == 404:
                    response.raise_for_status()
                if response.status_code >= 500 or response.status_code == 429:
                    raise httpx.HTTPStatusError(
                        f"upstream {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                payload = response.json()
                self._cache.set(path, payload)
                return payload
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    raise
                last_error = exc
            except (httpx.TransportError, ValueError) as exc:
                last_error = exc

            if attempt < attempts - 1:
                backoff = (2**attempt) * 0.5 + random.uniform(0, 0.3)
                log.warning("FPL GET %s failed (%s), retrying in %.1fs", path, last_error, backoff)
                await asyncio.sleep(backoff)

        stale = self._cache.get_stale(path)
        if stale is not None:
            log.warning("FPL GET %s exhausted retries; serving stale cache", path)
            return stale
        raise FPLUnavailable(f"GET {path} failed after {attempts} attempts: {last_error}")

    # --- endpoints -------------------------------------------------------

    async def bootstrap_static(self) -> dict:
        return await self._get("/bootstrap-static/")

    async def fixtures(self, event: int | None = None) -> list[dict]:
        path = "/fixtures/" if event is None else f"/fixtures/?event={event}"
        return await self._get(path)

    async def element_summary(self, element_id: int) -> dict:
        return await self._get(f"/element-summary/{element_id}/")

    async def entry(self, entry_id: int) -> dict:
        return await self._get(f"/entry/{entry_id}/")

    async def entry_picks(self, entry_id: int, event: int) -> dict:
        return await self._get(f"/entry/{entry_id}/event/{event}/picks/")

    async def entry_history(self, entry_id: int) -> dict:
        return await self._get(f"/entry/{entry_id}/history/")

    async def event_live(self, event: int) -> dict:
        return await self._get(f"/event/{event}/live/")

    async def element_summaries(self, element_ids: list[int]) -> dict[int, dict]:
        """Fetch many player summaries concurrently, skipping individual failures."""
        results: dict[int, dict] = {}

        async def one(element_id: int) -> None:
            try:
                results[element_id] = await self.element_summary(element_id)
            except Exception as exc:  # a single bad element must not fail the run
                log.warning("element-summary %s failed: %s", element_id, exc)

        await asyncio.gather(*(one(i) for i in element_ids))
        return results

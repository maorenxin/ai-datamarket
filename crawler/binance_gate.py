"""
Binance RPC Gate — singleton async HTTP client with rate limiting.

All Binance API requests go through this gate to:
- Share a single connection pool
- Enforce concurrency limits (max 3 parallel requests)
- Track Binance weight budget (1200/min per IP)
- Auto-retry on 429/418 with backoff
"""
import asyncio
import time
import logging
from typing import Any, List, Optional

import httpx

from crawler.base_gate import BaseGate

logger = logging.getLogger(__name__)

SPOT_BASE = "https://api.binance.com"
PERP_BASE = "https://fapi.binance.com"


class WeightCounter:
    """Sliding-window weight tracker for Binance IP rate limits."""

    def __init__(self, limit: int = 1100, window: int = 60):
        self.limit = limit
        self.window = window
        self._weight = 0
        self._window_start = time.monotonic()

    def _reset_if_needed(self):
        now = time.monotonic()
        if now - self._window_start >= self.window:
            self._weight = 0
            self._window_start = now

    def add(self, w: int):
        self._reset_if_needed()
        self._weight += w

    async def wait_if_needed(self, cost: int = 2):
        self._reset_if_needed()
        if self._weight + cost >= self.limit:
            sleep_for = self.window - (time.monotonic() - self._window_start) + 1
            logger.warning("Weight budget nearly exhausted, sleeping %.1fs", sleep_for)
            await asyncio.sleep(max(sleep_for, 1))
            self._reset_if_needed()
        self.add(cost)


class BinanceGate(BaseGate):
    """Singleton gate for all Binance HTTP requests."""

    _instance: Optional["BinanceGate"] = None
    MIN_INTERVAL = 0.2  # seconds between requests

    def __new__(cls):
        if cls._instance is None:
            inst = super().__new__(cls)
            # Create asyncio primitives lazily at instance creation time
            # (avoids binding to the wrong event loop at class definition)
            inst._semaphore = None
            inst._weight_counter = WeightCounter(limit=1100, window=60)
            inst._client = None
            inst._last_request_time = 0.0
            cls._instance = inst
        return cls._instance

    def _get_semaphore(self):
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(3)
        return self._semaphore

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                headers={"User-Agent": "ai-datamarket/0.1"},
            )
        return self._client

    async def get_klines(
        self,
        market_type: str,  # "spot" | "perp"
        params: dict,
        weight: int = 2,
        **kwargs: Any,
    ) -> List[list]:
        base = SPOT_BASE if market_type == "spot" else PERP_BASE
        path = "/api/v3/klines" if market_type == "spot" else "/fapi/v1/klines"
        url = base + path

        async with self._get_semaphore():
            # Enforce minimum interval between requests
            elapsed = time.monotonic() - self._last_request_time
            if elapsed < self.MIN_INTERVAL:
                await asyncio.sleep(self.MIN_INTERVAL - elapsed)

            await self._weight_counter.wait_if_needed(weight)
            client = await self._get_client()

            for attempt in range(5):
                try:
                    resp = await client.get(url, params=params)
                    self._last_request_time = time.monotonic()

                    if resp.status_code == 200:
                        return resp.json()

                    if resp.status_code in (429, 418):
                        retry_after = int(resp.headers.get("Retry-After", 60))
                        wait = retry_after * (2 ** attempt)
                        logger.warning("Rate limited (%d), waiting %ds", resp.status_code, wait)
                        await asyncio.sleep(wait)
                        continue

                    resp.raise_for_status()

                except httpx.TimeoutException:
                    wait = 2 ** attempt
                    logger.warning("Timeout on attempt %d, retrying in %ds", attempt + 1, wait)
                    await asyncio.sleep(wait)
                except httpx.HTTPStatusError as e:
                    logger.error("HTTP error: %s", e)
                    raise

            raise RuntimeError(f"Failed after 5 attempts: {url} {params}")

    def max_batch_size(self, market_type: str) -> int:
        return 1500 if market_type == "perp" else 1000

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        # Reset semaphore so it's recreated on next use (in a new event loop)
        self._semaphore = None


def get_gate() -> "BinanceGate":
    """Return (or create) the module-level gate instance."""
    return BinanceGate()


# Module-level singleton — use get_gate() to access it
gate = get_gate()

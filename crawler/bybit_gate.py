"""
Bybit RPC Gate — singleton async HTTP client with rate limiting.

Bybit API v5 docs: https://bybit-exchange.github.io/docs/v5/market/kline
- GET /v5/market/kline
- Params: category=spot|linear, symbol, interval=1, start, end (ms), limit=200
- Max 200 candles per request
- Returns data in DESCENDING order (newest first) — we reverse internally.
- Does NOT return trade_count — we set it to 0.
"""
import asyncio
import time
import logging
from typing import Any, List, Optional

import httpx

from crawler.base_gate import BaseGate

logger = logging.getLogger(__name__)

BYBIT_BASE = "https://api.bybit.com"


class BybitGate(BaseGate):
    """Singleton gate for all Bybit HTTP requests."""

    _instance = None  # type: Optional[BybitGate]
    MIN_INTERVAL = 0.1  # 100ms between requests

    def __new__(cls):
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._semaphore = None
            inst._client = None
            inst._last_request_time = 0.0
            cls._instance = inst
        return cls._instance

    def _get_semaphore(self):
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(5)
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
        market_type: str,
        params: dict,
        **kwargs: Any,
    ) -> List[list]:
        """
        Fetch klines from Bybit.

        Expected params keys: symbol, interval, startTime (ms), limit
        Optional: endTime (ms)

        Bybit API params:
          category = "spot" | "linear" (for perp)
          symbol = params["symbol"]  (e.g. "BTCUSDT")
          interval = "1" (1 minute)
          start = startTime (ms)
          end = endTime (ms)
          limit = min(params["limit"], 200)
        """
        symbol = params["symbol"]
        limit = min(params.get("limit", 200), 200)
        start_time = params.get("startTime")
        end_time = params.get("endTime")

        category = "spot" if market_type == "spot" else "linear"

        bybit_params = {
            "category": category,
            "symbol": symbol,
            "interval": "1",
            "limit": limit,
        }
        if start_time is not None:
            bybit_params["start"] = start_time
            # Cap end to start + limit minutes so we get the oldest batch first
            # (Bybit returns newest-first within the range)
            batch_end = start_time + limit * 60 * 1000
            if end_time is not None:
                batch_end = min(batch_end, end_time)
            bybit_params["end"] = batch_end
        elif end_time is not None:
            bybit_params["end"] = end_time

        url = BYBIT_BASE + "/v5/market/kline"

        async with self._get_semaphore():
            elapsed = time.monotonic() - self._last_request_time
            if elapsed < self.MIN_INTERVAL:
                await asyncio.sleep(self.MIN_INTERVAL - elapsed)

            client = await self._get_client()

            for attempt in range(5):
                try:
                    resp = await client.get(url, params=bybit_params)
                    self._last_request_time = time.monotonic()

                    if resp.status_code == 200:
                        body = resp.json()
                        ret_code = body.get("retCode", -1)
                        if ret_code != 0:
                            msg = body.get("retMsg", "unknown")
                            if "too many" in msg.lower() or ret_code == 10006:
                                wait = 2 ** attempt
                                logger.warning("Bybit rate limited, waiting %ds: %s", wait, msg)
                                await asyncio.sleep(wait)
                                continue
                            logger.error("Bybit API error: %s", body)
                            return []

                        raw_list = body.get("result", {}).get("list", [])
                        if not raw_list:
                            return []

                        # Bybit returns newest first — reverse to ascending order
                        raw_list.reverse()

                        # Bybit format: [startTime, open, high, low, close, volume, turnover]
                        # Standardize to: [open_time_ms, O, H, L, C, V, quote_vol, trade_count]
                        result = []
                        for row in raw_list:
                            result.append([
                                int(row[0]),      # open_time_ms
                                row[1],           # open
                                row[2],           # high
                                row[3],           # low
                                row[4],           # close
                                row[5],           # volume (base)
                                row[6],           # turnover (quote volume)
                                0,                # trade_count (not provided by Bybit)
                            ])
                        return result

                    if resp.status_code == 429:
                        wait = 2 ** attempt
                        logger.warning("Bybit 429 rate limited, waiting %ds", wait)
                        await asyncio.sleep(wait)
                        continue

                    resp.raise_for_status()

                except httpx.TimeoutException:
                    wait = 2 ** attempt
                    logger.warning("Bybit timeout attempt %d, retrying in %ds", attempt + 1, wait)
                    await asyncio.sleep(wait)
                except httpx.HTTPStatusError as e:
                    logger.error("Bybit HTTP error: %s", e)
                    raise

            raise RuntimeError("Bybit: failed after 5 attempts: {} {}".format(url, bybit_params))

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._semaphore = None

    def max_batch_size(self, market_type: str) -> int:
        return 200


def get_gate() -> BybitGate:
    return BybitGate()


gate = get_gate()

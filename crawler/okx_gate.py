"""
OKX RPC Gate — singleton async HTTP client with rate limiting.

OKX API docs: https://www.okx.com/docs-v5/en/
- GET /api/v5/market/history-candles (historical)
- GET /api/v5/market/candles (recent)
- Max 100 candles per request
- Rate limit: 20 requests per 2 seconds
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

OKX_BASE = "https://www.okx.com"


class OKXGate(BaseGate):
    """Singleton gate for all OKX HTTP requests."""

    _instance = None  # type: Optional[OKXGate]
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
        Fetch klines from OKX.

        Expected params keys: symbol, interval, startTime (ms), limit
        Optional: endTime (ms)

        OKX API params mapping:
          instId = params["symbol"]  (e.g. "BTC-USDT" or "BTC-USDT-SWAP")
          bar = "1m"
          after = startTime - 1  (OKX 'after' means "return candles before this ts")
          before = endTime (exclusive upper bound — returns candles after this ts)
          limit = min(params["limit"], 100)
        """
        inst_id = params["symbol"]
        limit = min(params.get("limit", 100), 100)
        start_time = params.get("startTime")
        end_time = params.get("endTime")

        # OKX history-candles pagination:
        #   `before` = only return candles with ts > before (newer than)
        #   `after`  = only return candles with ts < after  (older than)
        #
        # The unified crawler paginates FORWARD: it sends startTime (advancing)
        # and endTime (fixed). We use:
        #   before = startTime - 1  → get candles >= startTime
        #   after  = startTime + limit*60000  → cap the batch to ~limit candles forward
        # This ensures we get the OLDEST candles in the range first.
        okx_params = {
            "instId": inst_id,
            "bar": "1m",
            "limit": str(limit),
        }
        if start_time is not None:
            okx_params["before"] = str(start_time - 1)
            # Cap upper bound to startTime + limit minutes to get oldest-first batch
            batch_end = start_time + limit * 60 * 1000
            if end_time is not None:
                batch_end = min(batch_end, end_time)
            okx_params["after"] = str(batch_end)

        # Use history-candles for older data, candles for recent
        path = "/api/v5/market/history-candles"

        async with self._get_semaphore():
            elapsed = time.monotonic() - self._last_request_time
            if elapsed < self.MIN_INTERVAL:
                await asyncio.sleep(self.MIN_INTERVAL - elapsed)

            client = await self._get_client()
            url = OKX_BASE + path

            for attempt in range(5):
                try:
                    resp = await client.get(url, params=okx_params)
                    self._last_request_time = time.monotonic()

                    if resp.status_code == 200:
                        body = resp.json()
                        if body.get("code") != "0":
                            msg = body.get("msg", "unknown error")
                            if "Too many" in msg or body.get("code") == "50011":
                                wait = 2 ** attempt
                                logger.warning("OKX rate limited, waiting %ds: %s", wait, msg)
                                await asyncio.sleep(wait)
                                continue
                            logger.error("OKX API error: %s", body)
                            return []

                        raw_data = body.get("data", [])
                        if not raw_data:
                            return []

                        # OKX returns newest first — reverse to ascending order
                        raw_data.reverse()

                        # Standardize to: [open_time_ms, O, H, L, C, V, quote_vol, trade_count]
                        # OKX format: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
                        result = []
                        for row in raw_data:
                            result.append([
                                int(row[0]),      # open_time_ms
                                row[1],           # open
                                row[2],           # high
                                row[3],           # low
                                row[4],           # close
                                row[5],           # volume (base)
                                row[6],           # volCcy (quote volume approx)
                                0,                # trade_count (not provided by OKX)
                            ])
                        return result

                    if resp.status_code == 429:
                        wait = 2 ** attempt
                        logger.warning("OKX 429 rate limited, waiting %ds", wait)
                        await asyncio.sleep(wait)
                        continue

                    resp.raise_for_status()

                except httpx.TimeoutException:
                    wait = 2 ** attempt
                    logger.warning("OKX timeout attempt %d, retrying in %ds", attempt + 1, wait)
                    await asyncio.sleep(wait)
                except httpx.HTTPStatusError as e:
                    logger.error("OKX HTTP error: %s", e)
                    raise

            raise RuntimeError("OKX: failed after 5 attempts: {} {}".format(url, okx_params))

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._semaphore = None

    def max_batch_size(self, market_type: str) -> int:
        return 100


def get_gate() -> OKXGate:
    return OKXGate()


gate = get_gate()

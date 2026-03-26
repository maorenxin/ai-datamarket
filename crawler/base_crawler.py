"""
Abstract base class for OHLCV time-series crawlers.

Subclasses define a domain (e.g. "crypto") and implement get_targets() / get_gate().
All common logic — DB access, checkpoint, parse, insert, fetch loop — lives here.
"""
import argparse
import asyncio
import calendar
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import duckdb

from config.domains import get_db_path, OHLCV_SCHEMA
from crawler.data_validator import validate_batch

logger = logging.getLogger(__name__)


class BaseCrawler:
    """OHLCV time-series crawler base class.

    Subclasses must set `domain` and implement `get_targets()` and `get_gate()`.
    """

    domain = None  # type: Optional[str]  # e.g. "crypto", "us_stock"

    # ------------------------------------------------------------------
    # Subclass interface
    # ------------------------------------------------------------------

    def get_targets(self, platform):
        # type: (str) -> List[dict]
        """Return crawl targets for a platform. Each target dict has:
        symbol, market_type, start, exchange.
        """
        raise NotImplementedError

    def get_gate(self, platform):
        # type: (str) -> Any
        """Return the API gate instance for a platform."""
        raise NotImplementedError

    async def close_gates(self):
        # type: () -> None
        """Close all gate HTTP clients. Override if needed."""
        pass

    # ------------------------------------------------------------------
    # DB helpers — each platform writes its own .duckdb file
    # ------------------------------------------------------------------

    def get_db(self, platform):
        # type: (str) -> duckdb.DuckDBPyConnection
        """Get a write connection with retry on lock contention."""
        path = get_db_path(self.domain, platform)
        for attempt in range(60):
            try:
                return duckdb.connect(str(path))
            except duckdb.IOException:
                if attempt == 59:
                    raise
                time.sleep(0.2 + random.random() * min(attempt * 0.1, 1.8))

    def ensure_schema(self, platform):
        # type: (str) -> None
        """Create the ohlcv_1m table if it doesn't exist."""
        path = get_db_path(self.domain, platform)
        path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(path))
        con.execute(OHLCV_SCHEMA)
        con.close()

    # ------------------------------------------------------------------
    # Checkpoint / parse / insert
    # ------------------------------------------------------------------

    def get_checkpoint(self, con, symbol, market_type, exchange):
        # type: (duckdb.DuckDBPyConnection, str, str, str) -> Optional[datetime]
        """Return MAX(open_time) for this symbol+market_type+exchange, or None."""
        row = con.execute(
            "SELECT MAX(open_time) FROM ohlcv_1m WHERE symbol=? AND market_type=? AND exchange=?",
            [symbol, market_type, exchange],
        ).fetchone()
        val = row[0] if row else None
        if val is None:
            return None
        if isinstance(val, datetime):
            return val.replace(tzinfo=None)
        return val

    @staticmethod
    def parse_kline(raw, symbol, market_type, exchange):
        # type: (list, str, str, str) -> dict
        """Convert standardized kline list to ohlcv_1m dict.
        All gates return: [open_time_ms, O, H, L, C, V, quote_vol, trade_count]
        """
        open_time_ms = int(raw[0])
        return {
            "symbol": symbol,
            "market_type": market_type,
            "exchange": exchange,
            "open_time": datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc).replace(tzinfo=None),
            "open": float(raw[1]),
            "high": float(raw[2]),
            "low": float(raw[3]),
            "close": float(raw[4]),
            "volume": float(raw[5]),
            "quote_volume": float(raw[6]),
            "trade_count": int(float(raw[7])),
        }

    @staticmethod
    def insert_rows(con, rows):
        # type: (duckdb.DuckDBPyConnection, List[dict]) -> int
        """Insert rows, ignoring duplicates. Returns count inserted."""
        if not rows:
            return 0
        con.executemany(
            """
            INSERT OR IGNORE INTO ohlcv_1m
            (symbol, market_type, exchange, open_time, open, high, low, close, volume, quote_volume, trade_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r["symbol"], r["market_type"], r["exchange"], r["open_time"],
                    r["open"], r["high"], r["low"], r["close"],
                    r["volume"], r["quote_volume"], r["trade_count"],
                )
                for r in rows
            ],
        )
        return len(rows)

    # ------------------------------------------------------------------
    # Fetch loop
    # ------------------------------------------------------------------

    async def fetch_and_store(self, symbol, market_type, exchange, start_dt, end_dt):
        # type: (str, str, str, datetime, datetime) -> int
        """Fetch klines in batches and write to the platform's DB file."""
        gate = self.get_gate(exchange)
        batch_size = gate.max_batch_size(market_type)
        current_ms = int(calendar.timegm(start_dt.timetuple())) * 1000
        end_ms = int(calendar.timegm(end_dt.timetuple())) * 1000
        total = 0

        while current_ms < end_ms:
            params = {
                "symbol": symbol,
                "interval": "1m",
                "startTime": current_ms,
                "endTime": end_ms,
                "limit": batch_size,
            }

            try:
                raw = await gate.get_klines(market_type, params)
            except Exception as e:
                logger.error("%s %s %s: fetch error at %s: %s", exchange, symbol, market_type, current_ms, e)
                break

            if not raw:
                break

            rows = [self.parse_kline(r, symbol, market_type, exchange) for r in raw]
            valid_rows = validate_batch(rows, log_warnings=False)

            con = self.get_db(exchange)
            inserted = self.insert_rows(con, valid_rows)
            con.close()
            total += inserted

            await asyncio.sleep(0.1)

            last_open_time_ms = int(raw[-1][0])
            current_ms = last_open_time_ms + 60_000

            logger.info(
                "%s %s %s: fetched %d, inserted %d, up to %s",
                exchange, symbol, market_type, len(raw), inserted,
                datetime.utcfromtimestamp(last_open_time_ms / 1000).strftime("%Y-%m-%d %H:%M"),
            )

            if len(raw) < batch_size:
                break

        return total

    async def backfill_target(self, target, days=None):
        # type: (dict, Optional[int]) -> None
        """Backfill one symbol on one exchange."""
        symbol = target["symbol"]
        market_type = target["market_type"]
        exchange = target["exchange"]

        con = self.get_db(exchange)
        checkpoint = self.get_checkpoint(con, symbol, market_type, exchange)
        con.close()

        now_utc = datetime.utcnow().replace(second=0, microsecond=0)

        if days is not None:
            start_dt = now_utc - timedelta(days=days)
            if checkpoint and checkpoint > start_dt:
                start_dt = checkpoint + timedelta(minutes=1)
        else:
            start_dt = datetime.strptime(target["start"], "%Y-%m-%d")
            if checkpoint:
                logger.info("%s %s %s: full backfill from %s (have data up to %s)",
                            exchange, symbol, market_type, start_dt.date(), checkpoint.isoformat())
            else:
                logger.info("%s %s %s: full backfill from %s", exchange, symbol, market_type, start_dt.date())

        if start_dt >= now_utc:
            logger.info("%s %s %s: already up to date", exchange, symbol, market_type)
            return

        logger.info("%s %s %s: fetching %s -> %s", exchange, symbol, market_type, start_dt.date(), now_utc.date())
        total = await self.fetch_and_store(symbol, market_type, exchange, start_dt, now_utc)
        logger.info("%s %s %s: backfill complete, %d total rows inserted", exchange, symbol, market_type, total)

    async def live_update(self, platforms):
        # type: (List[str]) -> None
        """Run incremental updates every 60 seconds."""
        logger.info("Starting live update loop (60s interval) for: %s", platforms)
        while True:
            for platform in platforms:
                targets = self.get_targets(platform)
                for target in targets:
                    for retry in range(3):
                        try:
                            await self.backfill_target(target)
                            break
                        except duckdb.IOException:
                            if retry < 2:
                                await asyncio.sleep(5)
                            else:
                                logger.error("Error updating %s %s %s: DuckDB lock failed after 3 retries",
                                             target["exchange"], target["symbol"], target["market_type"])
                        except Exception as e:
                            logger.error("Error updating %s %s %s: %s",
                                         target["exchange"], target["symbol"], target["market_type"], e)
                            break
            await asyncio.sleep(60)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(self, mode, platforms, days=None, symbol=None):
        # type: (str, List[str], Optional[int], Optional[str]) -> None
        """Run the crawler in backfill or live mode."""
        # Ensure schema for all target platforms
        for platform in platforms:
            self.ensure_schema(platform)

        try:
            if mode == "backfill":
                for platform in platforms:
                    targets = self.get_targets(platform)
                    if symbol:
                        targets = [t for t in targets if t["symbol"] == symbol]
                    if not targets:
                        logger.warning("No targets found for %s symbol=%s", platform, symbol)
                        continue
                    logger.info("Starting backfill for %s: %d targets", platform, len(targets))
                    for t in targets:
                        for retry in range(3):
                            try:
                                await self.backfill_target(t, days)
                                break
                            except duckdb.IOException as e:
                                if retry < 2:
                                    logger.warning("%s %s %s: DuckDB lock failed, retry %d/3 in 5s",
                                                   t["exchange"], t["symbol"], t["market_type"], retry + 1)
                                    await asyncio.sleep(5)
                                else:
                                    logger.error("%s %s %s: DuckDB lock failed after 3 retries: %s",
                                                 t["exchange"], t["symbol"], t["market_type"], e)
                            except Exception as e:
                                logger.error("Error backfilling %s %s %s: %s",
                                             t["exchange"], t["symbol"], t["market_type"], e)
                                break
            elif mode == "live":
                await self.live_update(platforms)
            else:
                logger.error("Unknown mode: %s", mode)
        finally:
            await self.close_gates()

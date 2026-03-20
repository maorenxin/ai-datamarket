"""
Binance OHLCV Crawler — historical backfill + live incremental updates.

Usage:
    # Backfill last 7 days for all targets (quick test mode)
    python binance_crawler.py --mode backfill --days 7

    # Full historical backfill from each symbol's start date
    python binance_crawler.py --mode backfill

    # Live incremental updates (runs indefinitely, polls every 60s)
    python binance_crawler.py --mode live
"""
import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional

import duckdb

# Add parent dir to path so we can import from sibling packages
sys.path.insert(0, str(Path(__file__).parent.parent))

from crawler.binance_gate import gate
from crawler.data_validator import validate_batch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "market.duckdb"

TARGETS = [
    {"symbol": "BTCUSDT", "market_type": "spot", "start": "2017-08-17"},
    {"symbol": "ETHUSDT", "market_type": "spot", "start": "2017-08-17"},
    {"symbol": "SOLUSDT", "market_type": "spot", "start": "2020-08-11"},
    {"symbol": "BTCUSDT", "market_type": "perp", "start": "2019-09-25"},
    {"symbol": "ETHUSDT", "market_type": "perp", "start": "2019-11-27"},
    {"symbol": "SOLUSDT", "market_type": "perp", "start": "2021-09-29"},
]

SPOT_BATCH = 1000
PERP_BATCH = 1500


def get_db() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB_PATH))


def get_checkpoint(con: duckdb.DuckDBPyConnection, symbol: str, market_type: str) -> Optional[datetime]:
    """Return MAX(open_time) for this symbol+market_type, or None if no data."""
    row = con.execute(
        "SELECT MAX(open_time) FROM ohlcv_1m WHERE symbol=? AND market_type=? AND exchange='binance'",
        [symbol, market_type],
    ).fetchone()
    val = row[0] if row else None
    if val is None:
        return None
    # duckdb returns datetime objects
    if isinstance(val, datetime):
        return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val
    return val


def parse_kline(raw: list, symbol: str, market_type: str) -> dict[str, Any]:
    """Convert raw Binance kline list to a dict matching ohlcv_1m schema."""
    open_time_ms = int(raw[0])
    return {
        "symbol": symbol,
        "market_type": market_type,
        "exchange": "binance",
        "open_time": datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc).replace(tzinfo=None),
        "open": float(raw[1]),
        "high": float(raw[2]),
        "low": float(raw[3]),
        "close": float(raw[4]),
        "volume": float(raw[5]),
        "quote_volume": float(raw[7]),
        "trade_count": int(raw[8]),
    }


def insert_rows(con: duckdb.DuckDBPyConnection, rows: List[dict]) -> int:
    """Insert rows, ignoring duplicates. Returns count inserted."""
    if not rows:
        return 0
    # Use parameterized insert
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


async def fetch_and_store(
    con: duckdb.DuckDBPyConnection,
    symbol: str,
    market_type: str,
    start_dt: datetime,
    end_dt: datetime,
) -> int:
    """Fetch klines in batches from start_dt to end_dt and write to DB. Returns total rows inserted."""
    batch_size = PERP_BATCH if market_type == "perp" else SPOT_BATCH
    current = start_dt
    total = 0

    while current < end_dt:
        params = {
            "symbol": symbol,
            "interval": "1m",
            "startTime": int(current.timestamp() * 1000),
            "endTime": int(end_dt.timestamp() * 1000),
            "limit": batch_size,
        }
        raw = await gate.get_klines(market_type, params)
        if not raw:
            break

        rows = [parse_kline(r, symbol, market_type) for r in raw]
        valid_rows = validate_batch(rows, log_warnings=False)
        inserted = insert_rows(con, valid_rows)
        total += inserted

        # Advance past last fetched candle
        last_open_time_ms = int(raw[-1][0])
        current = datetime.fromtimestamp(last_open_time_ms / 1000, tz=timezone.utc) + timedelta(minutes=1)
        current = current.replace(tzinfo=None)

        logger.info(
            "%s %s: fetched %d, inserted %d, up to %s",
            symbol, market_type, len(raw), inserted,
            datetime.fromtimestamp(last_open_time_ms / 1000).strftime("%Y-%m-%d %H:%M"),
        )

        if len(raw) < batch_size:
            break  # reached the end

    return total


async def backfill_target(target: dict, days: Optional[int] = None):
    """Backfill one symbol. If days is set, only fetch the last N days."""
    symbol = target["symbol"]
    market_type = target["market_type"]
    con = get_db()

    checkpoint = get_checkpoint(con, symbol, market_type)
    now_utc = datetime.utcnow().replace(second=0, microsecond=0)

    if days is not None:
        # Quick mode: only last N days
        start_dt = now_utc - timedelta(days=days)
        if checkpoint and checkpoint > start_dt:
            start_dt = checkpoint + timedelta(minutes=1)
    else:
        # Full historical backfill
        if checkpoint:
            start_dt = checkpoint + timedelta(minutes=1)
            logger.info("%s %s: resuming from %s", symbol, market_type, checkpoint.isoformat())
        else:
            start_dt = datetime.strptime(target["start"], "%Y-%m-%d")
            logger.info("%s %s: starting from %s", symbol, market_type, start_dt.date())

    if start_dt >= now_utc:
        logger.info("%s %s: already up to date", symbol, market_type)
        con.close()
        return

    logger.info("%s %s: fetching %s → %s", symbol, market_type, start_dt.date(), now_utc.date())
    total = await fetch_and_store(con, symbol, market_type, start_dt, now_utc)
    logger.info("%s %s: backfill complete, %d total rows inserted", symbol, market_type, total)
    con.close()


async def live_update():
    """Run incremental updates every 60 seconds for all targets."""
    logger.info("Starting live update loop (60s interval)")
    while True:
        for target in TARGETS:
            try:
                await backfill_target(target)
            except Exception as e:
                logger.error("Error updating %s %s: %s", target["symbol"], target["market_type"], e)
        await asyncio.sleep(60)


async def main(mode: str, days: Optional[int]):
    try:
        if mode == "backfill":
            tasks = [backfill_target(t, days) for t in TARGETS]
            await asyncio.gather(*tasks)
        elif mode == "live":
            await live_update()
        else:
            logger.error("Unknown mode: %s", mode)
    finally:
        await gate.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Binance OHLCV Crawler")
    parser.add_argument("--mode", choices=["backfill", "live"], default="backfill")
    parser.add_argument("--days", type=int, default=None, help="Backfill only last N days (quick test)")
    args = parser.parse_args()
    asyncio.run(main(args.mode, args.days))

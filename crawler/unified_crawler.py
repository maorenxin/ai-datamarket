"""
Unified multi-exchange OHLCV Crawler — historical backfill + live incremental updates.

Supports Binance, OKX, and Bybit. Uses the gate_registry for exchange-specific HTTP clients
and config.symbols for target generation.

Usage:
    # Backfill last 7 days for all Binance targets
    python unified_crawler.py --exchange binance --mode backfill --days 7

    # Full historical backfill for OKX
    python unified_crawler.py --exchange okx --mode backfill

    # Backfill a single symbol on Bybit
    python unified_crawler.py --exchange bybit --mode backfill --symbol BTCUSDT --days 1

    # Live updates for all exchanges
    python unified_crawler.py --exchange all --mode live

    # All exchanges backfill
    python unified_crawler.py --exchange all --mode backfill --days 7
"""
import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional

import duckdb

# Add parent dir to path so we can import from sibling packages
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.symbols import get_targets
from crawler.gate_registry import get_gate, close_all
from crawler.data_validator import validate_batch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "market.duckdb"


def get_db() -> duckdb.DuckDBPyConnection:
    """Get a write connection with retry on lock contention (3 concurrent writers)."""
    for attempt in range(50):
        try:
            return duckdb.connect(str(DB_PATH))
        except duckdb.IOException:
            if attempt == 49:
                raise
            time.sleep(0.1 * (1 + attempt % 10))  # 100ms-1s jittered, up to ~5s total


def get_checkpoint(con: duckdb.DuckDBPyConnection, symbol: str, market_type: str, exchange: str) -> Optional[datetime]:
    """Return MAX(open_time) for this symbol+market_type+exchange, or None if no data."""
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


def parse_kline(raw: list, symbol: str, market_type: str, exchange: str) -> dict:
    """Convert standardized kline list to a dict matching ohlcv_1m schema.

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


def insert_rows(con: duckdb.DuckDBPyConnection, rows: List[dict]) -> int:
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


async def fetch_and_store(
    symbol: str,
    market_type: str,
    exchange: str,
    start_dt: datetime,
    end_dt: datetime,
) -> int:
    """Fetch klines in batches from start_dt to end_dt and write to DB."""
    gate = get_gate(exchange)
    batch_size = gate.max_batch_size(market_type)
    # Work in milliseconds to avoid timezone pitfalls with naive datetimes
    import calendar
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

        rows = [parse_kline(r, symbol, market_type, exchange) for r in raw]
        valid_rows = validate_batch(rows, log_warnings=False)

        # Open connection, insert, close immediately — lock held only during INSERT
        con = get_db()
        inserted = insert_rows(con, valid_rows)
        con.close()
        total += inserted

        # Yield time so API read connections can acquire the lock
        await asyncio.sleep(0.1)

        # Advance past last fetched candle
        last_open_time_ms = int(raw[-1][0])
        current_ms = last_open_time_ms + 60_000

        logger.info(
            "%s %s %s: fetched %d, inserted %d, up to %s",
            exchange, symbol, market_type, len(raw), inserted,
            datetime.utcfromtimestamp(last_open_time_ms / 1000).strftime("%Y-%m-%d %H:%M"),
        )

        if len(raw) < batch_size:
            break  # reached the end

    return total


async def backfill_target(target: dict, days: Optional[int] = None):
    """Backfill one symbol on one exchange."""
    symbol = target["symbol"]
    market_type = target["market_type"]
    exchange = target["exchange"]

    con = get_db()
    checkpoint = get_checkpoint(con, symbol, market_type, exchange)
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

    logger.info("%s %s %s: fetching %s → %s", exchange, symbol, market_type, start_dt.date(), now_utc.date())
    total = await fetch_and_store(symbol, market_type, exchange, start_dt, now_utc)
    logger.info("%s %s %s: backfill complete, %d total rows inserted", exchange, symbol, market_type, total)


async def live_update(exchanges: List[str]):
    """Run incremental updates every 60 seconds for all targets on given exchanges."""
    logger.info("Starting live update loop (60s interval) for: %s", exchanges)
    while True:
        for ex in exchanges:
            targets = get_targets(ex)
            for target in targets:
                try:
                    await backfill_target(target)
                except Exception as e:
                    logger.error("Error updating %s %s %s: %s",
                                 target["exchange"], target["symbol"], target["market_type"], e)
        await asyncio.sleep(60)


def filter_targets(targets: List[dict], symbol_filter: Optional[str] = None) -> List[dict]:
    """Filter targets by symbol name if specified."""
    if symbol_filter is None:
        return targets
    return [t for t in targets if t["symbol"] == symbol_filter]


async def main(mode: str, exchanges: List[str], days: Optional[int], symbol: Optional[str]):
    try:
        if mode == "backfill":
            for ex in exchanges:
                targets = filter_targets(get_targets(ex), symbol)
                if not targets:
                    logger.warning("No targets found for exchange=%s symbol=%s", ex, symbol)
                    continue
                logger.info("Starting backfill for %s: %d targets", ex, len(targets))
                for t in targets:
                    try:
                        await backfill_target(t, days)
                    except Exception as e:
                        logger.error("Error backfilling %s %s %s: %s",
                                     t["exchange"], t["symbol"], t["market_type"], e)
        elif mode == "live":
            await live_update(exchanges)
        else:
            logger.error("Unknown mode: %s", mode)
    finally:
        await close_all()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Unified Multi-Exchange OHLCV Crawler")
    parser.add_argument("--mode", choices=["backfill", "live"], default="backfill")
    parser.add_argument("--exchange", default="all",
                        help="Exchange: binance, okx, bybit, or all (default: all)")
    parser.add_argument("--days", type=int, default=None,
                        help="Backfill only last N days (quick test)")
    parser.add_argument("--symbol", type=str, default=None,
                        help="Only crawl this specific symbol (e.g. BTCUSDT, BTC-USDT)")
    args = parser.parse_args()

    if args.exchange == "all":
        exchange_list = ["binance", "okx", "bybit"]
    else:
        exchange_list = [args.exchange]

    asyncio.run(main(args.mode, exchange_list, args.days, args.symbol))

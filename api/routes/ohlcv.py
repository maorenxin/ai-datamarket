"""
OHLCV data query routes — multi-exchange support (Binance, OKX, Bybit).

Each exchange has its own DuckDB file. Single-exchange queries hit one file;
/coverage and /symbols aggregate across all platform files.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from api.db.duckdb_client import get_con, get_all_cons
from config.symbols import user_symbol_to_db, db_symbol_to_user, TOP_100_BASES

logger = logging.getLogger(__name__)
router = APIRouter()

TZ_OFFSET = timezone(timedelta(hours=8))  # UTC+8

SUPPORTED_EXCHANGES = ["binance", "okx", "bybit"]

INTERVAL_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360,
    "12h": 720, "1d": 1440,
}  # type: Dict[str, int]


def parse_end_time(end_time_str, interval):
    # type: (Optional[str], str) -> datetime
    """
    Parse end_time string (UTC+8 input) and floor to interval boundary.
    Returns UTC-naive datetime.
    """
    minutes = INTERVAL_MINUTES[interval]
    if end_time_str is None:
        now_utc8 = datetime.now(tz=TZ_OFFSET)
    else:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(end_time_str, fmt)
                if fmt == "%Y-%m-%d":
                    parsed = parsed.replace(hour=8, minute=0, second=0)
                now_utc8 = parsed.replace(tzinfo=TZ_OFFSET)
                break
            except ValueError:
                continue
        else:
            raise HTTPException(status_code=400, detail="Invalid end_time format: {!r}".format(end_time_str))

    utc = now_utc8.astimezone(timezone.utc).replace(tzinfo=None)
    total_seconds = int(utc.timestamp())
    floored_seconds = (total_seconds // (minutes * 60)) * (minutes * 60)
    return datetime.utcfromtimestamp(floored_seconds)


def aggregate_ohlcv(df, interval_minutes):
    # type: (pd.DataFrame, int) -> pd.DataFrame
    """Aggregate 1m candles into the requested interval."""
    if interval_minutes == 1:
        return df
    freq = "{}min".format(interval_minutes)
    df = df.set_index("open_time").sort_index()
    agg = df.resample(freq, label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        quote_volume=("quote_volume", "sum"),
        trade_count=("trade_count", "sum"),
    ).dropna(subset=["open"])
    return agg.reset_index()


@router.get("/symbols")
async def list_symbols(
    exchange: Optional[str] = Query(None, description="Filter by exchange: binance, okx, bybit"),
):
    """List all supported symbols, optionally filtered by exchange."""
    spot = ["{}/USDT".format(b) for b in TOP_100_BASES]
    perp = ["{}USDT".format(b) for b in TOP_100_BASES]
    result = {
        "spot": spot,
        "perp": perp,
        "exchanges": SUPPORTED_EXCHANGES if exchange is None else [exchange],
        "note": "Slash format (BTC/USDT) = spot; no-slash (BTCUSDT) = perpetual futures",
    }
    return result


@router.get("/coverage")
async def data_coverage(
    exchange: Optional[str] = Query(None, description="Filter by exchange: binance, okx, bybit"),
):
    """Show available date ranges for each symbol (helps agents pick valid time ranges)."""
    coverage = []

    if exchange:
        # Single exchange — single file
        platforms = [exchange]
    else:
        platforms = SUPPORTED_EXCHANGES

    for platform in platforms:
        try:
            con = get_con(domain="crypto", platform=platform)
        except Exception:
            continue
        try:
            df = con.execute(
                """
                SELECT symbol, market_type, exchange,
                       COUNT(*) as rows,
                       MIN(open_time) as min_time,
                       MAX(open_time) as max_time
                FROM ohlcv_1m
                GROUP BY symbol, market_type, exchange
                ORDER BY exchange, symbol, market_type
                """
            ).fetchdf()
        except Exception:
            continue
        finally:
            con.close()

        for _, row in df.iterrows():
            ex = row["exchange"]
            user_sym = db_symbol_to_user(row["symbol"], row["market_type"], ex)
            min_t = row["min_time"]
            max_t = row["max_time"]
            if hasattr(min_t, "replace"):
                min_t = min_t.replace(tzinfo=timezone.utc).astimezone(TZ_OFFSET).strftime("%Y-%m-%d")
                max_t = max_t.replace(tzinfo=timezone.utc).astimezone(TZ_OFFSET).strftime("%Y-%m-%d")
            coverage.append({
                "symbol": user_sym,
                "market_type": row["market_type"],
                "exchange": ex,
                "rows": int(row["rows"]),
                "from": min_t,
                "to": max_t,
            })

    return {"coverage": coverage, "note": "Data is being continuously backfilled. Gaps may exist between 'from' and 'to'."}


@router.get("/ohlcv")
async def get_ohlcv(
    symbol: str = Query(..., description="e.g. BTC/USDT (spot) or BTCUSDT (perp)"),
    exchange: str = Query("binance", description="Exchange: binance, okx, bybit"),
    interval: str = Query("1m", description="1m/5m/15m/30m/1h/2h/4h/6h/12h/1d"),
    end_time: Optional[str] = Query(None, description="End time in UTC+8, e.g. '2025-03-03 18:04:00' or '2025-03-03'"),
    duration: int = Query(60, ge=1, le=1500, description="Number of candles to return"),
    ai_id: Optional[str] = Query(None, description="Your zCloak AI ID for token tracking"),
):
    # Validate exchange
    if exchange not in SUPPORTED_EXCHANGES:
        raise HTTPException(
            status_code=400,
            detail="Exchange '{}' not supported. Supported: {}".format(exchange, SUPPORTED_EXCHANGES),
        )

    # Validate interval
    if interval not in INTERVAL_MINUTES:
        raise HTTPException(
            status_code=400,
            detail="Invalid interval '{}'. Valid: {}".format(interval, list(INTERVAL_MINUTES.keys())),
        )

    # Convert user symbol to DB format for the given exchange
    resolved = user_symbol_to_db(symbol, exchange)
    if resolved is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid symbol format: '{}'. Use BTC/USDT for spot or BTCUSDT for perp.".format(symbol),
        )

    db_symbol = resolved["symbol"]
    market_type = resolved["market_type"]
    interval_minutes = INTERVAL_MINUTES[interval]

    # Compute time range (all in UTC, no tzinfo)
    end_dt = parse_end_time(end_time, interval)
    start_dt = end_dt - timedelta(minutes=interval_minutes * duration)

    # Query 1m data from the exchange-specific DuckDB file
    con = get_con(domain="crypto", platform=exchange)
    try:
        rows = con.execute(
            """
            SELECT open_time, open, high, low, close, volume, quote_volume, trade_count
            FROM ohlcv_1m
            WHERE symbol=? AND market_type=? AND exchange=?
              AND open_time >= ? AND open_time < ?
            ORDER BY open_time
            """,
            [db_symbol, market_type, exchange, start_dt, end_dt],
        ).fetchdf()
    except Exception as e:
        logger.error("DuckDB query failed: %s", e)
        raise HTTPException(status_code=500, detail="Database query failed")
    finally:
        con.close()

    if rows.empty:
        # Query available date range to help the agent adjust
        try:
            con2 = get_con(domain="crypto", platform=exchange)
            range_row = con2.execute(
                "SELECT MIN(open_time), MAX(open_time) FROM ohlcv_1m WHERE symbol=? AND market_type=? AND exchange=?",
                [db_symbol, market_type, exchange],
            ).fetchone()
            con2.close()
        except Exception:
            range_row = None

        if range_row and range_row[0]:
            min_t = range_row[0].replace(tzinfo=timezone.utc).astimezone(TZ_OFFSET).strftime("%Y-%m-%d")
            max_t = range_row[1].replace(tzinfo=timezone.utc).astimezone(TZ_OFFSET).strftime("%Y-%m-%d")
            detail = (
                "No data for {} ({}) in requested range. "
                "Available data: {} to {}. "
                "Data is being backfilled — try a date within this range, or retry later."
            ).format(symbol, exchange, min_t, max_t)
        else:
            detail = "No data found for {} on {}. Data may still be loading.".format(symbol, exchange)
        raise HTTPException(status_code=404, detail=detail)

    # Aggregate to requested interval
    df = aggregate_ohlcv(rows, interval_minutes)

    # Keep only last `duration` candles
    df = df.tail(duration)

    tokens_used = len(df)

    # Token tracking — only for paid data categories
    from config.pricing import is_free_category

    data_category = "crypto_ohlcv"

    if not is_free_category(data_category):
        if not ai_id:
            raise HTTPException(
                status_code=401,
                detail="ai_id is required for paid data categories. Get one free at https://id.zcloak.ai",
            )

    # Format response — times in UTC+8
    data = []
    for _, row in df.iterrows():
        t = row["open_time"]
        if hasattr(t, "to_pydatetime"):
            t = t.to_pydatetime()
        utc_aware = t.replace(tzinfo=timezone.utc)
        local_time = utc_aware.astimezone(TZ_OFFSET).isoformat()

        data.append({
            "time": local_time,
            "open": round(row["open"], 8),
            "high": round(row["high"], 8),
            "low": round(row["low"], 8),
            "close": round(row["close"], 8),
            "volume": round(row["volume"], 8),
        })

    response = {
        "symbol": symbol,
        "exchange": exchange,
        "interval": interval,
        "data": data,
    }
    if not is_free_category(data_category):
        response["tokens_used"] = tokens_used

    return response

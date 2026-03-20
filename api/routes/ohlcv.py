"""
OHLCV data query routes.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from api.db.duckdb_client import get_con, get_write_con

logger = logging.getLogger(__name__)
router = APIRouter()

TZ_OFFSET = timezone(timedelta(hours=8))  # UTC+8

SUPPORTED_SYMBOLS = {
    # spot (slash format → DB symbol)
    "BTC/USDT": ("BTCUSDT", "spot"),
    "ETH/USDT": ("ETHUSDT", "spot"),
    "SOL/USDT": ("SOLUSDT", "spot"),
    # perp (no slash)
    "BTCUSDT": ("BTCUSDT", "perp"),
    "ETHUSDT": ("ETHUSDT", "perp"),
    "SOLUSDT": ("SOLUSDT", "perp"),
}

INTERVAL_MINUTES: dict[str, int] = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360,
    "12h": 720, "1d": 1440,
}


def parse_end_time(end_time_str: Optional[str], interval: str) -> datetime:
    """
    Parse end_time string (UTC+8 input) and floor to interval boundary.
    Returns UTC-naive datetime.
    """
    minutes = INTERVAL_MINUTES[interval]
    if end_time_str is None:
        # Default: current time, floored to interval boundary
        now_utc8 = datetime.now(tz=TZ_OFFSET)
    else:
        # Try parsing with time, then date-only
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(end_time_str, fmt)
                if fmt == "%Y-%m-%d":
                    # Date-only → 08:00:00 UTC+8 = 00:00:00 UTC
                    parsed = parsed.replace(hour=8, minute=0, second=0)
                now_utc8 = parsed.replace(tzinfo=TZ_OFFSET)
                break
            except ValueError:
                continue
        else:
            raise HTTPException(status_code=400, detail=f"Invalid end_time format: {end_time_str!r}")

    # Convert to UTC, floor to interval boundary
    utc = now_utc8.astimezone(timezone.utc).replace(tzinfo=None)
    total_seconds = int(utc.timestamp())
    floored_seconds = (total_seconds // (minutes * 60)) * (minutes * 60)
    return datetime.utcfromtimestamp(floored_seconds)


def aggregate_ohlcv(df: pd.DataFrame, interval_minutes: int) -> pd.DataFrame:
    """Aggregate 1m candles into the requested interval."""
    if interval_minutes == 1:
        return df
    freq = f"{interval_minutes}min"
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


def count_tokens(ai_id: str) -> int:
    """Return total tokens used by this AI ID."""
    con = get_write_con()
    try:
        row = con.execute(
            "SELECT total_tokens FROM ai_id_usage WHERE ai_id=?", [ai_id]
        ).fetchone()
        return row[0] if row else 0
    finally:
        con.close()


def add_tokens(ai_id: str, tokens: int):
    """Increment token usage for an AI ID."""
    con = get_write_con()
    try:
        con.execute(
            """
            INSERT INTO ai_id_usage (ai_id, total_tokens) VALUES (?, ?)
            ON CONFLICT (ai_id) DO UPDATE SET total_tokens = total_tokens + excluded.total_tokens
            """,
            [ai_id, tokens],
        )
    finally:
        con.close()


@router.get("/symbols")
async def list_symbols():
    """List all supported symbols."""
    return {
        "symbols": list(SUPPORTED_SYMBOLS.keys()),
        "note": "Slash format (BTC/USDT) = spot; no-slash (BTCUSDT) = perpetual futures",
    }


@router.get("/ohlcv")
async def get_ohlcv(
    symbol: str = Query(..., description="e.g. BTC/USDT (spot) or BTCUSDT (perp)"),
    exchange: str = Query("binance", description="Exchange (currently only binance)"),
    interval: str = Query("1m", description="1m/5m/15m/30m/1h/2h/4h/6h/12h/1d"),
    end_time: Optional[str] = Query(None, description="End time in UTC+8, e.g. '2025-03-03 18:04:00' or '2025-03-03'"),
    duration: int = Query(60, ge=1, le=1500, description="Number of candles to return"),
    ai_id: Optional[str] = Query(None, description="Your zCloak AI ID for token tracking"),
):
    # Validate symbol
    if symbol not in SUPPORTED_SYMBOLS:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Symbol '{symbol}' is not yet supported. Stay tuned. "
                f"Supported: {list(SUPPORTED_SYMBOLS.keys())}"
            ),
        )

    # Validate interval
    if interval not in INTERVAL_MINUTES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid interval '{interval}'. Valid: {list(INTERVAL_MINUTES.keys())}",
        )

    if exchange != "binance":
        raise HTTPException(status_code=400, detail=f"Exchange '{exchange}' not yet supported.")

    db_symbol, market_type = SUPPORTED_SYMBOLS[symbol]
    interval_minutes = INTERVAL_MINUTES[interval]

    # Compute time range (all in UTC, no tzinfo)
    end_dt = parse_end_time(end_time, interval)
    start_dt = end_dt - timedelta(minutes=interval_minutes * duration)

    # Query 1m data from DuckDB
    con = get_con()
    try:
        rows = con.execute(
            """
            SELECT open_time, open, high, low, close, volume, quote_volume, trade_count
            FROM ohlcv_1m
            WHERE symbol=? AND market_type=? AND exchange='binance'
              AND open_time >= ? AND open_time < ?
            ORDER BY open_time
            """,
            [db_symbol, market_type, start_dt, end_dt],
        ).fetchdf()
    except Exception as e:
        logger.error("DuckDB query failed: %s", e)
        raise HTTPException(status_code=500, detail="Database query failed")
    finally:
        con.close()

    if rows.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No data found for {symbol} {interval} in the requested time range.",
        )

    # Aggregate to requested interval
    df = aggregate_ohlcv(rows, interval_minutes)

    # Keep only last `duration` candles
    df = df.tail(duration)

    tokens_used = len(df)

    # Token tracking
    if ai_id:
        add_tokens(ai_id, tokens_used)
        from config.pricing import FREE_TOKEN_QUOTA
        total_used = count_tokens(ai_id)
        tokens_remaining = max(0, FREE_TOKEN_QUOTA - total_used)
    else:
        tokens_remaining = None

    # Format response — times in UTC+8
    data = []
    for _, row in df.iterrows():
        # open_time is UTC, convert to UTC+8 for response
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
        "tokens_used": tokens_used,
    }
    if tokens_remaining is not None:
        response["tokens_remaining_free"] = tokens_remaining

    return response

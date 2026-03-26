"""
Currency / forex data routes — powered by Yahoo Finance direct API + yFinance fallback.

GET /currency/rate      — exchange rate for a currency pair
GET /currency/history   — historical forex data
GET /currency/available — list common forex pairs
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.providers.client import get_yf_ticker, df_to_records, yahoo_chart, yahoo_quote

router = APIRouter(tags=["currency"])

FOREX_PAIRS = {
    "EURUSD=X": "EUR/USD",
    "GBPUSD=X": "GBP/USD",
    "USDJPY=X": "USD/JPY",
    "USDCNY=X": "USD/CNY",
    "USDCHF=X": "USD/CHF",
    "AUDUSD=X": "AUD/USD",
    "USDCAD=X": "USD/CAD",
    "NZDUSD=X": "NZD/USD",
    "USDHKD=X": "USD/HKD",
    "USDSGD=X": "USD/SGD",
    "DX-Y.NYB": "US Dollar Index (DXY)",
}


@router.get("/currency/available")
async def currency_available():
    """List common forex pairs."""
    return {"pairs": FOREX_PAIRS, "note": "Use Yahoo Finance forex format: EURUSD=X, GBPUSD=X, etc."}


@router.get("/currency/rate")
async def currency_rate(
    symbol: str = Query("EURUSD=X", description="Forex pair, e.g. EURUSD=X, USDJPY=X"),
):
    result = await yahoo_quote(symbol)
    if result and result.get("regularMarketPrice"):
        return result
    try:
        result = await asyncio.to_thread(_get_rate_yf, symbol)
        if result:
            return result
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="No rate for '{}'".format(symbol))


def _get_rate_yf(symbol):
    # type: (str,) -> dict
    t = get_yf_ticker(symbol)
    info = t.info
    if not info or info.get("regularMarketPrice") is None:
        return None
    keys = [
        "symbol", "shortName", "regularMarketPrice", "regularMarketChange",
        "regularMarketChangePercent", "regularMarketDayHigh", "regularMarketDayLow",
        "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
    ]
    return {k: info.get(k) for k in keys}


@router.get("/currency/history")
async def currency_history(
    symbol: str = Query("EURUSD=X", description="Forex pair, e.g. EURUSD=X"),
    period: str = Query("1mo", description="1d,5d,1mo,3mo,6mo,1y,2y,5y,10y,ytd,max"),
    interval: str = Query("1d", description="1d,5d,1wk,1mo,3mo"),
):
    records = await yahoo_chart(symbol, period=period, interval=interval)
    if records:
        return {"symbol": symbol, "name": FOREX_PAIRS.get(symbol, symbol), "period": period, "interval": interval, "data": records}
    try:
        records = await asyncio.to_thread(_get_fx_history_yf, symbol, period, interval)
        if records:
            return {"symbol": symbol, "name": FOREX_PAIRS.get(symbol, symbol), "period": period, "interval": interval, "data": records}
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="No history for '{}'".format(symbol))


def _get_fx_history_yf(symbol, period, interval):
    # type: (str, str, str) -> list
    t = get_yf_ticker(symbol)
    df = t.history(period=period, interval=interval)
    return df_to_records(df)

"""
Index data routes — powered by Yahoo Finance direct API + yFinance fallback.

GET /index/quote     — real-time index quote
GET /index/history   — historical index data
GET /index/available — list supported indices
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.providers.client import get_yf_ticker, df_to_records, yahoo_chart, yahoo_quote

router = APIRouter(tags=["index"])

INDICES = {
    "^GSPC": "S&P 500",
    "^DJI": "Dow Jones Industrial Average",
    "^IXIC": "NASDAQ Composite",
    "^RUT": "Russell 2000",
    "^VIX": "CBOE Volatility Index",
    "^FTSE": "FTSE 100",
    "^N225": "Nikkei 225",
    "^HSI": "Hang Seng Index",
    "^GDAXI": "DAX",
    "000001.SS": "Shanghai Composite",
}


@router.get("/index/available")
async def index_available():
    """List supported market indices."""
    return {"indices": INDICES}


@router.get("/index/quote")
async def index_quote(
    symbol: str = Query("^GSPC", description="Index symbol, e.g. ^GSPC, ^DJI, ^VIX"),
):
    result = await yahoo_quote(symbol)
    if result and result.get("regularMarketPrice"):
        return result
    try:
        result = await asyncio.to_thread(_get_index_quote_yf, symbol)
        if result:
            return result
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="No quote for index '{}'".format(symbol))


def _get_index_quote_yf(symbol):
    # type: (str,) -> dict
    t = get_yf_ticker(symbol)
    info = t.info
    if not info or info.get("regularMarketPrice") is None:
        return None
    keys = [
        "symbol", "shortName", "regularMarketPrice", "regularMarketChange",
        "regularMarketChangePercent", "regularMarketVolume",
        "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
    ]
    return {k: info.get(k) for k in keys}


@router.get("/index/history")
async def index_history(
    symbol: str = Query("^GSPC", description="Index symbol, e.g. ^GSPC"),
    period: str = Query("1mo", description="1d,5d,1mo,3mo,6mo,1y,2y,5y,10y,ytd,max"),
    interval: str = Query("1d", description="1d,5d,1wk,1mo,3mo"),
):
    records = await yahoo_chart(symbol, period=period, interval=interval)
    if records:
        return {"symbol": symbol, "name": INDICES.get(symbol, symbol), "period": period, "interval": interval, "data": records}
    try:
        records = await asyncio.to_thread(_get_index_history_yf, symbol, period, interval)
        if records:
            return {"symbol": symbol, "name": INDICES.get(symbol, symbol), "period": period, "interval": interval, "data": records}
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="No history for index '{}'".format(symbol))


def _get_index_history_yf(symbol, period, interval):
    # type: (str, str, str) -> list
    t = get_yf_ticker(symbol)
    df = t.history(period=period, interval=interval)
    return df_to_records(df)

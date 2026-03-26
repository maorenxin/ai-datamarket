"""
Equity (stock) data routes — powered by Yahoo Finance direct API + yFinance fallback.

GET /equity/quote       — real-time quote
GET /equity/history     — historical OHLCV
GET /equity/info        — company profile
GET /equity/available   — list example tickers
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.providers.client import get_yf_ticker, df_to_records, yahoo_chart, yahoo_quote

router = APIRouter(tags=["equity"])


@router.get("/equity/available")
async def equity_available():
    """List example equity tickers."""
    return {
        "examples": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "JPM", "V", "WMT"],
        "note": "Any valid Yahoo Finance ticker is supported (US and international stocks).",
    }


@router.get("/equity/quote")
async def equity_quote(
    symbol: str = Query(..., description="Ticker symbol, e.g. AAPL"),
):
    # Direct Yahoo API (reliable, no rate-limit issues)
    result = await yahoo_quote(symbol)
    if result and result.get("regularMarketPrice"):
        return result
    # Fallback to yfinance
    try:
        result = await asyncio.to_thread(_get_quote_yf, symbol)
        if result:
            return result
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="No quote data for '{}'".format(symbol))


def _get_quote_yf(symbol):
    # type: (str,) -> dict
    t = get_yf_ticker(symbol)
    info = t.info
    if not info or info.get("regularMarketPrice") is None:
        return None
    keys = [
        "symbol", "shortName", "regularMarketPrice", "regularMarketChange",
        "regularMarketChangePercent", "regularMarketVolume", "marketCap",
        "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "trailingPE", "forwardPE",
        "dividendYield", "currency",
    ]
    return {k: info.get(k) for k in keys}


@router.get("/equity/history")
async def equity_history(
    symbol: str = Query(..., description="Ticker symbol, e.g. AAPL"),
    period: str = Query("1mo", description="1d,5d,1mo,3mo,6mo,1y,2y,5y,10y,ytd,max"),
    interval: str = Query("1d", description="1m,2m,5m,15m,30m,60m,90m,1h,1d,5d,1wk,1mo,3mo"),
):
    # Direct Yahoo API first
    records = await yahoo_chart(symbol, period=period, interval=interval)
    if records:
        return {"symbol": symbol, "period": period, "interval": interval, "data": records}
    # Fallback to yfinance
    try:
        records = await asyncio.to_thread(_get_history_yf, symbol, period, interval)
        if records:
            return {"symbol": symbol, "period": period, "interval": interval, "data": records}
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="No history for '{}'".format(symbol))


def _get_history_yf(symbol, period, interval):
    # type: (str, str, str) -> list
    t = get_yf_ticker(symbol)
    df = t.history(period=period, interval=interval)
    return df_to_records(df)


@router.get("/equity/info")
async def equity_info(
    symbol: str = Query(..., description="Ticker symbol, e.g. AAPL"),
):
    try:
        result = await asyncio.to_thread(_get_info_yf, symbol)
        if result:
            return result
    except Exception:
        pass
    # Fallback: return basic info from quote
    q = await yahoo_quote(symbol)
    if q:
        return q
    raise HTTPException(status_code=404, detail="No info for '{}'".format(symbol))


def _get_info_yf(symbol):
    # type: (str,) -> dict
    t = get_yf_ticker(symbol)
    info = t.info
    if not info or not info.get("shortName"):
        return None
    keys = [
        "symbol", "shortName", "longName", "sector", "industry", "country",
        "marketCap", "enterpriseValue", "trailingPE", "forwardPE",
        "profitMargins", "revenueGrowth", "returnOnEquity",
        "totalRevenue", "totalDebt", "totalCash", "fullTimeEmployees",
        "website", "currency",
    ]
    return {k: info.get(k) for k in keys}

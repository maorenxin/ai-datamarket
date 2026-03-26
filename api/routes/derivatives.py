"""
Derivatives (options) data routes — Yahoo Finance direct API + yFinance fallback.

GET /derivatives/chain     — options chain for a ticker
GET /derivatives/expirations — available expiration dates
GET /derivatives/available — list example tickers
"""
import asyncio
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.providers.client import get_yf_ticker, df_to_records, yahoo_options

router = APIRouter(tags=["derivatives"])


@router.get("/derivatives/available")
async def derivatives_available():
    """List example tickers with options data."""
    return {
        "examples": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "SPY", "QQQ", "IWM"],
        "note": "Any US equity/ETF ticker with listed options is supported.",
    }


@router.get("/derivatives/expirations")
async def derivatives_expirations(
    symbol: str = Query(..., description="Ticker symbol, e.g. AAPL"),
):
    # Direct Yahoo API
    result = await yahoo_options(symbol)
    if result:
        exp_timestamps = result.get("expirationDates", [])
        dates = [datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") for ts in exp_timestamps]
        if dates:
            return {"symbol": symbol, "expirations": dates}
    # Fallback to yfinance
    try:
        dates = await asyncio.to_thread(_get_expirations, symbol)
        if dates:
            return {"symbol": symbol, "expirations": dates}
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="No options data for '{}'".format(symbol))


def _get_expirations(symbol):
    # type: (str,) -> list
    t = get_yf_ticker(symbol)
    return list(t.options) if t.options else []


@router.get("/derivatives/chain")
async def derivatives_chain(
    symbol: str = Query(..., description="Ticker symbol, e.g. AAPL"),
    expiration: Optional[str] = Query(None, description="Expiration date YYYY-MM-DD (default: nearest)"),
    option_type: str = Query("calls", description="calls or puts"),
):
    if option_type not in ("calls", "puts"):
        raise HTTPException(status_code=400, detail="option_type must be 'calls' or 'puts'")
    # Direct Yahoo API
    result = await yahoo_options(symbol, expiration)
    if result:
        options_list = result.get("options", [])
        if options_list:
            raw = options_list[0].get(option_type, [])
            data = []
            for opt in raw:
                data.append({
                    "strike": opt.get("strike"),
                    "lastPrice": opt.get("lastPrice"),
                    "bid": opt.get("bid"),
                    "ask": opt.get("ask"),
                    "volume": opt.get("volume"),
                    "openInterest": opt.get("openInterest"),
                    "impliedVolatility": opt.get("impliedVolatility"),
                    "inTheMoney": opt.get("inTheMoney"),
                })
            exp_used = expiration or (datetime.utcfromtimestamp(options_list[0].get("expirationDate", 0)).strftime("%Y-%m-%d") if options_list[0].get("expirationDate") else "nearest")
            return {"symbol": symbol, "expiration": exp_used, "type": option_type, "data": data}
    # Fallback to yfinance
    try:
        result = await asyncio.to_thread(_get_chain, symbol, expiration, option_type)
        if result:
            return {"symbol": symbol, **result}
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="No options for '{}'".format(symbol))


def _get_chain(symbol, expiration, option_type):
    # type: (str, Optional[str], str) -> dict
    t = get_yf_ticker(symbol)
    if not t.options:
        return None
    exp = expiration or t.options[0]
    chain = t.option_chain(exp)
    if option_type == "calls":
        df = chain.calls
    elif option_type == "puts":
        df = chain.puts
    else:
        df = chain.calls
    records = df_to_records(df)
    return {"expiration": exp, "type": option_type, "data": records}

"""
Shared utilities for provider routes (yFinance, FRED, etc.).

- Cached httpx.AsyncClient for external HTTP calls
- yFinance ticker cache (sync lib, use with asyncio.to_thread)
- DataFrame-to-records helper
"""
import os
from typing import Any, Dict, List, Optional

import httpx

# ---------------------------------------------------------------------------
# httpx async client (reused across provider routes)
# ---------------------------------------------------------------------------
_http_client = None  # type: Optional[httpx.AsyncClient]


def get_http_client():
    # type: () -> httpx.AsyncClient
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    return _http_client


# ---------------------------------------------------------------------------
# FRED API key
# ---------------------------------------------------------------------------
def get_fred_client():
    """Return a fredapi.Fred instance (sync)."""
    from fredapi import Fred
    key = os.environ.get("FRED_API_KEY", "a4ddc6d7b3d49d6b05affc7bb692466c")
    return Fred(api_key=key)


# ---------------------------------------------------------------------------
# yFinance ticker cache
# ---------------------------------------------------------------------------
_yf_cache = {}  # type: Dict[str, Any]


def get_yf_ticker(symbol):
    # type: (str) -> Any
    """Get or create a cached yfinance.Ticker (sync)."""
    import yfinance as yf
    if symbol not in _yf_cache:
        _yf_cache[symbol] = yf.Ticker(symbol)
    return _yf_cache[symbol]


# ---------------------------------------------------------------------------
# Direct Yahoo Finance API (bypasses yfinance library for reliability)
# ---------------------------------------------------------------------------
_YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

# Yahoo crumb + cookies for authenticated endpoints (options, etc.)
_yf_crumb = None  # type: Optional[str]
_yf_cookies = None  # type: Optional[dict]


async def _ensure_yahoo_crumb():
    # type: () -> tuple
    """Get Yahoo Finance crumb and cookies (needed for v7 API)."""
    global _yf_crumb, _yf_cookies
    if _yf_crumb and _yf_cookies:
        return _yf_crumb, _yf_cookies
    import httpx as _httpx
    sync = _httpx.Client(headers=_YF_HEADERS, follow_redirects=True, timeout=15)
    try:
        sync.get("https://fc.yahoo.com")
        r = sync.get("https://query2.finance.yahoo.com/v1/test/getcrumb")
        if r.status_code == 200 and r.text.strip():
            _yf_crumb = r.text.strip()
            _yf_cookies = dict(sync.cookies)
    finally:
        sync.close()
    return _yf_crumb, _yf_cookies


async def yahoo_options(symbol, expiration=None):
    # type: (str, Optional[str]) -> Optional[Dict[str, Any]]
    """Fetch options data from Yahoo Finance v7 API directly."""
    crumb, cookies = await _ensure_yahoo_crumb()
    if not crumb:
        return None
    client = get_http_client()
    url = "https://query1.finance.yahoo.com/v7/finance/options/{symbol}?crumb={crumb}".format(
        symbol=symbol, crumb=crumb,
    )
    if expiration:
        # Convert YYYY-MM-DD to unix timestamp
        from datetime import datetime
        try:
            dt = datetime.strptime(expiration, "%Y-%m-%d")
            url += "&date={}".format(int(dt.timestamp()))
        except ValueError:
            pass
    resp = await client.get(url, headers=_YF_HEADERS, cookies=cookies, timeout=15.0)
    if resp.status_code != 200:
        return None
    data = resp.json()
    result = data.get("optionChain", {}).get("result", [])
    if not result:
        return None
    return result[0]


async def yahoo_chart(symbol, period="1mo", interval="1d"):
    # type: (str, str, str) -> List[Dict[str, Any]]
    """Fetch OHLCV from Yahoo Finance v8 chart API directly."""
    client = get_http_client()
    url = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range={period}".format(
        symbol=symbol, interval=interval, period=period,
    )
    resp = await client.get(url, headers=_YF_HEADERS, timeout=15.0)
    if resp.status_code != 200:
        return []
    data = resp.json()
    result = data.get("chart", {}).get("result", [])
    if not result:
        return []
    r = result[0]
    timestamps = r.get("timestamp", [])
    quote = r.get("indicators", {}).get("quote", [{}])[0]
    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    closes = quote.get("close", [])
    volumes = quote.get("volume", [])
    records = []
    from datetime import datetime, timezone
    for i, ts in enumerate(timestamps):
        if i < len(closes) and closes[i] is not None:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            records.append({
                "date": dt.isoformat(),
                "Open": opens[i] if i < len(opens) else None,
                "High": highs[i] if i < len(highs) else None,
                "Low": lows[i] if i < len(lows) else None,
                "Close": closes[i],
                "Volume": volumes[i] if i < len(volumes) else None,
            })
    return records


async def yahoo_quote(symbol):
    # type: (str,) -> Optional[Dict[str, Any]]
    """Fetch quote summary from Yahoo Finance v6 API directly."""
    client = get_http_client()
    # Use v8 chart with range=1d to get current price
    url = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d".format(symbol=symbol)
    resp = await client.get(url, headers=_YF_HEADERS, timeout=15.0)
    if resp.status_code != 200:
        return None
    data = resp.json()
    result = data.get("chart", {}).get("result", [])
    if not result:
        return None
    meta = result[0].get("meta", {})
    return {
        "symbol": meta.get("symbol", symbol),
        "shortName": meta.get("shortName"),
        "regularMarketPrice": meta.get("regularMarketPrice"),
        "previousClose": meta.get("previousClose") or meta.get("chartPreviousClose"),
        "currency": meta.get("currency"),
        "exchangeName": meta.get("exchangeName"),
        "instrumentType": meta.get("instrumentType"),
    }


# ---------------------------------------------------------------------------
# DataFrame helpers
# ---------------------------------------------------------------------------
def df_to_records(df):
    # type: (Any) -> List[Dict[str, Any]]
    """Convert a pandas DataFrame to a list of dicts, handling NaN and Timestamps."""
    import pandas as pd
    if df is None or df.empty:
        return []
    # Reset index if it's a DatetimeIndex
    if isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index()
    records = []
    for _, row in df.iterrows():
        rec = {}
        for col in df.columns:
            val = row[col]
            if pd.isna(val):
                rec[col] = None
            elif hasattr(val, "isoformat"):
                rec[col] = val.isoformat()
            elif hasattr(val, "item"):
                rec[col] = val.item()
            else:
                rec[col] = val
            # Rename 'Date' or 'Datetime' to 'date'
            if col in ("Date", "Datetime"):
                rec["date"] = rec.pop(col)
        records.append(rec)
    return records

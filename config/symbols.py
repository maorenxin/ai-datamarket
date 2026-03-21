"""
Unified symbol configuration for multi-exchange crawling.

Provides Top 100 crypto bases, per-exchange target generation,
and symbol format conversion utilities.
"""
from typing import Dict, List, Optional

# Top 100 base assets by market cap (approximate ordering)
TOP_100_BASES = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "TRX", "LINK",
    "DOT", "MATIC", "SHIB", "LTC", "BCH", "UNI", "ATOM", "XLM", "NEAR", "FIL",
    "APT", "ARB", "OP", "IMX", "INJ", "STX", "SUI", "SEI", "TIA", "JUP",
    "RENDER", "FET", "GRT", "ALGO", "VET", "SAND", "MANA", "AXS", "THETA", "FTM",
    "EOS", "AAVE", "MKR", "SNX", "CRV", "LDO", "RUNE", "DYDX", "COMP", "ENS",
    "1INCH", "SUSHI", "YFI", "BAL", "ZRX", "LRC", "KNC", "CELO", "FLOW", "ROSE",
    "KAVA", "IOTA", "ZIL", "ENJ", "CHZ", "GALA", "APE", "GMT", "BLUR", "WOO",
    "MASK", "PENDLE", "SSV", "CYBER", "ARKM", "ORDI", "WLD", "BONK", "PEPE", "FLOKI",
    "CFX", "ACH", "HOOK", "EDU", "MEME", "PYTH", "JTO", "STRK", "DYM", "ALT",
    "PIXEL", "PORTAL", "AEVO", "ENA", "W", "TNSR", "SAGA", "OMNI", "REZ", "IO",
]

# Historical start dates for major assets (when their USDT pair launched on Binance).
# Assets not listed here default to 2023-01-01.
START_DATES = {
    "BTC":   "2017-08-17",
    "ETH":   "2017-08-17",
    "BNB":   "2017-11-06",
    "XRP":   "2018-01-05",
    "LTC":   "2017-12-13",
    "BCH":   "2017-12-20",
    "ADA":   "2018-04-17",
    "DOT":   "2020-08-18",
    "LINK":  "2019-01-16",
    "DOGE":  "2019-07-05",
    "SOL":   "2020-08-11",
    "AVAX":  "2020-09-22",
    "MATIC": "2019-04-26",
    "TRX":   "2018-06-12",
    "ATOM":  "2019-04-29",
    "UNI":   "2020-09-17",
    "FIL":   "2020-10-15",
    "NEAR":  "2020-10-15",
    "ALGO":  "2019-08-12",
    "XLM":   "2018-06-04",
}

DEFAULT_START = "2023-01-01"

# Perp start dates (when the USDT perpetual launched on Binance).
# Falls back to the spot start date, then DEFAULT_START.
PERP_START_DATES = {
    "BTC":   "2019-09-25",
    "ETH":   "2019-11-27",
    "SOL":   "2021-09-29",
    "BNB":   "2020-02-10",
    "XRP":   "2020-01-31",
    "DOGE":  "2021-07-19",
    "ADA":   "2020-10-09",
    "AVAX":  "2021-09-29",
    "TRX":   "2021-11-03",
    "LINK":  "2020-01-14",
    "DOT":   "2020-08-31",
    "MATIC": "2021-04-22",
    "LTC":   "2020-01-09",
}


def _get_start(base: str, market_type: str) -> str:
    """Get the start date for a base asset + market type."""
    if market_type == "perp":
        return PERP_START_DATES.get(base, START_DATES.get(base, DEFAULT_START))
    return START_DATES.get(base, DEFAULT_START)


# ---------------------------------------------------------------------------
# Per-exchange target generators
# ---------------------------------------------------------------------------

def binance_targets() -> List[Dict[str, str]]:
    """Generate Binance crawl targets (spot + perp) for all Top 100 bases."""
    targets = []
    for base in TOP_100_BASES:
        sym = "{}USDT".format(base)
        targets.append({
            "symbol": sym,
            "market_type": "spot",
            "start": _get_start(base, "spot"),
            "exchange": "binance",
        })
        targets.append({
            "symbol": sym,
            "market_type": "perp",
            "start": _get_start(base, "perp"),
            "exchange": "binance",
        })
    return targets


def okx_targets() -> List[Dict[str, str]]:
    """Generate OKX crawl targets (spot + perp) for all Top 100 bases."""
    targets = []
    for base in TOP_100_BASES:
        targets.append({
            "symbol": "{}-USDT".format(base),
            "market_type": "spot",
            "start": _get_start(base, "spot"),
            "exchange": "okx",
        })
        targets.append({
            "symbol": "{}-USDT-SWAP".format(base),
            "market_type": "perp",
            "start": _get_start(base, "perp"),
            "exchange": "okx",
        })
    return targets


def bybit_targets() -> List[Dict[str, str]]:
    """Generate Bybit crawl targets (spot + perp) for all Top 100 bases."""
    targets = []
    for base in TOP_100_BASES:
        sym = "{}USDT".format(base)
        targets.append({
            "symbol": sym,
            "market_type": "spot",
            "start": _get_start(base, "spot"),
            "exchange": "bybit",
        })
        targets.append({
            "symbol": sym,
            "market_type": "perp",
            "start": _get_start(base, "perp"),
            "exchange": "bybit",
        })
    return targets


def get_targets(exchange: str) -> List[Dict[str, str]]:
    """Return all crawl targets for an exchange."""
    if exchange == "binance":
        return binance_targets()
    elif exchange == "okx":
        return okx_targets()
    elif exchange == "bybit":
        return bybit_targets()
    else:
        raise ValueError("Unknown exchange: {}".format(exchange))


# ---------------------------------------------------------------------------
# Symbol format conversion
# ---------------------------------------------------------------------------

# Exchange-specific DB symbol formats:
#   Binance: BTCUSDT (spot & perp share the same symbol, market_type differentiates)
#   OKX:     BTC-USDT (spot), BTC-USDT-SWAP (perp)
#   Bybit:   BTCUSDT (spot & perp share the same symbol, market_type differentiates)

def user_symbol_to_db(user_symbol: str, exchange: str) -> Optional[Dict[str, str]]:
    """
    Convert user-facing symbol to DB symbol + market_type for a given exchange.

    User formats:
      - "BTC/USDT" → spot
      - "BTCUSDT"  → perp

    Returns: {"symbol": <db_symbol>, "market_type": "spot"|"perp"} or None if invalid.
    """
    if "/" in user_symbol:
        # Spot: "BTC/USDT" → base=BTC, quote=USDT
        parts = user_symbol.split("/")
        if len(parts) != 2:
            return None
        base, quote = parts
        market_type = "spot"
    else:
        # Perp: "BTCUSDT" → base=BTC (strip USDT suffix)
        if not user_symbol.endswith("USDT"):
            return None
        base = user_symbol[:-4]
        quote = "USDT"
        market_type = "perp"

    if exchange == "binance":
        return {"symbol": "{}{}".format(base, quote), "market_type": market_type}
    elif exchange == "okx":
        if market_type == "spot":
            return {"symbol": "{}-{}".format(base, quote), "market_type": market_type}
        else:
            return {"symbol": "{}-{}-SWAP".format(base, quote), "market_type": market_type}
    elif exchange == "bybit":
        return {"symbol": "{}{}".format(base, quote), "market_type": market_type}
    else:
        return None


def db_symbol_to_user(db_symbol: str, market_type: str, exchange: str) -> str:
    """Convert DB symbol back to user-facing format."""
    if exchange == "okx":
        # BTC-USDT → BTC/USDT, BTC-USDT-SWAP → BTCUSDT
        if market_type == "perp":
            base = db_symbol.split("-")[0]
            return "{}USDT".format(base)
        else:
            return db_symbol.replace("-", "/")
    else:
        # Binance/Bybit: BTCUSDT → BTC/USDT (spot) or BTCUSDT (perp)
        if market_type == "spot":
            base = db_symbol[:-4]  # strip USDT
            return "{}/USDT".format(base)
        else:
            return db_symbol

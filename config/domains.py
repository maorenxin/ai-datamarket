"""
Domain registry — central mapping of domain + platform to DuckDB file paths.

All components (API, crawlers) use this to locate the correct DB file.
Each platform gets its own .duckdb file, eliminating cross-process lock contention.
"""
from pathlib import Path
from typing import Dict

DATA_DIR = Path(__file__).parent.parent / "data"

DOMAINS = {
    "crypto": {
        "platforms": {
            "binance": DATA_DIR / "crypto" / "binance.duckdb",
            "okx": DATA_DIR / "crypto" / "okx.duckdb",
            "bybit": DATA_DIR / "crypto" / "bybit.duckdb",
        },
        "table": "ohlcv_1m",
    },
    # Future domains:
    # "us_stock": {"platforms": {"alpha_vantage": DATA_DIR / "us_stock" / "alpha_vantage.duckdb"}, "table": "ohlcv_1m"},
    # "cn_stock": {"platforms": {"tushare": DATA_DIR / "cn_stock" / "tushare.duckdb"}, "table": "ohlcv_1m"},
}

# Schema for ohlcv_1m table (shared across all domains)
OHLCV_SCHEMA = """
CREATE TABLE IF NOT EXISTS ohlcv_1m (
    symbol       VARCHAR NOT NULL,
    market_type  VARCHAR NOT NULL,
    exchange     VARCHAR NOT NULL,
    open_time    TIMESTAMP NOT NULL,
    open         DOUBLE,
    high         DOUBLE,
    low          DOUBLE,
    close        DOUBLE,
    volume       DOUBLE,
    quote_volume DOUBLE,
    trade_count  INTEGER,
    PRIMARY KEY (symbol, market_type, exchange, open_time)
)
"""


def get_db_path(domain, platform):
    # type: (str, str) -> Path
    """Return the DuckDB file path for a domain + platform."""
    d = DOMAINS.get(domain)
    if d is None:
        raise ValueError("Unknown domain: {}".format(domain))
    path = d["platforms"].get(platform)
    if path is None:
        raise ValueError("Unknown platform '{}' in domain '{}'".format(platform, domain))
    return path


def get_all_db_paths(domain):
    # type: (str,) -> Dict[str, Path]
    """Return all platform DB paths for a domain."""
    d = DOMAINS.get(domain)
    if d is None:
        raise ValueError("Unknown domain: {}".format(domain))
    return dict(d["platforms"])


def get_platforms(domain):
    # type: (str,) -> list
    """Return list of platform names for a domain."""
    d = DOMAINS.get(domain)
    if d is None:
        raise ValueError("Unknown domain: {}".format(domain))
    return list(d["platforms"].keys())

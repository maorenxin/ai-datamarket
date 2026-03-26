"""
DuckDB connection manager — domain + platform aware.

Each platform has its own .duckdb file, so connections never contend across
different crawlers or exchanges. Read connections retry on lock contention
(the crawler holds write locks briefly during INSERT batches).
"""
import time
from typing import Dict

import duckdb

from config.domains import get_db_path, get_all_db_paths, OHLCV_SCHEMA


def get_con(domain="crypto", platform="binance"):
    # type: (str, str) -> duckdb.DuckDBPyConnection
    """Return a read-only connection for a specific domain + platform."""
    path = get_db_path(domain, platform)
    for attempt in range(200):
        try:
            con = duckdb.connect(str(path), read_only=True)
            return con
        except duckdb.IOException:
            if attempt == 199:
                raise
            time.sleep(0.05 * (1 + attempt % 10))


def get_write_con(domain="crypto", platform="binance"):
    # type: (str, str) -> duckdb.DuckDBPyConnection
    """Return a write-capable connection for a specific domain + platform."""
    path = get_db_path(domain, platform)
    for attempt in range(10):
        try:
            return duckdb.connect(str(path))
        except duckdb.IOException:
            if attempt == 9:
                raise
            time.sleep(0.05 * (attempt + 1))


def get_all_cons(domain="crypto"):
    # type: (str,) -> Dict[str, duckdb.DuckDBPyConnection]
    """Return read-only connections for all platforms in a domain.
    Caller is responsible for closing all connections.
    """
    paths = get_all_db_paths(domain)
    cons = {}
    for platform, path in paths.items():
        if not path.exists():
            continue
        try:
            cons[platform] = duckdb.connect(str(path), read_only=True)
        except Exception:
            # Skip platforms whose DB doesn't exist yet
            pass
    return cons


def ensure_schema(domain="crypto", platform="binance"):
    # type: (str, str) -> None
    """Create the ohlcv_1m table if it doesn't exist."""
    path = get_db_path(domain, platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(OHLCV_SCHEMA)
    con.close()

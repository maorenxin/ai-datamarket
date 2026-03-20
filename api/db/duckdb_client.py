"""
DuckDB connection manager for the API server.
Uses a single persistent connection (DuckDB supports multiple readers, one writer).
"""
from pathlib import Path
from typing import Optional
import duckdb

DB_PATH = Path(__file__).parent.parent.parent / "data" / "market.duckdb"

_con: Optional[duckdb.DuckDBPyConnection] = None


def get_con() -> duckdb.DuckDBPyConnection:
    """Return the global DuckDB connection, creating it if needed."""
    global _con
    if _con is None:
        _con = duckdb.connect(str(DB_PATH), read_only=True)
    return _con


def get_write_con() -> duckdb.DuckDBPyConnection:
    """Return a write-capable connection (used for token tracking)."""
    return duckdb.connect(str(DB_PATH))

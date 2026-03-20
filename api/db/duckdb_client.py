"""
DuckDB connection manager for the API server.
Each call returns a fresh connection. Read-only connections retry on lock contention
(the background crawler holds write locks briefly — ~1ms per INSERT batch).
"""
import time
from pathlib import Path
import duckdb

DB_PATH = Path(__file__).parent.parent.parent / "data" / "market.duckdb"


def get_con() -> duckdb.DuckDBPyConnection:
    """Return a new read-only DuckDB connection with retry on lock contention."""
    for attempt in range(10):
        try:
            return duckdb.connect(str(DB_PATH), read_only=True)
        except duckdb.IOException:
            if attempt == 9:
                raise
            time.sleep(0.05 * (attempt + 1))  # 50ms, 100ms, 150ms...


def get_write_con() -> duckdb.DuckDBPyConnection:
    """Return a new write-capable connection with retry on lock contention."""
    for attempt in range(10):
        try:
            return duckdb.connect(str(DB_PATH))
        except duckdb.IOException:
            if attempt == 9:
                raise
            time.sleep(0.05 * (attempt + 1))

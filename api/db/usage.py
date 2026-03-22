"""
Token usage tracker — SQLite-based, no DuckDB lock contention.

Stores per-AI-ID token usage in data/usage.db (separate from market data).
Both the x402 middleware and API routes use this for quota checks and billing.
"""
import logging
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

USAGE_DB_PATH = Path(__file__).parent.parent.parent / "data" / "usage.db"

_local = threading.local()


def _get_con() -> sqlite3.Connection:
    """Get a thread-local SQLite connection (auto-creates table)."""
    con = getattr(_local, "con", None)
    if con is None:
        USAGE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(USAGE_DB_PATH), check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=3000")
        con.execute("""
            CREATE TABLE IF NOT EXISTS ai_id_usage (
                ai_id TEXT PRIMARY KEY,
                total_tokens INTEGER DEFAULT 0
            )
        """)
        con.commit()
        _local.con = con
    return con


def count_tokens(ai_id: str) -> int:
    """Return total tokens used by this AI ID."""
    con = _get_con()
    row = con.execute(
        "SELECT total_tokens FROM ai_id_usage WHERE ai_id=?", (ai_id,)
    ).fetchone()
    return row[0] if row else 0


def add_tokens(ai_id: str, tokens: int):
    """Increment token usage for an AI ID."""
    con = _get_con()
    con.execute(
        """INSERT INTO ai_id_usage (ai_id, total_tokens) VALUES (?, ?)
           ON CONFLICT(ai_id) DO UPDATE SET total_tokens = total_tokens + excluded.total_tokens""",
        (ai_id, tokens),
    )
    con.commit()

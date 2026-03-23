"""
Token usage tracker — SQLite-based, no DuckDB lock contention.

Stores per-AI-ID token usage in data/usage.db (separate from market data).
Both the x402 middleware and API routes use this for quota checks and billing.
"""
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

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
        con.execute("""
            CREATE TABLE IF NOT EXISTS auth_tokens (
                token TEXT PRIMARY KEY,
                ai_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                created_at INTEGER DEFAULT (strftime('%s','now'))
            )
        """)
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_tokens_ai_id ON auth_tokens(ai_id)"
        )
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


# ---------------------------------------------------------------------------
# Auth token management
# ---------------------------------------------------------------------------

def save_token(token: str, ai_id: str, event_id: str):
    """Store a bearer token for an AI ID."""
    con = _get_con()
    con.execute(
        "INSERT OR REPLACE INTO auth_tokens (token, ai_id, event_id) VALUES (?, ?, ?)",
        (token, ai_id, event_id),
    )
    con.commit()


def get_ai_id_by_token(token: str) -> Optional[str]:
    """Look up the AI ID associated with a bearer token. Returns None if invalid."""
    con = _get_con()
    row = con.execute(
        "SELECT ai_id FROM auth_tokens WHERE token=?", (token,)
    ).fetchone()
    return row[0] if row else None


def revoke_tokens(ai_id: str) -> int:
    """Revoke all tokens for an AI ID. Returns number of tokens revoked."""
    con = _get_con()
    cur = con.execute("DELETE FROM auth_tokens WHERE ai_id=?", (ai_id,))
    con.commit()
    return cur.rowcount

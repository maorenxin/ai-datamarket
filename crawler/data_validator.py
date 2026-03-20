"""
Data validation for OHLCV records before writing to DuckDB.
"""
import logging
from datetime import datetime, timedelta
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


def validate_kline_row(row: dict, prev_time: Optional[datetime] = None) -> List[str]:
    """
    Returns a list of validation error messages (empty = valid).
    row keys: open_time, open, high, low, close, volume, quote_volume, trade_count
    """
    errors = []
    o, h, l, c = row["open"], row["high"], row["low"], row["close"]

    # Price sanity: close within 50%–200% of open (detects extreme outliers)
    if o > 0 and not (o * 0.5 <= c <= o * 2.0):
        errors.append(f"Suspicious price jump: open={o}, close={c}")

    # OHLC consistency
    if not (l <= o <= h and l <= c <= h):
        errors.append(f"OHLC inconsistent: o={o} h={h} l={l} c={c}")

    # Volume non-negative
    if row["volume"] < 0:
        errors.append(f"Negative volume: {row['volume']}")

    # Time continuity
    if prev_time is not None:
        expected = prev_time + timedelta(minutes=1)
        if row["open_time"] != expected:
            errors.append(
                f"Time gap: expected {expected.isoformat()}, got {row['open_time'].isoformat()}"
            )

    return errors


def validate_batch(rows: List[dict], log_warnings: bool = True) -> List[dict]:
    """Filter and return only valid rows. Logs warnings for invalid ones."""
    valid = []
    prev_time = None
    for row in rows:
        errors = validate_kline_row(row, prev_time)
        if errors:
            if log_warnings:
                logger.warning("Dropping invalid row %s: %s", row.get("open_time"), errors)
        else:
            valid.append(row)
            prev_time = row["open_time"]
    return valid

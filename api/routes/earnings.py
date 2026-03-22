"""
US Stock Earnings API routes — SEC EDGAR data.

GET /earnings          — query filings (paid, requires ai_id)
GET /earnings/companies — list supported companies (free)
"""
import gzip
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from config.earnings import (
    COMPANIES,
    DETAIL_TOKEN_COST,
    EARNINGS_DATA_DIR,
    EARNINGS_DB_PATH,
    SUMMARY_METRICS,
)
from config.pricing import PAID_FREE_QUOTA

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    """Get a read-only SQLite connection."""
    if not EARNINGS_DB_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="Earnings database not yet initialized. Run the crawler first.",
        )
    con = sqlite3.connect(str(EARNINGS_DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _count_tokens(ai_id: str) -> int:
    """Return total tokens used by this AI ID (from DuckDB ai_id_usage table).
    Returns 0 if DuckDB is locked (crawlers running)."""
    try:
        from api.db.duckdb_client import get_write_con
        ddb = get_write_con()
        try:
            row = ddb.execute(
                "SELECT total_tokens FROM ai_id_usage WHERE ai_id=?", [ai_id]
            ).fetchone()
            return row[0] if row else 0
        finally:
            ddb.close()
    except Exception:
        logger.warning("DuckDB locked, skipping token count for %s", ai_id)
        return 0


def _add_tokens(ai_id: str, tokens: int):
    """Increment token usage for an AI ID (in DuckDB).
    Silently skips if DuckDB is locked."""
    try:
        from api.db.duckdb_client import get_write_con
        ddb = get_write_con()
        try:
            ddb.execute(
                """INSERT INTO ai_id_usage (ai_id, total_tokens) VALUES (?, ?)
                   ON CONFLICT (ai_id) DO UPDATE SET total_tokens = total_tokens + excluded.total_tokens""",
                [ai_id, tokens],
            )
        finally:
            ddb.close()
    except Exception:
        logger.warning("DuckDB locked, skipping token tracking for %s", ai_id)


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------

def _build_summary(
    con: sqlite3.Connection,
    ticker: str,
    accession: str,
) -> Dict[str, Any]:
    """Build ~10 key metrics for a filing from financial_facts."""
    rows = con.execute(
        "SELECT metric, value, unit FROM financial_facts "
        "WHERE ticker=? AND accession=? AND unit IN ('USD', 'USD/shares', 'shares')",
        (ticker, accession),
    ).fetchall()

    # Index by metric name for quick lookup
    fact_map = {}  # type: Dict[str, Any]
    for r in rows:
        fact_map[r["metric"]] = {"value": r["value"], "unit": r["unit"]}

    summary = {}  # type: Dict[str, Any]
    for key, tags in SUMMARY_METRICS.items():
        for tag in tags:
            if tag in fact_map:
                summary[key] = fact_map[tag]["value"]
                break

    return summary


# ---------------------------------------------------------------------------
# Statements builder (all XBRL facts for a filing)
# ---------------------------------------------------------------------------

def _build_statements(
    con: sqlite3.Connection,
    ticker: str,
    accession: str,
) -> List[Dict[str, Any]]:
    """Return all financial facts for a filing."""
    rows = con.execute(
        "SELECT metric, value, unit, period_start, period_end "
        "FROM financial_facts WHERE ticker=? AND accession=? "
        "ORDER BY metric, period_end",
        (ticker, accession),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Full text reader
# ---------------------------------------------------------------------------

def _read_full_text(ticker: str, form_type: str, period_end: str) -> Optional[str]:
    """Read gzipped Markdown full text for a filing."""
    filename = "{}_{}.md.gz".format(form_type, period_end)
    filepath = EARNINGS_DATA_DIR / ticker / filename
    if not filepath.exists():
        return None
    with gzip.open(str(filepath), "rt", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/earnings/companies")
async def list_companies():
    """List supported companies and their filing counts. Free, no ai_id needed."""
    result = []
    try:
        con = _get_db()
        for ticker, info in COMPANIES.items():
            row = con.execute(
                "SELECT COUNT(*) as cnt FROM filings WHERE ticker=?", (ticker,)
            ).fetchone()
            count = row["cnt"] if row else 0
            result.append({
                "ticker": ticker,
                "company": info["name"],
                "filings_count": count,
            })
        con.close()
    except HTTPException:
        # DB not initialized — return companies with 0 filings
        for ticker, info in COMPANIES.items():
            result.append({
                "ticker": ticker,
                "company": info["name"],
                "filings_count": 0,
            })

    return {"companies": result, "count": len(result)}


@router.get("/earnings")
async def get_earnings(
    ticker: str = Query(..., description="Company ticker, e.g. AAPL"),
    form_type: str = Query("all", description="10-K, 10-Q, or all"),
    period: Optional[str] = Query(None, description="Period end date YYYY-MM-DD (default: latest)"),
    limit: int = Query(4, ge=1, le=40, description="Number of filings to return"),
    detail: str = Query("summary", description="summary / statements / full"),
    ai_id: Optional[str] = Query(None, description="zCloak AI ID (required — paid data)"),
):
    """Query US stock earnings data. Paid data — requires ai_id."""
    # Validate ai_id
    if not ai_id:
        raise HTTPException(
            status_code=401,
            detail="ai_id is required for earnings data (paid category). Get one free at https://id.zcloak.ai",
        )

    # Validate ticker
    ticker = ticker.upper()
    if ticker not in COMPANIES:
        raise HTTPException(
            status_code=400,
            detail="Ticker '{}' not supported. Supported: {}".format(
                ticker, list(COMPANIES.keys())
            ),
        )

    # Validate detail level
    if detail not in DETAIL_TOKEN_COST:
        raise HTTPException(
            status_code=400,
            detail="Invalid detail level '{}'. Valid: summary, statements, full".format(detail),
        )

    # Validate form_type
    if form_type not in ("all", "10-K", "10-Q"):
        raise HTTPException(
            status_code=400,
            detail="Invalid form_type '{}'. Valid: all, 10-K, 10-Q".format(form_type),
        )

    con = _get_db()

    # Build query
    sql = "SELECT * FROM filings WHERE ticker=?"
    params = [ticker]  # type: List[Any]

    if form_type != "all":
        sql += " AND form_type=?"
        params.append(form_type)

    if period:
        sql += " AND period_end=?"
        params.append(period)

    sql += " ORDER BY period_end DESC LIMIT ?"
    params.append(limit)

    rows = con.execute(sql, params).fetchall()

    if not rows:
        con.close()
        raise HTTPException(
            status_code=404,
            detail="No filings found for {} (form_type={}, period={})".format(
                ticker, form_type, period
            ),
        )

    # Calculate token cost
    token_cost_per = DETAIL_TOKEN_COST[detail]
    total_tokens = token_cost_per * len(rows)

    # Check quota
    current_used = _count_tokens(ai_id)
    remaining_before = max(0, PAID_FREE_QUOTA - current_used)

    # Charge tokens
    _add_tokens(ai_id, total_tokens)
    tokens_remaining = max(0, remaining_before - total_tokens)

    # Build response
    filings_out = []
    for row in rows:
        filing = {
            "form_type": row["form_type"],
            "filing_date": row["filing_date"],
            "period_end": row["period_end"],
            "fiscal_year": row["fiscal_year"],
            "fiscal_quarter": row["fiscal_quarter"],
        }

        # Always include summary
        filing["summary"] = _build_summary(con, ticker, row["accession"])

        # Statements level
        if detail in ("statements", "full"):
            filing["statements"] = _build_statements(con, ticker, row["accession"])

        # Full text level
        if detail == "full":
            text = _read_full_text(ticker, row["form_type"], row["period_end"])
            if text:
                filing["full_text"] = text
            else:
                filing["full_text"] = None
                filing["full_text_note"] = "Full text not yet downloaded. Run crawler without --skip-full-text."

        filings_out.append(filing)

    con.close()

    return {
        "ticker": ticker,
        "company": COMPANIES[ticker]["name"],
        "filings": filings_out,
        "tokens_used": total_tokens,
        "tokens_remaining_free": tokens_remaining,
    }

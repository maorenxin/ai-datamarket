"""
SEC EDGAR Earnings Crawler — fetch 10-K/10-Q filings for US public companies.

Two phases:
1. Structured data: submissions JSON → filing list, companyfacts JSON → XBRL facts → SQLite
2. Full text: filing HTML → html2text → gzip Markdown → filesystem

Usage:
    python crawler/earnings_crawler.py                    # Top 10 companies
    python crawler/earnings_crawler.py --all              # ALL SEC-listed companies (~10K)
    python crawler/earnings_crawler.py --top 500          # Top N by market cap
    python crawler/earnings_crawler.py --ticker AAPL      # Single company
    python crawler/earnings_crawler.py --skip-full-text   # Skip HTML download
"""
import argparse
import asyncio
import gzip
import json
import logging
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.earnings import (
    COMPANIES,
    EARNINGS_DATA_DIR,
    EARNINGS_DB_PATH,
    SEC_BASE_URL,
    SEC_USER_AGENT,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Rate limiting: SEC allows 10 req/sec, we stay under with semaphore + delay
CONCURRENCY = 8
REQUEST_DELAY = 0.12  # seconds between requests

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


# ---------------------------------------------------------------------------
# Dynamic company list from SEC
# ---------------------------------------------------------------------------

def fetch_all_companies(top_n: int = 0) -> Dict[str, Dict[str, str]]:
    """Fetch full company list from SEC EDGAR. Returns {ticker: {name, cik}}.
    The SEC list is roughly ordered by market cap.
    If top_n > 0, return only the first N companies.
    """
    import httpx as httpx_sync
    resp = httpx_sync.get(
        SEC_TICKERS_URL,
        headers={"User-Agent": SEC_USER_AGENT},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()

    companies = {}
    for entry in data.values():
        ticker = entry["ticker"]
        cik_raw = entry["cik_str"]
        cik = str(cik_raw).zfill(10)
        name = entry["title"]
        companies[ticker] = {"name": name, "cik": cik}
        if top_n > 0 and len(companies) >= top_n:
            break

    logger.info("Fetched %d companies from SEC EDGAR", len(companies))
    return companies


# ---------------------------------------------------------------------------
# SQLite setup
# ---------------------------------------------------------------------------

def init_db() -> sqlite3.Connection:
    """Create tables if needed and return a connection."""
    EARNINGS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(EARNINGS_DB_PATH))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS filings (
            ticker TEXT NOT NULL,
            cik TEXT NOT NULL,
            accession TEXT NOT NULL,
            form_type TEXT NOT NULL,
            filing_date TEXT NOT NULL,
            period_end TEXT NOT NULL,
            fiscal_year INTEGER,
            fiscal_quarter INTEGER,
            primary_doc TEXT,
            has_full_text INTEGER DEFAULT 0,
            PRIMARY KEY (ticker, accession)
        );

        CREATE TABLE IF NOT EXISTS financial_facts (
            ticker TEXT NOT NULL,
            accession TEXT NOT NULL,
            metric TEXT NOT NULL,
            period_end TEXT NOT NULL,
            period_start TEXT,
            value REAL NOT NULL,
            unit TEXT NOT NULL,
            form_type TEXT NOT NULL,
            fiscal_year INTEGER,
            fiscal_quarter INTEGER,
            PRIMARY KEY (ticker, accession, metric, period_end, unit)
        );

        CREATE INDEX IF NOT EXISTS idx_facts_ticker_period
            ON financial_facts(ticker, period_end);
        CREATE INDEX IF NOT EXISTS idx_facts_metric
            ON financial_facts(metric);
    """)
    con.commit()
    return con


# ---------------------------------------------------------------------------
# SEC EDGAR fetchers
# ---------------------------------------------------------------------------

async def fetch_json(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    url: str,
) -> Optional[Dict[str, Any]]:
    """Fetch JSON from SEC EDGAR with rate limiting."""
    async with sem:
        await asyncio.sleep(REQUEST_DELAY)
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("Failed to fetch %s: %s", url, e)
            return None


async def fetch_text(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    url: str,
) -> Optional[str]:
    """Fetch text/HTML from SEC EDGAR with rate limiting."""
    async with sem:
        await asyncio.sleep(REQUEST_DELAY)
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logger.error("Failed to fetch %s: %s", url, e)
            return None


# ---------------------------------------------------------------------------
# Phase 1: Structured data
# ---------------------------------------------------------------------------

def parse_submissions(
    ticker: str,
    cik: str,
    data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Extract 10-K and 10-Q filings from submissions JSON."""
    filings = []
    recent = data.get("filings", {}).get("recent", {})
    if not recent:
        return filings

    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])
    # reportDate is the period end date
    report_dates = recent.get("reportDate", [])

    for i, form in enumerate(forms):
        if form not in ("10-K", "10-Q"):
            continue
        accession = accessions[i] if i < len(accessions) else ""
        filing_date = filing_dates[i] if i < len(filing_dates) else ""
        primary_doc = primary_docs[i] if i < len(primary_docs) else ""
        period_end = report_dates[i] if i < len(report_dates) else filing_date

        # Guess fiscal year/quarter from period_end
        fiscal_year = None
        fiscal_quarter = None
        if period_end:
            try:
                dt = datetime.strptime(period_end, "%Y-%m-%d")
                fiscal_year = dt.year
                if form == "10-Q":
                    # Approximate quarter from month
                    fiscal_quarter = (dt.month - 1) // 3 + 1
            except ValueError:
                pass

        filings.append({
            "ticker": ticker,
            "cik": cik,
            "accession": accession,
            "form_type": form,
            "filing_date": filing_date,
            "period_end": period_end,
            "fiscal_year": fiscal_year,
            "fiscal_quarter": fiscal_quarter,
            "primary_doc": primary_doc,
        })

    return filings


def parse_companyfacts(
    ticker: str,
    data: Dict[str, Any],
    filing_accessions: set,
) -> List[Dict[str, Any]]:
    """Extract XBRL facts from companyfacts JSON, filtered to known filings."""
    facts = []
    us_gaap = data.get("facts", {}).get("us-gaap", {})

    for metric_name, metric_data in us_gaap.items():
        units = metric_data.get("units", {})
        for unit_name, entries in units.items():
            for entry in entries:
                accn = entry.get("accn", "")
                if accn not in filing_accessions:
                    continue

                val = entry.get("val")
                if val is None:
                    continue

                form = entry.get("form", "")
                if form not in ("10-K", "10-Q"):
                    continue

                period_end = entry.get("end", "")
                period_start = entry.get("start")  # None for instant metrics
                fy = entry.get("fy")
                fp = entry.get("fp", "")

                fiscal_quarter = None
                if fp and fp.startswith("Q"):
                    try:
                        fiscal_quarter = int(fp[1:])
                    except ValueError:
                        pass

                facts.append({
                    "ticker": ticker,
                    "accession": accn,
                    "metric": metric_name,
                    "period_end": period_end,
                    "period_start": period_start,
                    "value": float(val),
                    "unit": unit_name,
                    "form_type": form,
                    "fiscal_year": fy,
                    "fiscal_quarter": fiscal_quarter,
                })

    return facts


def store_filings(con: sqlite3.Connection, filings: List[Dict[str, Any]]) -> int:
    """INSERT OR IGNORE filings into SQLite. Returns count."""
    if not filings:
        return 0
    con.executemany(
        """INSERT OR IGNORE INTO filings
           (ticker, cik, accession, form_type, filing_date, period_end,
            fiscal_year, fiscal_quarter, primary_doc)
           VALUES (:ticker, :cik, :accession, :form_type, :filing_date,
                   :period_end, :fiscal_year, :fiscal_quarter, :primary_doc)""",
        filings,
    )
    con.commit()
    return len(filings)


def store_facts(con: sqlite3.Connection, facts: List[Dict[str, Any]]) -> int:
    """INSERT OR IGNORE financial facts into SQLite. Returns count."""
    if not facts:
        return 0
    con.executemany(
        """INSERT OR IGNORE INTO financial_facts
           (ticker, accession, metric, period_end, period_start, value,
            unit, form_type, fiscal_year, fiscal_quarter)
           VALUES (:ticker, :accession, :metric, :period_end, :period_start,
                   :value, :unit, :form_type, :fiscal_year, :fiscal_quarter)""",
        facts,
    )
    con.commit()
    return len(facts)


async def crawl_structured(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    con: sqlite3.Connection,
    ticker: str,
    cik: str,
) -> int:
    """Phase 1: fetch submissions + companyfacts, store in SQLite."""
    # Fetch submissions
    sub_url = "{}/submissions/CIK{}.json".format(SEC_BASE_URL, cik)
    sub_data = await fetch_json(client, sem, sub_url)
    if not sub_data:
        logger.error("%s: failed to fetch submissions", ticker)
        return 0

    filings = parse_submissions(ticker, cik, sub_data)
    stored = store_filings(con, filings)
    logger.info("%s: %d filings found, %d stored", ticker, len(filings), stored)

    if not filings:
        return 0

    # Fetch companyfacts
    facts_url = "{}/api/xbrl/companyfacts/CIK{}.json".format(SEC_BASE_URL, cik)
    facts_data = await fetch_json(client, sem, facts_url)
    if not facts_data:
        logger.error("%s: failed to fetch companyfacts", ticker)
        return stored

    filing_accessions = {f["accession"] for f in filings}
    facts = parse_companyfacts(ticker, facts_data, filing_accessions)
    facts_stored = store_facts(con, facts)
    logger.info("%s: %d facts parsed, %d stored", ticker, len(facts), facts_stored)

    return stored + facts_stored


# ---------------------------------------------------------------------------
# Phase 2: Full text (HTML → Markdown → gzip)
# ---------------------------------------------------------------------------

async def crawl_full_text(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    con: sqlite3.Connection,
    ticker: str,
    cik: str,
):
    """Phase 2: download filing HTML, convert to Markdown, gzip store."""
    try:
        import html2text
    except ImportError:
        logger.error("html2text not installed. Run: pip install html2text")
        return

    # Get filings that don't have full text yet
    rows = con.execute(
        "SELECT accession, form_type, period_end, primary_doc FROM filings "
        "WHERE ticker=? AND has_full_text=0 AND primary_doc IS NOT NULL AND primary_doc != ''",
        (ticker,),
    ).fetchall()

    if not rows:
        logger.info("%s: all filings already have full text", ticker)
        return

    out_dir = EARNINGS_DATA_DIR / ticker
    out_dir.mkdir(parents=True, exist_ok=True)

    h2t = html2text.HTML2Text()
    h2t.ignore_links = False
    h2t.ignore_images = True
    h2t.body_width = 0  # no wrapping

    for accession, form_type, period_end, primary_doc in rows:
        # Build URL: accession with dashes removed for path
        accession_path = accession.replace("-", "")
        url = "{}/Archives/edgar/data/{}/{}/{}".format(
            SEC_BASE_URL, cik.lstrip("0"), accession_path, primary_doc,
        )

        html = await fetch_text(client, sem, url)
        if not html:
            continue

        # Convert to markdown
        md = h2t.handle(html)

        # Truncate at 500KB to keep things manageable
        max_bytes = 500 * 1024
        if len(md.encode("utf-8")) > max_bytes:
            md = md[:max_bytes] + "\n\n[TRUNCATED]"

        # Save as gzip
        filename = "{}_{}.md.gz".format(form_type, period_end)
        filepath = out_dir / filename
        with gzip.open(str(filepath), "wt", encoding="utf-8") as f:
            f.write(md)

        # Mark as having full text
        con.execute(
            "UPDATE filings SET has_full_text=1 WHERE ticker=? AND accession=?",
            (ticker, accession),
        )
        con.commit()
        logger.info("%s: saved %s (%d bytes gzipped)", ticker, filename, filepath.stat().st_size)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def crawl_company(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    con: sqlite3.Connection,
    ticker: str,
    company_info: Dict[str, str],
    skip_full_text: bool = False,
):
    """Crawl one company end-to-end."""
    cik = company_info["cik"]
    name = company_info.get("name", ticker)
    logger.info("=== %s (%s) CIK=%s ===", ticker, name, cik)

    await crawl_structured(client, sem, con, ticker, cik)

    if not skip_full_text:
        await crawl_full_text(client, sem, con, ticker, cik)


async def main(
    ticker: Optional[str] = None,
    skip_full_text: bool = False,
    all_companies: bool = False,
    top_n: int = 0,
):
    con = init_db()

    headers = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    sem = asyncio.Semaphore(CONCURRENCY)

    # Determine company list
    if ticker:
        # Single ticker — check hardcoded first, then fetch from SEC
        info = COMPANIES.get(ticker.upper())
        if not info:
            all_cos = fetch_all_companies()
            info = all_cos.get(ticker.upper())
        if not info:
            logger.error("Unknown ticker: %s", ticker)
            con.close()
            return
        targets = {ticker.upper(): info}
    elif all_companies or top_n > 0:
        targets = fetch_all_companies(top_n=top_n)
    else:
        targets = COMPANIES

    logger.info("Crawling %d companies (skip_full_text=%s)", len(targets), skip_full_text)

    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
    ) as client:
        done = 0
        for t, info in targets.items():
            # Skip if already has filings (resume support)
            existing = con.execute(
                "SELECT COUNT(*) FROM filings WHERE ticker=?", (t,)
            ).fetchone()[0]
            if existing > 0:
                logger.info("%s: already has %d filings, skipping", t, existing)
                done += 1
                continue

            await crawl_company(client, sem, con, t, info, skip_full_text)
            done += 1
            if done % 100 == 0:
                logger.info("Progress: %d/%d companies done", done, len(targets))

    # Final stats
    total_filings = con.execute("SELECT COUNT(*) FROM filings").fetchone()[0]
    total_facts = con.execute("SELECT COUNT(*) FROM financial_facts").fetchone()[0]
    total_tickers = con.execute("SELECT COUNT(DISTINCT ticker) FROM filings").fetchone()[0]
    logger.info("Done. %d companies, %d filings, %d facts in database.",
                total_tickers, total_filings, total_facts)

    con.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SEC EDGAR Earnings Crawler")
    parser.add_argument("--ticker", type=str, default=None,
                        help="Crawl single ticker (e.g. AAPL)")
    parser.add_argument("--all", action="store_true", dest="all_companies",
                        help="Crawl ALL SEC-listed companies (~10K)")
    parser.add_argument("--top", type=int, default=0,
                        help="Crawl top N companies by market cap (e.g. --top 500)")
    parser.add_argument("--skip-full-text", action="store_true",
                        help="Skip downloading filing full text (HTML → Markdown)")
    args = parser.parse_args()

    asyncio.run(main(args.ticker, args.skip_full_text, args.all_companies, args.top))

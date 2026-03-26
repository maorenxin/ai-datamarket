"""
Economy data routes — powered by FRED (Federal Reserve Economic Data).

GET /economy/series     — fetch a FRED time series
GET /economy/search     — search FRED series
GET /economy/available  — list common series IDs
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.providers.client import get_fred_client, df_to_records

router = APIRouter(tags=["economy"])


COMMON_SERIES = {
    "GDP": "Gross Domestic Product",
    "UNRATE": "Unemployment Rate",
    "CPIAUCSL": "Consumer Price Index (All Urban)",
    "FEDFUNDS": "Federal Funds Rate",
    "DGS10": "10-Year Treasury Yield",
    "DGS2": "2-Year Treasury Yield",
    "M2SL": "M2 Money Supply",
    "PAYEMS": "Total Nonfarm Payrolls",
    "UMCSENT": "Consumer Sentiment (UMich)",
    "INDPRO": "Industrial Production Index",
    "HOUST": "Housing Starts",
    "RSAFS": "Retail Sales",
    "PCE": "Personal Consumption Expenditures",
    "PCEPI": "PCE Price Index",
}


@router.get("/economy/available")
async def economy_available():
    """List common FRED series IDs."""
    return {"series": COMMON_SERIES, "note": "Any valid FRED series_id is supported. See https://fred.stlouisfed.org"}


def _fetch_series(series_id, limit):
    # type: (str, int) -> list
    fred = get_fred_client()
    s = fred.get_series(series_id)
    if s is None or s.empty:
        return []
    import pandas as pd
    df = s.tail(limit).reset_index()
    df.columns = ["date", "value"]
    records = []
    for _, row in df.iterrows():
        val = row["value"]
        records.append({
            "date": row["date"].isoformat() if hasattr(row["date"], "isoformat") else str(row["date"]),
            "value": None if pd.isna(val) else float(val),
        })
    return records


@router.get("/economy/series")
async def economy_series(
    series_id: str = Query(..., description="FRED series ID, e.g. GDP, UNRATE, CPIAUCSL"),
    limit: int = Query(60, ge=1, le=1000, description="Number of recent observations"),
):
    records = await asyncio.to_thread(_fetch_series, series_id, limit)
    if not records:
        raise HTTPException(status_code=404, detail="No data for FRED series '{}'".format(series_id))
    return {"series_id": series_id, "count": len(records), "data": records}


def _search_series(query, limit):
    # type: (str, int) -> list
    try:
        fred = get_fred_client()
        df = fred.search(query)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    results = []
    for _, row in df.head(limit).iterrows():
        results.append({
            "series_id": row.get("id", ""),
            "title": row.get("title", ""),
            "frequency": row.get("frequency_short", ""),
            "units": row.get("units_short", ""),
            "seasonal_adjustment": row.get("seasonal_adjustment_short", ""),
        })
    return results


@router.get("/economy/search")
async def economy_search(
    query: str = Query(..., description="Search term, e.g. 'inflation', 'unemployment'"),
    limit: int = Query(10, ge=1, le=50, description="Max results"),
):
    results = await asyncio.to_thread(_search_series, query, limit)
    if not results:
        raise HTTPException(status_code=404, detail="No FRED series found for '{}'".format(query))
    return {"query": query, "count": len(results), "results": results}

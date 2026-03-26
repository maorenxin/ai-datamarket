"""
Fixed income / bond data routes — powered by FRED.

GET /fixedincome/rates     — treasury yields and interest rates
GET /fixedincome/spread    — yield spread (e.g. 10Y-2Y)
GET /fixedincome/available — list common rate series
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.providers.client import get_fred_client

router = APIRouter(tags=["fixedincome"])

RATE_SERIES = {
    "DGS1MO": "1-Month Treasury",
    "DGS3MO": "3-Month Treasury",
    "DGS6MO": "6-Month Treasury",
    "DGS1": "1-Year Treasury",
    "DGS2": "2-Year Treasury",
    "DGS5": "5-Year Treasury",
    "DGS7": "7-Year Treasury",
    "DGS10": "10-Year Treasury",
    "DGS20": "20-Year Treasury",
    "DGS30": "30-Year Treasury",
    "FEDFUNDS": "Federal Funds Rate",
    "DPRIME": "Bank Prime Loan Rate",
    "MORTGAGE30US": "30-Year Fixed Mortgage Rate",
    "MORTGAGE15US": "15-Year Fixed Mortgage Rate",
    "BAMLH0A0HYM2": "High Yield Corporate Bond Spread",
    "AAA": "Moody's AAA Corporate Bond Yield",
    "BAA": "Moody's BAA Corporate Bond Yield",
}


@router.get("/fixedincome/available")
async def fixedincome_available():
    """List available fixed income series."""
    return {"series": RATE_SERIES, "note": "Any valid FRED series_id for rates/yields is supported."}


def _fetch_rate(series_id, limit):
    # type: (str, int) -> list
    import pandas as pd
    fred = get_fred_client()
    s = fred.get_series(series_id)
    if s is None or s.empty:
        return []
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


@router.get("/fixedincome/rates")
async def fixedincome_rates(
    series_id: str = Query("DGS10", description="FRED series ID, e.g. DGS10, FEDFUNDS, MORTGAGE30US"),
    limit: int = Query(60, ge=1, le=1000, description="Number of recent observations"),
):
    records = await asyncio.to_thread(_fetch_rate, series_id, limit)
    if not records:
        raise HTTPException(status_code=404, detail="No data for '{}'".format(series_id))
    return {"series_id": series_id, "name": RATE_SERIES.get(series_id, series_id), "count": len(records), "data": records}


def _fetch_spread(long_id, short_id, limit):
    # type: (str, str, int) -> list
    import pandas as pd
    fred = get_fred_client()
    long_s = fred.get_series(long_id)
    short_s = fred.get_series(short_id)
    if long_s is None or short_s is None:
        return []
    spread = (long_s - short_s).dropna().tail(limit)
    records = []
    for date, val in spread.items():
        records.append({
            "date": date.isoformat() if hasattr(date, "isoformat") else str(date),
            "spread": round(float(val), 4),
        })
    return records


@router.get("/fixedincome/spread")
async def fixedincome_spread(
    long_series: str = Query("DGS10", description="Long-term rate series (e.g. DGS10)"),
    short_series: str = Query("DGS2", description="Short-term rate series (e.g. DGS2)"),
    limit: int = Query(60, ge=1, le=1000, description="Number of recent observations"),
):
    records = await asyncio.to_thread(_fetch_spread, long_series, short_series, limit)
    if not records:
        raise HTTPException(status_code=404, detail="No spread data for {}-{}".format(long_series, short_series))
    return {
        "spread": "{}-{}".format(long_series, short_series),
        "count": len(records),
        "data": records,
    }

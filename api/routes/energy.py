"""
Energy / commodities data routes — FRED for prices, EIA for US energy data.

GET /energy/price     — commodity price series (via FRED)
GET /energy/eia       — EIA energy data (if API key available)
GET /energy/available — list common commodity series
"""
import asyncio
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.providers.client import get_fred_client, get_http_client

router = APIRouter(tags=["energy"])

COMMODITY_SERIES = {
    "DCOILWTICO": "WTI Crude Oil ($/barrel)",
    "DCOILBRENTEU": "Brent Crude Oil ($/barrel)",
    "DHHNGSP": "Henry Hub Natural Gas ($/MMBtu)",
    "GOLDAMGBD228NLBM": "Gold Price (London, $/oz)",
    "DEXUSAL": "Silver Price (London, $/oz)",
    "PCOPPUSDM": "Copper Price ($/lb)",
    "CHRIS-CME_CL1": "WTI Crude Futures",
    "GASREGW": "US Regular Gasoline ($/gallon)",
    "APU0000708111": "US Electricity Price (cents/kWh)",
}


@router.get("/energy/available")
async def energy_available():
    """List available commodity/energy series."""
    eia_key = os.environ.get("EIA_API_KEY")
    return {
        "fred_series": COMMODITY_SERIES,
        "eia_available": eia_key is not None,
        "note": "Commodity prices via FRED. Set EIA_API_KEY env var for US energy statistics.",
    }


def _fetch_commodity(series_id, limit):
    # type: (str, int) -> list
    import pandas as pd
    try:
        fred = get_fred_client()
        s = fred.get_series(series_id)
    except Exception:
        return []
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


@router.get("/energy/price")
async def energy_price(
    series_id: str = Query("DCOILWTICO", description="FRED series ID, e.g. DCOILWTICO, GOLDAMGBD228NLBM"),
    limit: int = Query(60, ge=1, le=1000, description="Number of recent observations"),
):
    records = await asyncio.to_thread(_fetch_commodity, series_id, limit)
    if not records:
        raise HTTPException(status_code=404, detail="No data for '{}'".format(series_id))
    return {"series_id": series_id, "name": COMMODITY_SERIES.get(series_id, series_id), "count": len(records), "data": records}


@router.get("/energy/eia")
async def energy_eia(
    series_id: str = Query("ELEC.GEN.ALL-US-99.M", description="EIA series ID"),
    limit: int = Query(24, ge=1, le=200, description="Number of recent observations"),
):
    """Fetch EIA energy data (requires EIA_API_KEY env var)."""
    api_key = os.environ.get("EIA_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="EIA_API_KEY not configured. Set env var to enable.")

    client = get_http_client()
    url = "https://api.eia.gov/v2/seriesid/{series_id}?api_key={key}&num={limit}".format(
        series_id=series_id, key=api_key, limit=limit,
    )
    try:
        resp = await client.get(url, timeout=30.0)
    except Exception as e:
        raise HTTPException(status_code=502, detail="EIA API error: {}".format(str(e)))

    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="No EIA data for '{}'".format(series_id))

    try:
        data = resp.json()
        series = data.get("response", {}).get("data", [])
        records = [{"period": r.get("period"), "value": r.get("value")} for r in series[:limit]]
    except Exception as e:
        raise HTTPException(status_code=502, detail="Failed to parse EIA response: {}".format(str(e)))

    return {"source": "EIA", "series_id": series_id, "count": len(records), "data": records}

"""
International macro data routes — OECD and IMF via public SDMX-JSON APIs.

GET /macro/oecd         — OECD indicator data
GET /macro/imf          — IMF indicator data
GET /macro/available    — list common indicators
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.providers.client import get_http_client

router = APIRouter(tags=["macro"])

OECD_INDICATORS = {
    "GDP": "Gross Domestic Product",
    "CPI": "Consumer Price Index",
    "UNEMPLOYMENT": "Unemployment Rate",
    "TRADE": "Trade Balance",
}

IMF_INDICATORS = {
    "NGDP_RPCH": "Real GDP Growth (%)",
    "PCPIPCH": "Inflation, CPI (%)",
    "LUR": "Unemployment Rate (%)",
    "BCA_NGDPD": "Current Account Balance (% GDP)",
}

# IMF uses ISO 3-letter codes; map common 2-letter inputs
_ISO2_TO_ISO3 = {
    "US": "USA", "GB": "GBR", "JP": "JPN", "CN": "CHN", "DE": "DEU",
    "FR": "FRA", "IN": "IND", "BR": "BRA", "CA": "CAN", "AU": "AUS",
    "KR": "KOR", "IT": "ITA", "ES": "ESP", "MX": "MEX", "RU": "RUS",
}


@router.get("/macro/available")
async def macro_available():
    """List common OECD and IMF indicators."""
    return {
        "oecd": OECD_INDICATORS,
        "imf": IMF_INDICATORS,
        "note": "OECD uses SDMX REST API. IMF uses World Economic Outlook (WEO) dataset.",
    }


@router.get("/macro/oecd")
async def macro_oecd(
    dataset: str = Query("QNA", description="OECD dataset ID, e.g. QNA, PRICES_CPI, LFS_SEXAGE_I_R"),
    country: str = Query("USA", description="ISO 3-letter country code, e.g. USA, GBR, JPN, DEU"),
    subject: str = Query("B1_GE", description="Subject code, e.g. B1_GE (GDP), CPALTT01 (CPI)"),
    measure: str = Query("VOBARSA", description="Measure, e.g. VOBARSA (volume), IXOB (index), GY (growth)"),
    frequency: str = Query("Q", description="A (annual), Q (quarterly), M (monthly)"),
    start_time: str = Query("2015", description="Start period, e.g. 2015 or 2020-Q1"),
    limit: int = Query(20, ge=1, le=100, description="Number of recent observations"),
):
    """Fetch OECD data via legacy stats.oecd.org SDMX-JSON API."""
    client = get_http_client()
    # Old OECD API — reliable and well-documented
    key = "{country}.{subject}.{measure}.{freq}".format(
        country=country, subject=subject, measure=measure, freq=frequency,
    )
    url = "https://stats.oecd.org/sdmx-json/data/{dataset}/{key}/all?startTime={start}".format(
        dataset=dataset, key=key, start=start_time,
    )
    try:
        resp = await client.get(url, timeout=30.0)
    except Exception as e:
        raise HTTPException(status_code=502, detail="OECD API error: {}".format(str(e)))

    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="No OECD data for {}/{}. Try different parameters. Common datasets: QNA (GDP), PRICES_CPI (CPI), LFS_SEXAGE_I_R (labor).".format(dataset, key))

    try:
        data = resp.json()
        observations = _parse_oecd_old(data, limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail="Failed to parse OECD response: {}".format(str(e)))

    return {"source": "OECD", "dataset": dataset, "country": country, "subject": subject, "count": len(observations), "data": observations}


def _parse_oecd_old(data, limit):
    # type: (dict, int) -> list
    """Parse old stats.oecd.org SDMX-JSON response into simple records."""
    records = []
    try:
        ds = data.get("data", {}).get("dataSets", [])
        if not ds:
            return []
        series_map = ds[0].get("series", {})

        # Get time dimension from structures (note: plural)
        structs = data.get("data", {}).get("structures", [])
        time_values = []
        if structs:
            dims = structs[0].get("dimensions", {})
            obs_dims = dims.get("observation", [])
            for d in obs_dims:
                if d.get("id") in ("TIME_PERIOD", "TIME"):
                    time_values = [v.get("id", v.get("name", "")) for v in d.get("values", [])]
                    break

        # Find the first series with enough observations
        best_series = None
        best_count = 0
        for key, sval in series_map.items():
            obs = sval.get("observations", {})
            if len(obs) > best_count:
                best_count = len(obs)
                best_series = obs

        if best_series:
            for idx_str, values in sorted(best_series.items(), key=lambda x: int(x[0])):
                idx = int(idx_str)
                period = time_values[idx] if idx < len(time_values) else idx_str
                val = values[0] if values else None
                records.append({"period": period, "value": val})
    except Exception:
        return []

    records.sort(key=lambda x: x.get("period", ""))
    return records[-limit:]


@router.get("/macro/imf")
async def macro_imf(
    indicator: str = Query("NGDP_RPCH", description="IMF WEO indicator, e.g. NGDP_RPCH, PCPIPCH, LUR"),
    country: str = Query("USA", description="ISO country code, e.g. USA, GBR, JPN, CHN (or 2-letter: US, GB, JP)"),
    limit: int = Query(10, ge=1, le=50, description="Number of recent years"),
):
    """Fetch IMF World Economic Outlook data."""
    # Convert 2-letter to 3-letter if needed
    country_code = _ISO2_TO_ISO3.get(country.upper(), country.upper())
    client = get_http_client()
    url = "https://www.imf.org/external/datamapper/api/v1/{indicator}/{country}".format(
        indicator=indicator, country=country_code,
    )
    try:
        resp = await client.get(url, timeout=30.0)
    except Exception as e:
        raise HTTPException(status_code=502, detail="IMF API error: {}".format(str(e)))

    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="No IMF data for {}/{}".format(indicator, country))

    try:
        data = resp.json()
        values = data.get("values", {}).get(indicator, {}).get(country_code, {})
        records = [{"year": int(y), "value": float(v)} for y, v in sorted(values.items()) if v is not None]
        records = records[-limit:]
    except Exception as e:
        raise HTTPException(status_code=502, detail="Failed to parse IMF response: {}".format(str(e)))

    return {"source": "IMF", "indicator": indicator, "name": IMF_INDICATORS.get(indicator, indicator), "country": country, "count": len(records), "data": records}

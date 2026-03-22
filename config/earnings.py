"""
US Stock Earnings configuration — SEC EDGAR data source.

Top 10 US companies by market cap, XBRL metric mappings, and path constants.
"""
from pathlib import Path
from typing import Dict, List

# ---------------------------------------------------------------------------
# Top 10 US companies — ticker → CIK mapping
# ---------------------------------------------------------------------------
COMPANIES = {
    "AAPL":  {"name": "Apple Inc.",              "cik": "0000320193"},
    "MSFT":  {"name": "Microsoft Corporation",   "cik": "0000789019"},
    "NVDA":  {"name": "NVIDIA Corporation",      "cik": "0001045810"},
    "AMZN":  {"name": "Amazon.com Inc.",         "cik": "0001018724"},
    "GOOGL": {"name": "Alphabet Inc.",           "cik": "0001652044"},
    "META":  {"name": "Meta Platforms Inc.",      "cik": "0001326801"},
    "BRK-B": {"name": "Berkshire Hathaway Inc.", "cik": "0001067983"},
    "TSLA":  {"name": "Tesla Inc.",              "cik": "0001318605"},
    "AVGO":  {"name": "Broadcom Inc.",           "cik": "0001649338"},
    "LLY":   {"name": "Eli Lilly and Company",  "cik": "0000059478"},
}  # type: Dict[str, Dict[str, str]]

# ---------------------------------------------------------------------------
# Summary metrics — XBRL tags with aliases (priority order)
# Different companies use different tags for the same concept.
# ---------------------------------------------------------------------------
SUMMARY_METRICS = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    ],
    "eps_basic": [
        "EarningsPerShareBasic",
    ],
    "eps_diluted": [
        "EarningsPerShareDiluted",
    ],
    "total_assets": [
        "Assets",
    ],
    "total_liabilities": [
        "Liabilities",
        "LiabilitiesAndStockholdersEquity",
    ],
    "stockholders_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "operating_income": [
        "OperatingIncomeLoss",
    ],
    "gross_profit": [
        "GrossProfit",
    ],
    "cash_and_equivalents": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsAndShortTermInvestments",
    ],
}  # type: Dict[str, List[str]]

# Flat set of all XBRL tags we care about (for quick lookup)
ALL_SUMMARY_TAGS = set()
for tags in SUMMARY_METRICS.values():
    ALL_SUMMARY_TAGS.update(tags)

# ---------------------------------------------------------------------------
# Storage paths
# ---------------------------------------------------------------------------
EARNINGS_DB_PATH = Path(__file__).parent.parent / "data" / "earnings" / "us.db"
EARNINGS_DATA_DIR = Path(__file__).parent.parent / "data" / "earnings" / "us"

# ---------------------------------------------------------------------------
# SEC EDGAR constants
# ---------------------------------------------------------------------------
SEC_BASE_URL = "https://data.sec.gov"
SEC_USER_AGENT = "ai-datamarket/0.1 contact@example.com"

# Token costs per detail level
DETAIL_TOKEN_COST = {
    "summary": 1,
    "statements": 10,
    "full": 100,
}  # type: Dict[str, int]

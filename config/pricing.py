"""
Data category pricing configuration for AI Datamarket.

Each data category is either free (public data, no AI ID needed)
or paid (requires AI ID, shared free quota, then x402 payment).
"""
from typing import Optional

# ---------------------------------------------------------------------------
# Data category definitions
# ---------------------------------------------------------------------------
# is_free=True  → public data, unlimited access, no AI ID required
# is_free=False → paid data, AI ID required, shared free quota then x402
# price_per_m   → USDC per 1M tokens (only for paid categories)

DATA_CATEGORIES = {
    # ── Currently active ──
    "crypto_ohlcv":     {"is_free": True},

    # ── Future paid categories ──
    "stock_ohlcv":      {"is_free": False, "price_per_m": 0.10},
    "futures_ohlcv":    {"is_free": False, "price_per_m": 0.10},
    "macro_data":       {"is_free": False, "price_per_m": 0.10},
    "options_data":     {"is_free": False, "price_per_m": 0.10},
    "fund_data":        {"is_free": False, "price_per_m": 0.10},
    "bond_data":        {"is_free": False, "price_per_m": 0.10},
    "index_data":       {"is_free": False, "price_per_m": 0.10},
    "industrial_chain": {"is_free": False, "price_per_m": 0.10},
    "stock_financials": {"is_free": False, "price_per_m": 0.10},
}

# Shared free quota for ALL paid data categories combined (per AI ID)
PAID_FREE_QUOTA = 1_000  # 1K tokens (low for debugging, raise for production)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def is_free_category(category: str) -> bool:
    """Return True if this data category is free (no AI ID needed)."""
    cat = DATA_CATEGORIES.get(category)
    if cat is None:
        return True  # unknown category defaults to free
    return cat.get("is_free", True)


def get_price_per_m(category: str) -> Optional[float]:
    """Return USDC price per 1M tokens for a paid category, or None if free."""
    cat = DATA_CATEGORIES.get(category)
    if cat is None or cat.get("is_free", True):
        return None
    return cat.get("price_per_m", 0.01)


# ---------------------------------------------------------------------------
# x402 payment configuration
# ---------------------------------------------------------------------------
X402_FACILITATOR_URL = "https://api.cdp.coinbase.com/platform/v2/x402"  # mainnet
X402_PAY_TO_EVM = "0xcd01585e3a8fa9a9cb01d91bc39948dff88d9761"

# Supported blockchain networks (default: Arbitrum for low fees)
X402_DEFAULT_NETWORK = "eip155:42161"  # Arbitrum One

X402_SUPPORTED_NETWORKS = {
    "arbitrum":     "eip155:42161",    # Arbitrum One (default — fast & cheap)
    "base":         "eip155:8453",     # Base Mainnet
    "solana":       "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
    "base-sepolia": "eip155:84532",    # testnet only
}

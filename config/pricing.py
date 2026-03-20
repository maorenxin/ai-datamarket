"""
Token pricing configuration for AI Datamarket.
"""

# Free quota per AI ID (in tokens = OHLCV data points)
FREE_TOKEN_QUOTA = 10_000_000

# Per-million-token pricing by data category (None = TBD, not yet monetized)
PRICE_PER_M_TOKENS: dict[str, float | None] = {
    "crypto_ohlcv": None,   # TBD by owner
    "stock_ohlcv":  None,
    "futures_ohlcv": None,
    "macro_data":   None,
    "options_data": None,
    "fund_data":    None,
    "bond_data":    None,
    "index_data":   None,
}

# x402 payment configuration
X402_FACILITATOR_URL = "https://api.cdp.coinbase.com/platform/v2/x402"  # mainnet
X402_PAY_TO_EVM = "0xYourEvmAddress"   # TODO: fill before production

# Supported blockchain networks (default: Arbitrum for low fees)
X402_DEFAULT_NETWORK = "eip155:42161"  # Arbitrum One

X402_SUPPORTED_NETWORKS: dict[str, str] = {
    "arbitrum":     "eip155:42161",    # Arbitrum One (default — fast & cheap)
    "base":         "eip155:8453",     # Base Mainnet
    "solana":       "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
    "base-sepolia": "eip155:84532",    # testnet only
}

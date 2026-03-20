"""
x402 payment middleware — stub for MVP.

Full implementation will:
  1. Check if the request has a valid PAYMENT-SIGNATURE header
  2. If not, and the user has exceeded their free quota, return HTTP 402
     with x402 payment requirements (amount, network, facilitator URL)
  3. If payment signature present, verify via facilitator and allow request

References:
  - https://github.com/coinbase/x402
  - config/pricing.py for network and address config
"""
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class X402Middleware(BaseHTTPMiddleware):
    """
    Middleware that enforces x402 payment for requests exceeding free quota.
    MVP: pass-through (no payment enforcement).
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # TODO: implement x402 payment check
        # 1. Extract AI-ID header
        # 2. Check token usage vs FREE_TOKEN_QUOTA
        # 3. If over quota and no valid PAYMENT-SIGNATURE, return 402
        # 4. Otherwise proceed
        return await call_next(request)


def get_payment_requirements(data_type: str, tokens_needed: int) -> dict:
    """
    Build the x402 payment requirements object.
    Returned in the 402 response body so agents can construct payment.
    """
    from config.pricing import (
        PRICE_PER_M_TOKENS,
        X402_DEFAULT_NETWORK,
        X402_FACILITATOR_URL,
        X402_PAY_TO_EVM,
    )
    price_per_m = PRICE_PER_M_TOKENS.get(data_type)
    if price_per_m is None:
        price_per_m = 0.01  # fallback default

    amount_usdc = (tokens_needed / 1_000_000) * price_per_m

    return {
        "x402Version": 1,
        "accepts": [
            {
                "scheme": "exact",
                "network": X402_DEFAULT_NETWORK,
                "maxAmountRequired": str(int(amount_usdc * 1_000_000)),  # USDC 6 decimals
                "resource": "/v1/ohlcv",
                "description": f"AI Datamarket: {data_type} data",
                "mimeType": "application/json",
                "payTo": X402_PAY_TO_EVM,
                "facilitator": X402_FACILITATOR_URL,
            }
        ],
        "error": "Payment required. Free quota exhausted.",
    }

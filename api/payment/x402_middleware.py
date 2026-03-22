"""
x402 payment middleware — enforces payment for paid data categories.

Flow:
  1. Extract ai_id from query params or X-AI-ID header
  2. For paid endpoints: check token usage vs free quota
  3. If over quota and no PAYMENT-SIGNATURE header, return HTTP 402 with payment requirements
  4. If PAYMENT-SIGNATURE header present, verify via Coinbase facilitator
  5. Otherwise proceed

References:
  - https://github.com/coinbase/x402
  - config/pricing.py for network and address config
"""
import base64
import json
import logging
from typing import Optional

import httpx
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from config.pricing import (
    PAID_FREE_QUOTA,
    X402_DEFAULT_NETWORK,
    X402_FACILITATOR_URL,
    X402_PAY_TO_EVM,
    X402_SUPPORTED_NETWORKS,
    is_free_category,
)

logger = logging.getLogger(__name__)

# Map route prefixes to data categories
ROUTE_CATEGORY_MAP = {
    "/v1/ohlcv": "crypto_ohlcv",
    "/v1/earnings": "stock_financials",
    "/v1/coverage": "crypto_ohlcv",
    "/v1/symbols": "crypto_ohlcv",
}

# Routes that are always free (no payment check)
FREE_ROUTES = {
    "/", "/health", "/docs", "/redoc", "/openapi.json",
    "/v1/symbols", "/v1/coverage", "/v1/earnings/companies",
}


def _get_data_category(path: str) -> Optional[str]:
    """Determine data category from request path."""
    for prefix, category in ROUTE_CATEGORY_MAP.items():
        if path.startswith(prefix):
            return category
    return None


def _get_ai_id(request: Request) -> Optional[str]:
    """Extract AI ID from query params or header."""
    # Try query param first
    ai_id = request.query_params.get("ai_id")
    if ai_id:
        return ai_id
    # Try header
    return request.headers.get("x-ai-id")


def _estimate_token_cost(request: Request) -> int:
    """Estimate token cost for this request based on query params.

    For earnings: detail level × limit (number of filings).
    For other endpoints: 0 (free categories handled elsewhere).
    """
    from config.earnings import DETAIL_TOKEN_COST

    path = request.url.path
    if not path.startswith("/v1/earnings"):
        return 0

    detail = request.query_params.get("detail", "summary")
    limit = request.query_params.get("limit", "4")
    try:
        limit_int = int(limit)
    except ValueError:
        limit_int = 4

    cost_per = DETAIL_TOKEN_COST.get(detail, 1)
    return cost_per * limit_int


def _count_tokens_for_middleware(ai_id: str) -> int:
    """Read token usage from SQLite usage.db."""
    from api.db.usage import count_tokens
    return count_tokens(ai_id)


def _get_payment_header(request: Request) -> Optional[str]:
    """Extract payment header — supports both V2 (PAYMENT-SIGNATURE) and V1 (X-PAYMENT)."""
    header = request.headers.get("payment-signature")
    if header:
        return header
    return request.headers.get("x-payment")


def build_402_response(path: str, data_category: str) -> JSONResponse:
    """Build HTTP 402 response with x402 payment requirements."""
    from config.pricing import get_price_per_m

    price_per_m = get_price_per_m(data_category) or 0.01
    # Estimate tokens for a typical request
    estimated_tokens = 1000
    amount_usdc = (estimated_tokens / 1_000_000) * price_per_m
    # USDC has 6 decimals
    amount_micro = str(int(amount_usdc * 1_000_000))

    body = {
        "x402Version": 1,
        "accepts": [
            {
                "scheme": "exact",
                "network": X402_DEFAULT_NETWORK,
                "maxAmountRequired": amount_micro,
                "resource": path,
                "description": "AI Datamarket: {} data access".format(data_category),
                "mimeType": "application/json",
                "payTo": X402_PAY_TO_EVM,
                "facilitator": X402_FACILITATOR_URL,
            }
        ],
        "error": "Payment required. Free quota of {} tokens exhausted.".format(PAID_FREE_QUOTA),
        "quota": {
            "free_limit": PAID_FREE_QUOTA,
            "note": "Get a zCloak AI ID at https://id.zcloak.ai for free quota",
        },
    }

    # Encode body as Base64 for PAYMENT-REQUIRED header (x402 standard)
    body_bytes = json.dumps(body).encode("utf-8")
    payment_required_b64 = base64.b64encode(body_bytes).decode("ascii")

    return JSONResponse(
        status_code=402,
        content=body,
        headers={"PAYMENT-REQUIRED": payment_required_b64},
    )


async def _verify_payment(payment_header: str, path: str, data_category: str) -> bool:
    """Verify x402 payment via Coinbase facilitator.

    Decodes the Base64 payment payload, reconstructs the payment requirements,
    and sends both to the facilitator's /verify endpoint.
    """
    from config.pricing import get_price_per_m

    try:
        # Decode the payment payload from Base64
        try:
            payload_bytes = base64.b64decode(payment_header)
            payment_payload = json.loads(payload_bytes)
        except Exception:
            # If not Base64, try as raw JSON string
            payment_payload = json.loads(payment_header)

        # Reconstruct payment requirements (what we sent in the 402)
        price_per_m = get_price_per_m(data_category) or 0.01
        estimated_tokens = 1000
        amount_usdc = (estimated_tokens / 1_000_000) * price_per_m
        amount_micro = str(int(amount_usdc * 1_000_000))

        payment_requirements = {
            "x402Version": 1,
            "accepts": [
                {
                    "scheme": "exact",
                    "network": X402_DEFAULT_NETWORK,
                    "maxAmountRequired": amount_micro,
                    "resource": path,
                    "description": "AI Datamarket: {} data access".format(data_category),
                    "mimeType": "application/json",
                    "payTo": X402_PAY_TO_EVM,
                    "facilitator": X402_FACILITATOR_URL,
                }
            ],
        }

        # Send to facilitator for verification
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "{}/verify".format(X402_FACILITATOR_URL),
                json={
                    "x402Version": payment_payload.get("x402Version", 1),
                    "paymentPayload": payment_payload,
                    "paymentRequirements": payment_requirements,
                },
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                is_valid = data.get("valid", False)
                if is_valid:
                    logger.info("x402 payment verified for %s", path)
                return is_valid
            logger.warning("x402 facilitator returned %d: %s", resp.status_code, resp.text)
            # Fail-open: if facilitator is down or returns unexpected status, allow
            # This prevents blocking paying users due to facilitator issues
            if resp.status_code >= 500:
                logger.warning("Facilitator error — allowing payment (fail-open)")
                return True
            return False
    except Exception as e:
        logger.error("x402 payment verification failed: %s", e)
        # Fail-open on network errors
        return True


class X402Middleware(BaseHTTPMiddleware):
    """
    Middleware that enforces x402 payment for requests exceeding free quota.

    For paid data categories:
    - If ai_id has remaining free quota → allow
    - If over quota and PAYMENT-SIGNATURE header present → verify payment → allow/deny
    - If over quota and no payment → return HTTP 402
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Skip free routes
        if path in FREE_ROUTES:
            return await call_next(request)

        # Determine data category
        category = _get_data_category(path)
        if category is None or is_free_category(category):
            return await call_next(request)

        # Paid category — need ai_id
        ai_id = _get_ai_id(request)
        if not ai_id:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "ai_id is required for paid data. Pass as query param or X-AI-ID header.",
                    "get_id": "https://id.zcloak.ai",
                },
            )

        # Check quota (including estimated cost for this request)
        used = _count_tokens_for_middleware(ai_id)
        estimated = _estimate_token_cost(request)
        if used + estimated <= PAID_FREE_QUOTA:
            # Still within free quota — allow
            return await call_next(request)

        # Over quota — check for payment (V2: PAYMENT-SIGNATURE, V1: X-PAYMENT)
        payment_header = _get_payment_header(request)
        if payment_header:
            valid = await _verify_payment(payment_header, path, category)
            if valid:
                return await call_next(request)
            return JSONResponse(
                status_code=402,
                content={"error": "Payment verification failed. Please retry."},
            )

        # No payment — return 402
        return build_402_response(path, category)

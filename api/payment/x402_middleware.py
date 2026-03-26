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

# USDC contract addresses and EIP-712 domain info per network
USDC_INFO = {
    "eip155:42161": {
        "address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "name": "USD Coin",
        "version": "2",
    },
    "eip155:8453": {
        "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "name": "USD Coin",
        "version": "2",
    },
    "eip155:84532": {
        "address": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "name": "USDC",
        "version": "2",
    },
}

# Map route prefixes to data categories
ROUTE_CATEGORY_MAP = {
    "/v1/ohlcv": "crypto_ohlcv",
    "/v1/earnings": "stock_financials",
    "/v1/coverage": "crypto_ohlcv",
    "/v1/symbols": "crypto_ohlcv",
    "/v1/equity": "provider_passthrough",
    "/v1/economy": "provider_passthrough",
    "/v1/fixedincome": "provider_passthrough",
    "/v1/index": "provider_passthrough",
    "/v1/currency": "provider_passthrough",
    "/v1/derivatives": "provider_passthrough",
    "/v1/macro": "provider_passthrough",
    "/v1/energy": "provider_passthrough",
}

# Routes that are always free (no payment check)
FREE_ROUTES = {
    "/", "/health", "/docs", "/redoc", "/openapi.json",
    "/v1/symbols", "/v1/coverage", "/v1/earnings/companies",
    "/v1/auth/register",
    "/v1/equity/available", "/v1/economy/available", "/v1/fixedincome/available",
    "/v1/index/available", "/v1/currency/available", "/v1/derivatives/available",
    "/v1/macro/available", "/v1/energy/available",
}


def _get_data_category(path: str) -> Optional[str]:
    """Determine data category from request path."""
    for prefix, category in ROUTE_CATEGORY_MAP.items():
        if path.startswith(prefix):
            return category
    return None


def _get_ai_id(request: Request) -> Optional[str]:
    """Extract AI ID from Bearer token, query params, or header.

    For paid endpoints, Bearer token is the primary auth method.
    Query param ai_id is still accepted for free-quota checks on free endpoints.
    """
    # 1. Bearer token (preferred — proves identity via on-chain signature)
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        from api.db.usage import get_ai_id_by_token
        token = auth[7:]
        ai_id = get_ai_id_by_token(token)
        if ai_id:
            return ai_id
    # 2. Query param (legacy — will be rejected for paid endpoints below)
    ai_id = request.query_params.get("ai_id")
    if ai_id:
        return ai_id
    # 3. Header fallback
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

    usdc = USDC_INFO.get(X402_DEFAULT_NETWORK, USDC_INFO["eip155:42161"])

    body = {
        "x402Version": 2,
        "accepts": [
            {
                "scheme": "exact",
                "network": X402_DEFAULT_NETWORK,
                "amount": amount_micro,
                "maxTimeoutSeconds": 300,
                "asset": usdc["address"],
                "payTo": X402_PAY_TO_EVM,
                "extra": {
                    "name": usdc["name"],
                    "version": usdc["version"],
                },
            }
        ],
        "resource": {
            "url": path,
            "description": "AI Datamarket: {} data access".format(data_category),
            "mimeType": "application/json",
        },
        "error": "Payment required. Free quota of {} tokens exhausted.".format(PAID_FREE_QUOTA),
    }

    # Encode body as Base64 for PAYMENT-REQUIRED header (x402 V2 standard)
    body_bytes = json.dumps(body).encode("utf-8")
    payment_required_b64 = base64.b64encode(body_bytes).decode("ascii")

    return JSONResponse(
        status_code=402,
        content=body,
        headers={"PAYMENT-REQUIRED": payment_required_b64},
    )


async def _verify_and_settle_payment(payment_header: str, path: str, data_category: str) -> bool:
    """Verify x402 payment signature and settle on-chain.

    1. Decode the Base64 payment payload
    2. Basic validation (check from, to, value, signature present)
    3. Settle on-chain via EIP-3009 transferWithAuthorization
    """
    from api.payment.settle import settle_eip3009

    try:
        # Decode the payment payload from Base64
        try:
            payload_bytes = base64.b64decode(payment_header)
            payment_payload = json.loads(payload_bytes)
        except Exception:
            payment_payload = json.loads(payment_header)

        # Basic validation: check required fields exist
        inner = payment_payload.get("payload", {})
        auth = inner.get("authorization", {})
        signature = inner.get("signature", "")

        if not auth or not signature:
            logger.warning("Payment missing authorization or signature")
            return False

        to_addr = auth.get("to", "").lower()
        if to_addr and to_addr != X402_PAY_TO_EVM.lower():
            logger.warning("Payment to wrong address: %s != %s", to_addr, X402_PAY_TO_EVM)
            return False

        # Settle on-chain
        usdc = USDC_INFO.get(X402_DEFAULT_NETWORK, USDC_INFO["eip155:42161"])
        result = await settle_eip3009(payment_payload, usdc["address"])

        if result["success"]:
            logger.info("Payment settled on-chain: tx=%s for %s", result["tx_hash"], path)
            return True
        else:
            logger.warning("Settlement failed: %s", result.get("error"))
            return False

    except Exception as e:
        logger.error("Payment verification/settlement failed: %s", e)
        return False


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
                    "error": "Authentication required for paid data. Register with on-chain signature to get a bearer token.",
                    "register": "POST /v1/auth/register",
                    "steps": [
                        "1. Sign: zcloak-ai sign agreement \"ai-datamarket-auth:{ai_id}:{timestamp}\"",
                        "2. Register: POST /v1/auth/register with {ai_id, event_id, signed_content}",
                        "3. Use: Authorization: Bearer <token>",
                    ],
                },
            )

        # Check if auth came from Bearer token (required for paid endpoints)
        auth_header = request.headers.get("authorization", "")
        has_bearer = auth_header.startswith("Bearer ")
        if not has_bearer:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Bearer token required for paid data. Query param ai_id is no longer accepted for paid endpoints.",
                    "register": "POST /v1/auth/register",
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
        logger.info("Payment check: ai_id=%s, used=%d, est=%d, has_payment=%s",
                     ai_id, used, estimated, bool(payment_header))
        if payment_header:
            valid = await _verify_and_settle_payment(payment_header, path, category)
            if valid:
                return await call_next(request)
            return JSONResponse(
                status_code=402,
                content={"error": "Payment verification failed. Please retry."},
            )

        # No payment — return 402
        return build_402_response(path, category)

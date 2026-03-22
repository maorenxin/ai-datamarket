"""
zCloak AI ID verification.

Verifies AI IDs via the zCloak credential service at id.zcloak.ai.
Caches results to avoid repeated HTTP calls.
"""
import logging
import time
from typing import Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# Cache: ai_id -> (is_valid, timestamp)
_cache = {}  # type: Dict[str, Tuple[bool, float]]
_CACHE_TTL = 300  # 5 minutes

ZCLOAK_API_BASE = "https://id.zcloak.ai"


async def verify_ai_id(ai_id: str) -> bool:
    """
    Verify a zCloak AI ID is valid and active.

    Checks format, then verifies against zCloak's service.
    Results are cached for 5 minutes.
    Returns True if valid, False otherwise.
    """
    if not ai_id or not isinstance(ai_id, str):
        return False

    # Check cache
    cached = _cache.get(ai_id)
    if cached:
        is_valid, ts = cached
        if time.time() - ts < _CACHE_TTL:
            return is_valid

    # Basic format check — AI IDs are typically DID strings or hex identifiers
    # Accept any non-empty string for now; the HTTP check does the real validation
    ai_id = ai_id.strip()
    if len(ai_id) < 3:
        _cache[ai_id] = (False, time.time())
        return False

    # Verify against zCloak service
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Try the credential/profile endpoint
            resp = await client.get(
                "{}/api/credential/{}".format(ZCLOAK_API_BASE, ai_id),
                headers={"Accept": "application/json"},
            )
            if resp.status_code == 200:
                _cache[ai_id] = (True, time.time())
                return True

            # Also try the DID resolution endpoint
            resp2 = await client.get(
                "{}/api/did/{}".format(ZCLOAK_API_BASE, ai_id),
                headers={"Accept": "application/json"},
            )
            if resp2.status_code == 200:
                _cache[ai_id] = (True, time.time())
                return True

    except Exception as e:
        # If zCloak is unreachable, allow the request (fail-open for MVP)
        logger.warning("zCloak verification failed for %s: %s — allowing (fail-open)", ai_id, e)
        _cache[ai_id] = (True, time.time())
        return True

    # zCloak returned non-200 — ID not found
    logger.info("zCloak AI ID not found: %s", ai_id)
    _cache[ai_id] = (False, time.time())
    return False


def clear_cache():
    """Clear the verification cache (for testing)."""
    _cache.clear()

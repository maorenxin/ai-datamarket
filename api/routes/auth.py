"""
Auth routes — on-chain signature registration for bearer tokens.

POST /auth/register — submit zCloak on-chain signature event_id, get bearer token
"""
import logging
import secrets
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.auth.zcloak import verify_onchain_signature, verify_ai_id_exists
from api.db.usage import save_token, revoke_tokens

logger = logging.getLogger(__name__)
router = APIRouter()

AUTH_MESSAGE_PREFIX = "ai-datamarket-auth:"
TIMESTAMP_WINDOW = 600  # 10 minutes


class RegisterRequest(BaseModel):
    ai_id: str
    event_id: str
    signed_content: str


@router.post("/auth/register")
async def register(body: RegisterRequest):
    """
    Register an AI ID — submit on-chain signature event_id to get a bearer token.

    The agent must have previously signed a message on-chain:
        zcloak-ai sign agreement "ai-datamarket-auth:{ai_id}:{timestamp}"

    Submit the signed_content and event_id here to receive a bearer token.
    """
    ai_id = body.ai_id.strip()
    event_id = body.event_id.strip()
    signed_content = body.signed_content.strip()

    # 1. Validate signed_content format
    if not signed_content.startswith(AUTH_MESSAGE_PREFIX):
        raise HTTPException(
            status_code=400,
            detail="signed_content must start with '{}'".format(AUTH_MESSAGE_PREFIX),
        )

    # Parse: "ai-datamarket-auth:{ai_id}:{timestamp}"
    parts = signed_content[len(AUTH_MESSAGE_PREFIX):].rsplit(":", 1)
    if len(parts) != 2:
        raise HTTPException(
            status_code=400,
            detail="signed_content format: 'ai-datamarket-auth:{ai_id}:{timestamp}'",
        )

    content_ai_id, content_ts = parts[0], parts[1]

    # 2. Check ai_id in message matches claimed ai_id
    if content_ai_id != ai_id:
        raise HTTPException(
            status_code=403,
            detail="ai_id in signed_content ('{}') does not match claimed ai_id ('{}')".format(
                content_ai_id, ai_id
            ),
        )

    # 3. Check timestamp is within window
    try:
        ts = int(content_ts)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid timestamp in signed_content",
        )

    now = int(time.time())
    if abs(now - ts) > TIMESTAMP_WINDOW:
        raise HTTPException(
            status_code=403,
            detail="Timestamp expired. Must be within {} seconds. now={}, got={}".format(
                TIMESTAMP_WINDOW, now, ts
            ),
        )

    # 4. Verify on-chain signature via CLI
    ok, msg, signer_id = verify_onchain_signature(signed_content)
    if not ok:
        raise HTTPException(
            status_code=403,
            detail="On-chain signature verification failed: {}".format(msg),
        )

    # 5. If we parsed a signer_id, confirm it matches the claimed ai_id
    if signer_id and signer_id != ai_id:
        raise HTTPException(
            status_code=403,
            detail="Signer '{}' does not match claimed ai_id '{}'".format(signer_id, ai_id),
        )

    # 6. Revoke old tokens for this ai_id (one active token per identity)
    revoked = revoke_tokens(ai_id)
    if revoked:
        logger.info("Revoked %d old token(s) for ai_id=%s", revoked, ai_id)

    # 7. Generate bearer token
    token = secrets.token_hex(32)
    save_token(token, ai_id, event_id)

    logger.info("Registered ai_id=%s with event_id=%s", ai_id, event_id[:16])

    return {
        "token": token,
        "ai_id": ai_id,
        "expires": None,
        "message": "Bearer token issued. Use header: Authorization: Bearer {}".format(token),
    }

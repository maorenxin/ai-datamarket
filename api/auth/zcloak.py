"""
zCloak AI ID verification — stub for MVP.

Full implementation will:
  1. Fetch https://id.zcloak.ai/profile/{ai_id}
  2. Verify DID credential signature
  3. Return whether the AI ID is valid and active
"""


async def verify_ai_id(ai_id: str) -> bool:
    """
    Verify a zCloak AI ID.
    MVP: always returns True (pass-through).
    Production: verify DID/credential signature with zCloak.
    """
    # TODO: implement zCloak AI ID verification
    # resp = await httpx.get(f"https://id.zcloak.ai/profile/{ai_id}")
    # return resp.status_code == 200 and verify_signature(resp.json())
    return True

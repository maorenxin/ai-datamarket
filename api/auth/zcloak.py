"""
zCloak AI ID verification — on-chain signature via CLI.

Two functions:
  - verify_onchain_signature: verify a signed message via `zcloak-ai verify message`
  - verify_ai_id_exists: check if an AI ID is registered on-chain
"""
import json
import logging
import subprocess
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

CLI_TIMEOUT = 30  # seconds


def _run_cli(args: list) -> Tuple[int, str, str]:
    """Run a zcloak-ai CLI command. Returns (returncode, stdout, stderr)."""
    cmd = ["zcloak-ai"] + args
    logger.info("Running CLI: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=CLI_TIMEOUT
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return -1, "", "zcloak-ai CLI not found. Install: npm install -g @zcloak/ai-agent@latest"
    except subprocess.TimeoutExpired:
        return -2, "", "zcloak-ai CLI timed out after {}s".format(CLI_TIMEOUT)


def verify_onchain_signature(signed_content: str) -> Tuple[bool, str, Optional[str]]:
    """
    Verify a signed message on-chain via `zcloak-ai verify message`.

    Returns (success, message, signer_ai_id).
    On success, signer_ai_id is the principal that signed the message.
    """
    rc, stdout, stderr = _run_cli(["verify", "message", signed_content])

    if rc != 0:
        err = stderr or stdout or "CLI returned code {}".format(rc)
        logger.warning("verify message failed: %s", err)
        return False, err, None

    # Parse output — look for verification result
    # Expected output contains signer principal and verification status
    lines = stdout.split("\n")
    signer_id = None
    verified = False

    for line in lines:
        lower = line.lower()
        # Look for signer/principal info
        if "principal" in lower or "signer" in lower:
            # Extract the ID value — typically after a colon or equals
            for sep in [":", "="]:
                if sep in line:
                    val = line.split(sep, 1)[1].strip()
                    if val:
                        signer_id = val
                        break
        # Look for verification success indicators
        if "verified" in lower or "valid" in lower or "success" in lower:
            if "not" not in lower and "invalid" not in lower and "fail" not in lower:
                verified = True

    if verified and signer_id:
        return True, "Signature verified", signer_id
    elif verified:
        # Verified but couldn't parse signer — still treat as success
        # The caller should do additional checks
        return True, "Signature verified (signer not parsed from output)", None
    else:
        return False, "Signature verification failed. Output: {}".format(stdout[:200]), None


def verify_ai_id_exists(ai_id: str) -> bool:
    """Check if an AI ID (principal) is registered on-chain."""
    rc, stdout, stderr = _run_cli(["register", "lookup-by-principal", ai_id])
    if rc != 0:
        logger.info("AI ID lookup failed for %s: %s", ai_id, stderr or stdout)
        return False
    # Non-empty stdout with no error = exists
    return bool(stdout)

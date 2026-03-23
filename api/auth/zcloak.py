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

    # Parse output — zcloak-ai verify message returns Candid record + signer info
    # Success: stdout contains the event record with ai_id and "Agent Principal: ..."
    # Failure: non-zero exit code (handled above)
    lines = stdout.split("\n")
    signer_id = None

    for line in lines:
        stripped = line.strip()
        # "Agent Principal: a5mqj-eyzt5-..." in signer info section
        if stripped.startswith("Agent Principal:"):
            signer_id = stripped.split(":", 1)[1].strip()
            break
        # Fallback: ai_id = "..." in Candid record
        if stripped.startswith('ai_id = "') and not signer_id:
            signer_id = stripped.split('"')[1]

    if signer_id:
        return True, "Signature verified", signer_id
    elif stdout:
        # CLI returned 0 with output but we couldn't parse signer
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

"""
Self-settlement for x402 EIP-3009 payments on Arbitrum.

When the public x402 facilitator doesn't support our network, we settle
payments ourselves by calling USDC's transferWithAuthorization on-chain.

The EIP-3009 signature authorizes a gasless USDC transfer. Anyone can
submit it — the gas is paid by the submitter, USDC moves from signer to payTo.
"""
import json
import logging
import os

from eth_account import Account
from eth_abi import encode

logger = logging.getLogger(__name__)

# Arbitrum One RPC
ARBITRUM_RPC = "https://arb1.arbitrum.io/rpc"

# USDC transferWithAuthorization function selector
# transferWithAuthorization(address from, address to, uint256 value,
#   uint256 validAfter, uint256 validBefore, bytes32 nonce, uint8 v, bytes32 r, bytes32 s)
TRANSFER_WITH_AUTH_SELECTOR = "0xe3ee160e"


def _get_settler_key() -> str:
    """Get private key for the settlement wallet (pays gas)."""
    # Try dedicated settler key first, then fall back to WEB3_PRIVATE_KEY
    key = os.environ.get("SETTLER_PRIVATE_KEY") or os.environ.get("WEB3_PRIVATE_KEY")
    if not key:
        raise ValueError("No SETTLER_PRIVATE_KEY or WEB3_PRIVATE_KEY configured")
    return key


def _split_signature(sig_hex: str) -> tuple:
    """Split a hex signature into v, r, s components."""
    sig = bytes.fromhex(sig_hex.replace("0x", ""))
    if len(sig) != 65:
        raise ValueError("Invalid signature length: {}".format(len(sig)))
    r = sig[:32]
    s = sig[32:64]
    v = sig[64]
    # Handle EIP-155 v values
    if v < 27:
        v += 27
    return v, r, s


async def settle_eip3009(payment_payload: dict, usdc_address: str) -> dict:
    """Settle an EIP-3009 transferWithAuthorization on-chain.

    Args:
        payment_payload: Decoded x402 payment payload containing the authorization.
        usdc_address: USDC contract address on the target network.

    Returns:
        dict with tx_hash and status.
    """
    import httpx

    try:
        # Extract authorization from payload
        # V2 format: payload.payload.authorization + payload.payload.signature
        inner = payment_payload.get("payload", {})
        auth = inner.get("authorization", {})
        signature = inner.get("signature", "")

        if not auth or not signature:
            return {"success": False, "error": "Missing authorization or signature in payload"}

        from_addr = auth.get("from") or auth.get("fromAddress") or auth.get("from_address")
        to_addr = auth.get("to")
        value = int(auth.get("value", "0"))
        valid_after = int(auth.get("validAfter", auth.get("valid_after", "0")))
        valid_before = int(auth.get("validBefore", auth.get("valid_before", "0")))
        nonce = auth.get("nonce", "0x" + "00" * 32)

        if not from_addr or not to_addr:
            return {"success": False, "error": "Missing from/to in authorization"}

        # Split signature
        v, r, s = _split_signature(signature)

        # Ensure nonce is bytes32
        if isinstance(nonce, str):
            nonce_bytes = bytes.fromhex(nonce.replace("0x", "").zfill(64))
        else:
            nonce_bytes = nonce.to_bytes(32, "big")

        # Encode calldata for transferWithAuthorization
        from eth_utils import to_checksum_address
        calldata = bytes.fromhex(TRANSFER_WITH_AUTH_SELECTOR[2:]) + encode(
            ["address", "address", "uint256", "uint256", "uint256", "bytes32", "uint8", "bytes32", "bytes32"],
            [
                to_checksum_address(from_addr),
                to_checksum_address(to_addr),
                value,
                valid_after,
                valid_before,
                nonce_bytes,
                v,
                r,
                s,
            ]
        )

        # Get settler account
        settler_key = _get_settler_key()
        settler = Account.from_key(settler_key)
        logger.info("Settling payment: %s -> %s, %d USDC micro, settler=%s",
                     from_addr, to_addr, value, settler.address)

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get nonce for settler
            nonce_resp = await client.post(ARBITRUM_RPC, json={
                "jsonrpc": "2.0",
                "method": "eth_getTransactionCount",
                "params": [settler.address, "latest"],
                "id": 1,
            })
            tx_nonce = int(nonce_resp.json()["result"], 16)

            # Get gas price (use maxFeePerGas with buffer for Arbitrum EIP-1559)
            gas_resp = await client.post(ARBITRUM_RPC, json={
                "jsonrpc": "2.0",
                "method": "eth_gasPrice",
                "params": [],
                "id": 2,
            })
            gas_price = int(gas_resp.json()["result"], 16)
            # Add 50% buffer to avoid "base fee too low" on Arbitrum
            max_fee = int(gas_price * 1.5)
            max_priority_fee = 100000000  # 0.1 gwei tip

            # Build EIP-1559 transaction
            tx = {
                "to": to_checksum_address(usdc_address),
                "data": calldata,
                "gas": 150000,
                "maxFeePerGas": max_fee,
                "maxPriorityFeePerGas": max_priority_fee,
                "nonce": tx_nonce,
                "chainId": 42161,
                "value": 0,
                "type": 2,  # EIP-1559
            }

            # Sign and send
            signed = settler.sign_transaction(tx)
            raw_tx = "0x" + signed.raw_transaction.hex()

            send_resp = await client.post(ARBITRUM_RPC, json={
                "jsonrpc": "2.0",
                "method": "eth_sendRawTransaction",
                "params": [raw_tx],
                "id": 3,
            })
            result = send_resp.json()

            if "error" in result:
                logger.error("Settlement tx failed: %s", result["error"])
                return {"success": False, "error": result["error"].get("message", str(result["error"]))}

            tx_hash = result.get("result", "")
            logger.info("Settlement tx sent: %s", tx_hash)
            return {"success": True, "tx_hash": tx_hash}

    except Exception as e:
        logger.error("Settlement failed: %s", e)
        return {"success": False, "error": str(e)}

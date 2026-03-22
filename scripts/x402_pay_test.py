#!/usr/bin/env python3
"""x402 payment test — run with: uv run --python 3.13 scripts/x402_pay_test.py <URL>"""
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "x402[httpx,evm]",
#     "eth-account",
#     "python-dotenv",
#     "web3",
# ]
# ///

import asyncio
import json
import os
import sys

from dotenv import load_dotenv
from eth_account import Account

from x402 import x402Client
from x402.http import x402HTTPClient
from x402.http.clients import x402HttpxClient
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.register import register_exact_evm_client

# Load key from ~/.openclaw/.env
load_dotenv(os.path.expanduser("~/.openclaw/.env"))


async def main():
    pk = os.environ.get("EVM_PRIVATE_KEY") or os.environ.get("WEB3_PRIVATE_KEY")
    if not pk:
        print("Error: No EVM_PRIVATE_KEY or WEB3_PRIVATE_KEY found")
        sys.exit(1)

    url = sys.argv[1] if len(sys.argv) > 1 else (
        "http://localhost:8402/v1/earnings?ticker=AAPL&detail=full&limit=1&ai_id=test-pay-e2e"
    )

    client = x402Client()
    account = Account.from_key(pk)
    register_exact_evm_client(client, EthAccountSigner(account))
    print(f"Wallet: {account.address}")
    print(f"Request: {url}\n")

    http_client = x402HTTPClient(client)

    async with x402HttpxClient(client) as http:
        response = await http.get(url)
        await response.aread()

        print(f"Status: {response.status_code}")

        try:
            data = response.json()
            # Print without full_text to keep output manageable
            display = {k: v for k, v in data.items() if k != "filings"}
            if "filings" in data:
                display["filings_count"] = len(data["filings"])
            print(f"Response: {json.dumps(display, indent=2, ensure_ascii=False)}")
        except Exception:
            print(f"Response: {response.text[:500]}")

        # Check for payment settlement
        try:
            settle = http_client.get_payment_settle_response(
                lambda name: response.headers.get(name)
            )
            print(f"\nPayment settled: {settle.model_dump_json(indent=2)}")
        except (ValueError, Exception):
            pass


if __name__ == "__main__":
    asyncio.run(main())

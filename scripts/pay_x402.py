#!/usr/bin/env python3
"""
x402 payment helper — make paid API requests with automatic payment.

Usage:
    # Set your EVM private key (Arbitrum wallet with USDC)
    export EVM_PRIVATE_KEY=0xYourPrivateKeyHere

    # Query with automatic payment on 402
    python3 scripts/pay_x402.py "http://localhost:8402/v1/earnings?ticker=AAPL&detail=summary&ai_id=YOUR_AI_ID&limit=1"

Requires: pip install x402[httpx] eth-account python-dotenv
"""
import asyncio
import json
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from eth_account import Account
    from x402 import x402Client
    from x402.http import x402HTTPClient
    from x402.http.clients import x402HttpxClient
    from x402.mechanisms.evm import EthAccountSigner
    from x402.mechanisms.evm.exact.register import register_exact_evm_client
except ImportError:
    print("Missing dependencies. Install with:")
    print("  pip install x402[httpx] eth-account python-dotenv")
    sys.exit(1)


async def main():
    # Get private key from env
    private_key = os.getenv("EVM_PRIVATE_KEY")
    if not private_key:
        print("Error: EVM_PRIVATE_KEY environment variable not set.")
        print("Export your Arbitrum wallet private key (must have USDC balance):")
        print("  export EVM_PRIVATE_KEY=0x...")
        sys.exit(1)

    # Get URL from args
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/pay_x402.py <URL>")
        print('Example: python3 scripts/pay_x402.py "http://localhost:8402/v1/earnings?ticker=AAPL&detail=summary&ai_id=test&limit=1"')
        sys.exit(1)

    url = sys.argv[1]

    # Create x402 client with EVM payment
    client = x402Client()
    account = Account.from_key(private_key)
    register_exact_evm_client(client, EthAccountSigner(account))
    print("Wallet: {}".format(account.address))
    print("Request: {}\n".format(url))

    # Create HTTP client helper for payment response extraction
    http_client = x402HTTPClient(client)

    # Make request — x402HttpxClient auto-handles 402 → sign → retry
    async with x402HttpxClient(client) as http:
        response = await http.get(url)
        await response.aread()

        print("Status: {}".format(response.status_code))

        # Pretty-print JSON response
        try:
            data = response.json()
            print("Response:\n{}".format(json.dumps(data, indent=2, ensure_ascii=False)))
        except Exception:
            print("Response: {}".format(response.text[:2000]))

        # Check for payment settlement response
        try:
            settle_response = http_client.get_payment_settle_response(
                lambda name: response.headers.get(name)
            )
            print("\nPayment settled: {}".format(settle_response.model_dump_json(indent=2)))
        except (ValueError, Exception):
            if response.status_code == 200:
                print("\n(No payment needed — within free quota or payment already processed)")


if __name__ == "__main__":
    asyncio.run(main())

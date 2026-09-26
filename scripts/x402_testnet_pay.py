"""Make ONE real x402 payment to Waveline's /api/insight.

Base Sepolia (testnet) by default. `--mainnet` switches to Base mainnet for
the first REAL-money payment; paying there additionally requires
`--confirm-real-money`. Written against the pinned x402==2.24.0.
Safety rules (from Nikos's production experience, 26 Sep 2026):

1. Only the expected network is accepted (eip155:84532 Base Sepolia, or with
   --mainnet eip155:8453 Base); anything else aborts.
2. Amount cap: aborts without paying if the 402 asks for more than 0.01 USDC.
   Also requires that network's known USDC contract and scheme "exact".
3. Exactly one paid request. No retries: on any failure it prints and stops.
4. Prints the HTTP status, the response body and the decoded PAYMENT-RESPONSE
   header (tx hash), plus the basescan link to check it.
5. The test wallet's private key is read from the X402_TEST_PAYER_KEY env
   var or from a local file (--key-file). Never in code, never in a commit,
   never printed. Use a separate TEST wallet holding only what the test needs.

Default is a DRY RUN: it fetches and checks the 402 but does not sign or pay.
Add --pay to make the single real payment.

Setup (once, on your own machine):
    pip install "x402[evm,requests]==2.24.0"

Usage:
    python scripts/x402_testnet_pay.py --artist Radiohead            # dry run
    X402_TEST_PAYER_KEY=0x... python scripts/x402_testnet_pay.py --artist Radiohead --pay
    python scripts/x402_testnet_pay.py --artist Radiohead --pay --key-file ~/.x402-test-key
    python scripts/x402_testnet_pay.py --mainnet --artist Radiohead --pay --confirm-real-money --key-file ~/.x402-test-key
"""

import argparse
import base64
import json
import os
import sys
from urllib.parse import quote

EXPECTED_NETWORK = "eip155:84532"                               # Base Sepolia
EXPECTED_ASSET = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"   # USDC on Base Sepolia
BASESCAN_TX = "https://sepolia.basescan.org/tx/"
MAINNET_NETWORK = "eip155:8453"                                  # Base
MAINNET_ASSET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"     # USDC on Base
MAINNET_BASESCAN_TX = "https://basescan.org/tx/"
EXPECTED_SCHEME = "exact"
MAX_ATOMIC_AMOUNT = 10_000                                       # 0.01 USDC (6 decimals)
TIMEOUT_SECONDS = 30


class Abort(Exception):
    """Stop without paying."""


def select_requirement(accepts, expected_pay_to=None, network=EXPECTED_NETWORK, asset=EXPECTED_ASSET):
    """Return the single acceptable requirement (as a dict) or raise Abort.

    Pure function so it can be unit-tested without a network or a key.
    """
    if not accepts:
        raise Abort("402 has no payment options")
    candidates = []
    for option in accepts:
        if option.get("network") != network:
            continue
        if option.get("scheme") != EXPECTED_SCHEME:
            continue
        if (option.get("asset") or "").lower() != asset.lower():
            continue
        candidates.append(option)
    if not candidates:
        offered = [(o.get("scheme"), o.get("network"), o.get("asset")) for o in accepts]
        raise Abort(f"no option on {network} / exact / USDC {asset}; offered: {offered}")
    option = candidates[0]
    try:
        amount = int(option.get("amount"))
    except (TypeError, ValueError):
        raise Abort(f"unreadable amount: {option.get('amount')!r}")
    if amount <= 0 or amount > MAX_ATOMIC_AMOUNT:
        raise Abort(f"amount {amount} atomic units is outside (0, {MAX_ATOMIC_AMOUNT}] (max 0.01 USDC)")
    if expected_pay_to and (option.get("payTo") or "").lower() != expected_pay_to.lower():
        raise Abort(f"payTo {option.get('payTo')} does not match --expect-pay-to {expected_pay_to}")
    return option


def _load_key(key_file):
    if key_file:
        with open(os.path.expanduser(key_file)) as fh:
            key = fh.read().strip()
    else:
        key = (os.getenv("X402_TEST_PAYER_KEY") or "").strip()
    if not key:
        raise Abort("no private key: set X402_TEST_PAYER_KEY or pass --key-file")
    return key


def _print_response(resp):
    print(f"HTTP status: {resp.status_code}")
    try:
        print("Body:", json.dumps(resp.json(), indent=2, ensure_ascii=False))
    except ValueError:
        print("Body (raw):", resp.text[:2000])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="https://wearewaveline.com")
    parser.add_argument("--artist", required=True)
    parser.add_argument("--expect-pay-to", help="abort unless payTo equals this address")
    parser.add_argument("--key-file", help="local file containing the TEST wallet private key")
    parser.add_argument("--pay", action="store_true", help="actually sign and send the one payment")
    parser.add_argument("--mainnet", action="store_true", help="Base mainnet (REAL USDC) instead of Base Sepolia")
    parser.add_argument("--confirm-real-money", action="store_true",
                        help="required together with --mainnet --pay")
    args = parser.parse_args(argv)

    if args.mainnet:
        network, asset, explorer = MAINNET_NETWORK, MAINNET_ASSET, MAINNET_BASESCAN_TX
        if args.pay and not args.confirm_real_money:
            raise Abort("--mainnet --pay spends REAL USDC; add --confirm-real-money to proceed")
        print("MAINNET MODE: real USDC on Base")
    else:
        network, asset, explorer = EXPECTED_NETWORK, EXPECTED_ASSET, BASESCAN_TX

    import requests
    from x402.http import (
        decode_payment_required_header,
        decode_payment_response_header,
        encode_payment_signature_header,
    )

    url = f"{args.base_url.rstrip('/')}/api/insight?artist={quote(args.artist)}"
    print(f"GET {url}  (unpaid)")
    first = requests.get(url, timeout=TIMEOUT_SECONDS)
    if first.status_code != 402:
        _print_response(first)
        raise Abort(f"expected 402, got {first.status_code}")

    header = first.headers.get("PAYMENT-REQUIRED")
    if not header:
        raise Abort("402 without PAYMENT-REQUIRED header")
    required_from_header = json.loads(base64.b64decode(header))
    try:
        body_matches = first.json() == required_from_header
    except ValueError:
        body_matches = False
    print(f"402 body mirrors PAYMENT-REQUIRED header: {body_matches}")
    print(f"resource.url: {required_from_header.get('resource', {}).get('url')}")

    option = select_requirement(required_from_header.get("accepts") or [], args.expect_pay_to,
                                network=network, asset=asset)
    print(f"Accepted option: {option['amount']} atomic USDC ({int(option['amount']) / 1_000_000} USDC) "
          f"on {option['network']} to {option['payTo']}")

    if not args.pay:
        print("DRY RUN: checks passed, nothing signed or paid. Re-run with --pay to make the one payment.")
        return 0

    from eth_account import Account
    from x402 import x402ClientSync
    from x402.mechanisms.evm.exact import ExactEvmScheme
    from x402.mechanisms.evm.signers import EthAccountSigner

    account = Account.from_key(_load_key(args.key_file))
    print(f"Payer (test wallet): {account.address}")

    client = x402ClientSync()
    client.register(network, ExactEvmScheme(EthAccountSigner(account)))  # this network only
    client.set_spend_controls({"max_amount_per_payment": "$0.01"})

    payment_required = decode_payment_required_header(header)
    # Sign exactly the option we vetted, nothing else the server offered.
    payment_required = payment_required.model_copy(update={
        "accepts": [a for a in payment_required.accepts
                    if a.network == option["network"] and a.pay_to == option["payTo"]
                    and str(a.amount) == str(option["amount"])][:1],
    })
    if not payment_required.accepts:
        raise Abort("vetted option vanished after decoding; not paying")
    payload = client.create_payment_payload(payment_required)

    print(f"GET {url}  (paid, single attempt, no retry)")
    paid = requests.get(url, headers={"PAYMENT-SIGNATURE": encode_payment_signature_header(payload)},
                        timeout=TIMEOUT_SECONDS)
    _print_response(paid)

    receipt = paid.headers.get("PAYMENT-RESPONSE")
    if receipt:
        settle = decode_payment_response_header(receipt)
        print("PAYMENT-RESPONSE:", settle.model_dump_json(indent=2))
        if settle.transaction:
            print(f"Check on explorer: {explorer}{settle.transaction}")
    else:
        print("No PAYMENT-RESPONSE header (not settled).")
    return 0 if paid.status_code == 200 and receipt else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Abort as reason:
        print(f"ABORTED, nothing paid: {reason}")
        sys.exit(2)

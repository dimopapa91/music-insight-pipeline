"""Agent API: pay-per-request access to Waveline's AI artist insights (x402).

An AI agent calls GET /api/insight?artist=<name>. Without payment it gets an
HTTP 402 describing the price; its x402 client pays automatically (USDC) and
retries with a PAYMENT-SIGNATURE header; the x402 middleware verifies the
payment with the facilitator, runs the handler below, and only settles the
payment if the handler answered with a non-error status.

Deliberate constraints (agreed 26 Sep 2026, see the Notion Work Log):

* The server only ever holds the PUBLIC receiving address (X402_PAY_TO).
  No private key, no signing: the facilitator moves the funds.
* It serves insights that already exist in the `searches` table. It never
  runs the pipeline and never calls Claude, so a paid request can't be
  turned into unbounded AI spend (same principle as the anonymous-abuse fix
  in PR #19).
* Unknown artist -> 404. The Flask x402 middleware cancels settlement for
  any status >= 400, so the agent is not charged.
* The response carries only our own AI-written text, never Spotify/Last.fm
  numbers (their terms don't allow reselling their data).
* The route never redirects: a 3xx is not an error status, so it would be
  settled while the agent might not receive the content.

Everything is off unless X402_PAY_TO holds a valid 0x address. When it's
off, the route answers 404 so the content can never be served for free.
Phase 1 defaults to Base Sepolia (testnet) and the public x402.org
facilitator; mainnet + the Coinbase CDP facilitator (for Bazaar listing) is
phase 2 and only needs the X402_* env vars changed.
"""

import base64
import json
import logging
import os
import re

from flask import Blueprint, current_app, jsonify, request

from db import db_cursor

agent_api_bp = Blueprint("agent_api", __name__)

INSIGHT_PATH = "/api/insight"
PREVIEW_PATH = "/api/insight/preview"
PREVIEW_ARTIST = "Radiohead"  # fixed, real sample (verified present in the live DB, 26 Sep 2026)

DEFAULT_NETWORK = "eip155:84532"  # Base Sepolia (testnet)
DEFAULT_FACILITATOR_URL = "https://x402.org/facilitator"
DEFAULT_PRICE = "$0.005"

_EVM_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
_MAX_ARTIST_LEN = 200

SOURCE = "Waveline (https://wearewaveline.com), AI-written analysis generated with Anthropic Claude"


def _build_routes(pay_to, network, price):
    from x402.http import PaymentOption, RouteConfig

    return {
        f"GET {INSIGHT_PATH}": RouteConfig(
            accepts=PaymentOption(scheme="exact", pay_to=pay_to, price=price, network=network),
            description=(
                "Waveline artist insight: an AI-written analysis of an artist's "
                "sound and appeal, from Waveline's database of ~10,900 artists. "
                "Query: ?artist=<name>. Unknown artists return 404 and are not charged. "
                f"Free preview: GET {PREVIEW_PATH}"
            ),
            mime_type="application/json",
        )
    }


def init_x402(app, facilitator=None):
    """Install the x402 payment middleware if X402_PAY_TO is configured.

    Returns True when the paid endpoint is live. Must be called BEFORE
    ProxyFix wraps app.wsgi_app, so ProxyFix stays the outermost layer and
    x402 sees the real https scheme/host.

    `facilitator` is injectable for tests; production builds an HTTP client.
    """
    app.config["X402_ENABLED"] = False

    pay_to = (os.getenv("X402_PAY_TO") or "").strip()
    if not pay_to:
        return False
    if not _EVM_ADDRESS_RE.fullmatch(pay_to):
        logging.error("x402 disabled: X402_PAY_TO is not a valid 0x address")
        return False

    network = (os.getenv("X402_NETWORK") or DEFAULT_NETWORK).strip()
    facilitator_url = (os.getenv("X402_FACILITATOR_URL") or DEFAULT_FACILITATOR_URL).strip()
    price = (os.getenv("X402_PRICE") or DEFAULT_PRICE).strip()

    try:
        from x402 import x402ResourceServerSync
        from x402.http import HTTPFacilitatorClientSync, is_fatal_startup_init_error, x402HTTPResourceServerSync
        from x402.http.middleware.flask import payment_middleware
        from x402.mechanisms.evm.exact import ExactEvmServerScheme
    except ImportError:
        logging.exception("x402 disabled: the x402 package is not installed")
        return False

    if facilitator is None:
        facilitator = HTTPFacilitatorClientSync({"url": facilitator_url})

    server = x402ResourceServerSync(facilitator)
    server.register(network, ExactEvmServerScheme())
    routes = _build_routes(pay_to, network, price)

    # Preflight. The middleware's own startup initialize() calls os._exit(1)
    # on a permanent misconfiguration (e.g. a network the facilitator doesn't
    # support). That must never take the whole site down, so catch those
    # here first and just leave the paid endpoint off. Transient facilitator
    # outages are fine: the middleware retries on the first paid request.
    try:
        x402HTTPResourceServerSync(server, routes).initialize()
    except Exception as error:
        if is_fatal_startup_init_error(error):
            logging.error("x402 disabled: facilitator/route misconfiguration: %s", error)
            return False
        logging.warning("x402: facilitator unreachable at startup (%s); will retry on first paid request",
                        type(error).__name__)

    payment_middleware(app, routes, server)
    # Outside the x402 middleware (and inside ProxyFix): copy the v2
    # PAYMENT-REQUIRED header into the 402 body for v1-style agents.
    app.wsgi_app = PaymentRequiredBodyFill(app.wsgi_app)
    app.config["X402_ENABLED"] = True
    app.config["X402_NETWORK"] = network
    app.config["X402_PRICE"] = price
    logging.info("x402 enabled on %s (network=%s, price=%s)", INSIGHT_PATH, network, price)
    return True


class PaymentRequiredBodyFill:
    """WSGI wrapper: make the 402 readable by v1 AND v2 x402 clients.

    The x402 v2 middleware puts the payment requirements only in the
    base64 PAYMENT-REQUIRED header and sends `{}` as the body. Plenty of
    agents in the wild are still v1 and read only the JSON body; with an
    empty body they simply don't pay (production experience shared by Nikos,
    26 Sep 2026). When a 402 on the insight route has an empty body, decode
    the header and send it as the JSON body too. Anything else passes
    through untouched, and every other path skips this wrapper entirely.
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        if environ.get("PATH_INFO") != INSIGHT_PATH:
            return self.wsgi_app(environ, start_response)

        state = {"written": []}

        def capture(status, headers, exc_info=None):
            state["status"], state["headers"], state["exc_info"] = status, list(headers), exc_info
            return state["written"].append

        result = self.wsgi_app(environ, capture)
        try:
            chunks = list(result)
        finally:
            if hasattr(result, "close"):
                result.close()

        body = b"".join(state["written"]) + b"".join(chunks)
        status, headers = state["status"], state["headers"]
        if status.startswith("402"):
            body, headers = _fill_402_body(body, headers)
        start_response(status, headers, state["exc_info"])
        return [body]


def _fill_402_body(body, headers):
    if body.strip() not in (b"", b"{}"):
        return body, headers
    encoded = next((v for k, v in headers if k.lower() == "payment-required"), None)
    if not encoded:
        return body, headers
    try:
        decoded = json.loads(base64.b64decode(encoded))
    except Exception:
        return body, headers
    new_body = json.dumps(decoded).encode("utf-8")
    kept = [(k, v) for k, v in headers if k.lower() not in ("content-type", "content-length")]
    kept += [("Content-Type", "application/json"), ("Content-Length", str(len(new_body)))]
    return new_body, kept


def _latest_insight(artist):
    """Newest non-empty insight for this artist, or None. Read-only."""
    with db_cursor() as cur:
        cur.execute("""
            SELECT artist_name, claude_insight, searched_at
            FROM searches
            WHERE LOWER(artist_name) = LOWER(%s)
              AND claude_insight IS NOT NULL AND claude_insight <> ''
            ORDER BY searched_at DESC LIMIT 1
        """, (artist,))
        return cur.fetchone()


def _no_store(response, status=200):
    response.status_code = status
    response.headers["Cache-Control"] = "private, no-store"
    return response


@agent_api_bp.route(INSIGHT_PATH)
def insight():
    # Never serve the paid content when the middleware isn't in front of it.
    if not current_app.config.get("X402_ENABLED"):
        return _no_store(jsonify({"error": "not_found"}), 404)

    artist = (request.args.get("artist") or "").strip()
    if not artist or len(artist) > _MAX_ARTIST_LEN:
        return _no_store(jsonify({
            "error": "missing_artist",
            "message": "Pass the artist name as ?artist=<name>. You were not charged.",
        }), 400)

    try:
        row = _latest_insight(artist)
    except Exception:
        logging.exception("agent_api: insight lookup failed")
        return _no_store(jsonify({
            "error": "temporarily_unavailable",
            "message": "Lookup failed. You were not charged.",
        }), 503)

    if row is None:
        return _no_store(jsonify({
            "error": "artist_not_found",
            "message": "Waveline has no insight for this artist. You were not charged.",
        }), 404)

    return _no_store(jsonify(_insight_body(row)))


def _insight_body(row):
    name, text, searched_at = row
    generated_at = searched_at.isoformat() if hasattr(searched_at, "isoformat") else str(searched_at)
    return {
        "artist": name,
        "insight": text,
        "generated_at": generated_at,
        "source": SOURCE,
    }


@agent_api_bp.route(PREVIEW_PATH)
def insight_preview():
    """Free, unpaid sample: the real insight for one fixed artist, in exactly
    the paid response's shape plus pointers to the paid endpoint. Catalogs
    and agents often check a free preview before paying. Not payment-gated:
    the x402 route pattern matches only the exact /api/insight path."""
    if not current_app.config.get("X402_ENABLED"):
        return _no_store(jsonify({"error": "not_found"}), 404)
    try:
        row = _latest_insight(PREVIEW_ARTIST)
    except Exception:
        logging.exception("agent_api: preview lookup failed")
        return _no_store(jsonify({"error": "temporarily_unavailable"}), 503)
    if row is None:
        return _no_store(jsonify({"error": "preview_unavailable"}), 404)
    body = _insight_body(row)
    body.update({
        "preview": True,
        "paid_endpoint": f"{INSIGHT_PATH}?artist=<name>",
        "price": current_app.config.get("X402_PRICE", DEFAULT_PRICE),
        "currency": "USDC",
        "network": current_app.config.get("X402_NETWORK", DEFAULT_NETWORK),
    })
    return _no_store(jsonify(body))

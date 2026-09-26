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
Defaults: Base Sepolia (testnet) + the public x402.org facilitator.
Configuration (env):
  X402_PAY_TO        public receiving address (required to enable)
  X402_NETWORK       eip155:84532 (default, testnet) or eip155:8453 (Base mainnet)
  X402_FACILITATOR   x402org (default, testnet only) or cdp (Coinbase; mainnet + Bazaar)
  CDP_API_KEY_ID / CDP_API_KEY_SECRET   required when X402_FACILITATOR=cdp
  X402_PRICE         default $0.005
A misconfiguration (e.g. mainnet on the testnet-only facilitator, or cdp
without keys) leaves the endpoint off; it never takes the site down.
With the CDP facilitator, the route advertises Bazaar discovery metadata
(see _bazaar_extension) and gets listed after its first CDP-settled payment.
"""

import base64
import json
import logging
import os
import re
from datetime import timezone
from urllib.parse import parse_qs

from flask import Blueprint, current_app, jsonify, request

from db import db_cursor

agent_api_bp = Blueprint("agent_api", __name__)

INSIGHT_PATH = "/api/insight"
PREVIEW_PATH = "/api/insight/preview"
# Fixed, real sample, deliberately NOT Radiohead (the artist used in our paid
# examples), so the free preview doesn't give away the paid example
# (Nikos's review point β). Verified present with an insight in the live DB,
# 26 Sep 2026.
PREVIEW_ARTIST = "Massive Attack"

DEFAULT_NETWORK = "eip155:84532"  # Base Sepolia (testnet)
MAINNET_NETWORK = "eip155:8453"   # Base mainnet
DEFAULT_FACILITATOR_URL = "https://x402.org/facilitator"  # public, testnet-only
DEFAULT_PRICE = "$0.005"

# Bazaar (Coinbase's x402 discovery catalog) indexes a route after the CDP
# facilitator settles a payment for it, and its crawler calls the route with
# `input` below expecting a 402. The output example is a REAL response
# (the free preview for PREVIEW_ARTIST, 26 Sep 2026) with the insight text
# shortened to its first two sentences; every field the paid response has is
# present (Nikos: listings get dropped when required fields are missing).
BAZAAR_INPUT = {"artist": "Radiohead"}
BAZAAR_OUTPUT_EXAMPLE = {
    "artist": "Massive Attack",
    "insight": (
        "These five tracks reveal that Massive Attack's broad appeal stems from their "
        "ability to create atmospheric, emotionally resonant music that bridges underground "
        "credibility with mainstream accessibility. Teardrop and Angel dominate the play "
        "counts, and both are relatively immersive yet digestible pieces that work equally "
        "well in focused listening and as background accompaniment."
    ),
    "generated_at": "2026-08-03T05:17:07.102127Z",
    "source": "Waveline (https://wearewaveline.com), AI-written analysis generated with Anthropic Claude",
}
BAZAAR_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "artist": {"type": "string", "description": "Canonical artist name"},
        "insight": {"type": "string", "description": "AI-written analysis of the artist's sound and appeal"},
        "generated_at": {"type": "string", "description": "When the insight was generated (ISO-8601, UTC)"},
        "source": {"type": "string", "description": "Attribution"},
    },
    "required": ["artist", "insight", "generated_at", "source"],
}
SERVICE_NAME = "Waveline"
SERVICE_TAGS = ["music", "artists", "ai-insights"]

_EVM_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
_MAX_ARTIST_LEN = 200

SOURCE = "Waveline (https://wearewaveline.com), AI-written analysis generated with Anthropic Claude"


DESCRIPTION = (
    "Waveline artist insight: an AI-written analysis of an artist's "
    "sound and appeal, from Waveline's database of ~10,900 artists. "
    "Query: ?artist=<name>. Unknown artists return 404 and are not charged. "
    f"Free preview: GET {PREVIEW_PATH}"
)  # CDP's facilitator rejects verify/settle for descriptions over 500 chars.


def _bazaar_extension():
    """Discovery metadata for Bazaar, or {} if the extensions extra is missing."""
    try:
        from x402.extensions.bazaar import OutputConfig, declare_discovery_extension
    except ImportError:
        logging.warning("x402: bazaar extension unavailable (install x402[extensions]); not advertising")
        return {}
    return declare_discovery_extension(
        input=dict(BAZAAR_INPUT),
        input_schema={
            "type": "object",
            "properties": {"artist": {"type": "string", "description": "Artist name, e.g. Radiohead",
                                      "minLength": 1, "maxLength": _MAX_ARTIST_LEN}},
            "required": ["artist"],
        },
        output=OutputConfig(example=dict(BAZAAR_OUTPUT_EXAMPLE), schema=dict(BAZAAR_OUTPUT_SCHEMA)),
    )


def _build_routes(pay_to, network, price):
    from x402.http import PaymentOption, RouteConfig

    return {
        f"GET {INSIGHT_PATH}": RouteConfig(
            accepts=PaymentOption(scheme="exact", pay_to=pay_to, price=price, network=network),
            description=DESCRIPTION,
            mime_type="application/json",
            service_name=SERVICE_NAME,
            tags=list(SERVICE_TAGS),
            extensions=_bazaar_extension() or None,
        )
    }


def _build_facilitator(kind, url):
    """HTTP facilitator client for X402_FACILITATOR (`x402org` default, or `cdp`).

    `cdp` = Coinbase's hosted facilitator (needed for Base mainnet and for
    Bazaar listing). It authenticates with CDP_API_KEY_ID/CDP_API_KEY_SECRET,
    which the official cdp-sdk turns into per-request JWT headers. Fails
    closed: without both keys the paid endpoint stays off. The key values are
    never logged.
    """
    from x402.http import HTTPFacilitatorClientSync

    if kind == "cdp":
        key_id = (os.getenv("CDP_API_KEY_ID") or "").strip()
        key_secret = (os.getenv("CDP_API_KEY_SECRET") or "").strip()
        if not (key_id and key_secret):
            logging.error("x402 disabled: X402_FACILITATOR=cdp but CDP_API_KEY_ID/CDP_API_KEY_SECRET are not set")
            return None
        try:
            from cdp.x402 import create_facilitator_config
        except ImportError:
            logging.exception("x402 disabled: X402_FACILITATOR=cdp but cdp-sdk is not installed")
            return None
        return HTTPFacilitatorClientSync(create_facilitator_config(key_id, key_secret))
    if kind not in ("", "x402org"):
        logging.error("x402 disabled: unknown X402_FACILITATOR %r (use 'x402org' or 'cdp')", kind)
        return None
    return HTTPFacilitatorClientSync({"url": url})


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
    facilitator_kind = (os.getenv("X402_FACILITATOR") or "x402org").strip().lower()
    facilitator_url = (os.getenv("X402_FACILITATOR_URL") or DEFAULT_FACILITATOR_URL).strip()
    price = (os.getenv("X402_PRICE") or DEFAULT_PRICE).strip()

    try:
        from x402 import x402ResourceServerSync
        from x402.http import is_fatal_startup_init_error, x402HTTPResourceServerSync
        from x402.http.middleware.flask import payment_middleware
        from x402.mechanisms.evm.exact import ExactEvmServerScheme
    except ImportError:
        logging.exception("x402 disabled: the x402 package is not installed")
        return False

    if facilitator is None:
        facilitator = _build_facilitator(facilitator_kind, facilitator_url)
        if facilitator is None:
            return False

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
    app.config["X402_FACILITATOR"] = facilitator_kind
    app.config["X402_PRICE"] = price
    logging.info("x402 enabled on %s (network=%s, price=%s, facilitator=%s)", INSIGHT_PATH, network, price, facilitator_kind)
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

        # A request that can never be served must not be asked to pay:
        # reject a missing/blank/oversized ?artist with 400 BEFORE the x402
        # middleware issues its 402 (Nikos's review point α).
        problem = _artist_param_problem(environ.get("QUERY_STRING", ""))
        if problem:
            body = json.dumps({"error": "missing_artist", "message": problem}).encode("utf-8")
            start_response("400 Bad Request", [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "private, no-store"),
            ])
            return [body]

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


def _artist_param_problem(query_string):
    """Return an error message if ?artist can't be served, else None."""
    values = parse_qs(query_string, keep_blank_values=True).get("artist", [])
    artist = (values[0] if values else "").strip()
    if not artist:
        return "Pass the artist name as ?artist=<name>. No payment was requested."
    if len(artist) > _MAX_ARTIST_LEN:
        return f"Artist name is longer than {_MAX_ARTIST_LEN} characters. No payment was requested."
    return None


def _utc_iso(ts):
    """ISO-8601 in UTC with a trailing Z.

    searched_at is `timestamp without time zone DEFAULT now()` on a Postgres
    whose TimeZone is Etc/UTC (checked on Railway, 26 Sep 2026), and the app
    never changes the session time zone, so naive values are UTC.
    """
    if not hasattr(ts, "isoformat"):
        return str(ts)
    if getattr(ts, "tzinfo", None) is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts.isoformat() + "Z"


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
    return {
        "artist": name,
        "insight": text,
        "generated_at": _utc_iso(searched_at),
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

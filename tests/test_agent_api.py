"""Tests for the x402 pay-per-request agent API (agent_api.py).

Every test builds its own small Flask app with a fake facilitator, so no
test ever reaches a real x402 facilitator, blockchain or database. The
paid-flow tests drive the real x402 Flask middleware end to end: unpaid ->
402, paid + known artist -> 200 and settled, paid + unknown artist -> 404
and NOT settled.
"""

import base64
import datetime
import json

import pytest
from flask import Flask

import agent_api

PAY_TO = "0x" + "1" * 40
PAYER = "0x" + "2" * 40


class FakeFacilitator:
    """Stands in for HTTPFacilitatorClientSync. Records verify/settle calls."""

    def __init__(self, network="eip155:84532"):
        self.network = network
        self.verified = []
        self.settled = []

    def get_supported(self):
        from x402.schemas import SupportedResponse
        from x402.schemas.responses import SupportedKind
        return SupportedResponse(kinds=[SupportedKind(x402_version=2, scheme="exact", network=self.network)])

    def verify(self, payload, requirements, *args, **kwargs):
        from x402.schemas import VerifyResponse
        self.verified.append(requirements)
        return VerifyResponse(is_valid=True, payer=PAYER)

    def settle(self, payload, requirements, *args, **kwargs):
        from x402.schemas import SettleResponse
        self.settled.append(requirements)
        return SettleResponse(success=True, transaction="0xabc", network=requirements.network, payer=PAYER)


def _make_app(monkeypatch, pay_to=PAY_TO, facilitator=None):
    if pay_to is None:
        monkeypatch.delenv("X402_PAY_TO", raising=False)
    else:
        monkeypatch.setenv("X402_PAY_TO", pay_to)
    for var in ("X402_NETWORK", "X402_FACILITATOR_URL", "X402_PRICE"):
        monkeypatch.delenv(var, raising=False)
    app = Flask(__name__)
    app.register_blueprint(agent_api.agent_api_bp)
    enabled = agent_api.init_x402(app, facilitator=facilitator or FakeFacilitator())
    return app, enabled


def _payment_header(client, artist="Radiohead"):
    """Get the 402 challenge and build a (fake-signed) payment for it."""
    from x402.http import encode_payment_signature_header
    from x402.schemas import PaymentPayload, PaymentRequirements

    challenge = client.get(f"/api/insight?artist={artist}")
    assert challenge.status_code == 402
    required = json.loads(base64.b64decode(challenge.headers["PAYMENT-REQUIRED"]))
    requirements = PaymentRequirements.model_validate(required["accepts"][0])
    payload = PaymentPayload(x402_version=2, accepted=requirements,
                             payload={"signature": "0xdead", "authorization": {}},
                             resource=required["resource"])
    return encode_payment_signature_header(payload)


@pytest.fixture
def no_db(monkeypatch):
    """Fail loudly if anything reaches the real lookup unexpectedly."""
    def boom(artist):
        raise AssertionError("_latest_insight should not be called")
    monkeypatch.setattr(agent_api, "_latest_insight", boom)


# ── configuration gating ────────────────────────────────────────────

def test_disabled_without_address_and_never_serves_content(monkeypatch, no_db):
    app, enabled = _make_app(monkeypatch, pay_to=None)
    assert enabled is False
    resp = app.test_client().get("/api/insight?artist=Radiohead")
    assert resp.status_code == 404
    assert "insight" not in resp.get_json()


def test_invalid_address_keeps_it_disabled(monkeypatch, no_db):
    app, enabled = _make_app(monkeypatch, pay_to="not-an-address")
    assert enabled is False
    assert app.test_client().get("/api/insight?artist=Radiohead").status_code == 404


def test_unsupported_network_disables_instead_of_killing_the_process(monkeypatch, no_db):
    # Facilitator only supports a different chain: a permanent misconfig.
    # The x402 middleware would os._exit(1) on this at startup; our preflight
    # must catch it and just leave the endpoint off.
    app, enabled = _make_app(monkeypatch, facilitator=FakeFacilitator(network="eip155:1"))
    assert enabled is False
    assert app.test_client().get("/api/insight?artist=Radiohead").status_code == 404


# ── payment flow (real x402 middleware, fake facilitator) ───────────

def test_unpaid_request_gets_402_with_price_and_testnet_defaults(monkeypatch, no_db):
    app, enabled = _make_app(monkeypatch)
    assert enabled is True
    resp = app.test_client().get("/api/insight?artist=Radiohead")
    assert resp.status_code == 402
    required = json.loads(base64.b64decode(resp.headers["PAYMENT-REQUIRED"]))
    accept = required["accepts"][0]
    assert accept["network"] == "eip155:84532"   # Base Sepolia testnet
    assert accept["payTo"] == PAY_TO
    assert accept["amount"] == "5000"            # 0.005 USDC (6 decimals)


def test_paid_known_artist_returns_insight_and_settles(monkeypatch):
    facilitator = FakeFacilitator()
    app, _ = _make_app(monkeypatch, facilitator=facilitator)
    monkeypatch.setattr(agent_api, "_latest_insight",
                        lambda a: ("Radiohead", "An insight.", datetime.datetime(2026, 9, 1, 12, 0)))
    client = app.test_client()
    resp = client.get("/api/insight?artist=Radiohead", headers={"PAYMENT-SIGNATURE": _payment_header(client)})
    assert resp.status_code == 200
    assert resp.get_json() == {
        "artist": "Radiohead",
        "insight": "An insight.",
        "generated_at": "2026-09-01T12:00:00Z",
        "source": agent_api.SOURCE,
    }
    assert len(facilitator.settled) == 1
    assert "PAYMENT-RESPONSE" in resp.headers


def test_paid_unknown_artist_is_404_and_not_settled(monkeypatch):
    facilitator = FakeFacilitator()
    app, _ = _make_app(monkeypatch, facilitator=facilitator)
    monkeypatch.setattr(agent_api, "_latest_insight", lambda a: None)
    client = app.test_client()
    resp = client.get("/api/insight?artist=Nobody", headers={"PAYMENT-SIGNATURE": _payment_header(client, "Nobody")})
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "artist_not_found"
    assert len(facilitator.verified) == 1
    assert facilitator.settled == []


def test_paid_db_failure_is_503_and_not_settled(monkeypatch):
    facilitator = FakeFacilitator()
    app, _ = _make_app(monkeypatch, facilitator=facilitator)

    def broken(artist):
        raise RuntimeError("db down")
    monkeypatch.setattr(agent_api, "_latest_insight", broken)
    client = app.test_client()
    resp = client.get("/api/insight?artist=Radiohead", headers={"PAYMENT-SIGNATURE": _payment_header(client)})
    assert resp.status_code == 503
    assert facilitator.settled == []


# ── handler details ─────────────────────────────────────────────────

@pytest.mark.parametrize("query", ["", "?artist=", "?artist=%20%20", "?other=x", "?artist=" + "a" * 201])
def test_unservable_artist_is_400_before_any_402(monkeypatch, no_db, query):
    """Nikos's point α: never ask for payment for something we can't serve.
    The 400 comes from the edge wrapper, before the x402 middleware."""
    facilitator = FakeFacilitator()
    app, _ = _make_app(monkeypatch, facilitator=facilitator)
    resp = app.test_client().get("/api/insight" + query)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "missing_artist"
    assert "PAYMENT-REQUIRED" not in resp.headers
    assert facilitator.verified == [] and facilitator.settled == []


def test_valid_artist_still_gets_402(monkeypatch, no_db):
    app, _ = _make_app(monkeypatch)
    assert app.test_client().get("/api/insight?artist=" + "a" * 200).status_code == 402


def test_paid_request_without_artist_is_400_and_not_settled(monkeypatch):
    facilitator = FakeFacilitator()
    app, _ = _make_app(monkeypatch, facilitator=facilitator)
    client = app.test_client()
    header = _payment_header(client)
    resp = client.get("/api/insight", headers={"PAYMENT-SIGNATURE": header})
    assert resp.status_code == 400
    assert facilitator.verified == [] and facilitator.settled == []


def test_generated_at_is_utc_with_z():
    naive = datetime.datetime(2026, 9, 21, 17, 35, 18, 584386)
    assert agent_api._utc_iso(naive) == "2026-09-21T17:35:18.584386Z"
    athens = datetime.timezone(datetime.timedelta(hours=3))
    aware = datetime.datetime(2026, 9, 21, 20, 35, 18, tzinfo=athens)
    assert agent_api._utc_iso(aware) == "2026-09-21T17:35:18Z"


def test_preview_artist_differs_from_the_paid_example():
    assert agent_api.PREVIEW_ARTIST.lower() != "radiohead"


def test_response_has_no_third_party_numbers_and_no_store(monkeypatch):
    facilitator = FakeFacilitator()
    app, _ = _make_app(monkeypatch, facilitator=facilitator)
    monkeypatch.setattr(agent_api, "_latest_insight",
                        lambda a: ("Radiohead", "An insight.", datetime.datetime(2026, 9, 1)))
    client = app.test_client()
    resp = client.get("/api/insight?artist=Radiohead", headers={"PAYMENT-SIGNATURE": _payment_header(client)})
    body = resp.get_json()
    assert set(body) == {"artist", "insight", "generated_at", "source"}
    assert "no-store" in resp.headers["Cache-Control"]


def test_other_routes_are_not_payment_gated(monkeypatch):
    app, _ = _make_app(monkeypatch)

    @app.route("/free")
    def free():
        return "ok"

    assert app.test_client().get("/free").status_code == 200


# ── Nikos's production review (26 Sep 2026) ─────────────────────────

def test_402_body_is_nonempty_json_for_v1_clients(monkeypatch, no_db):
    """v1 agents read only the JSON body of the 402; an empty `{}` means
    they never pay. The body must mirror the PAYMENT-REQUIRED header."""
    app, _ = _make_app(monkeypatch)
    resp = app.test_client().get("/api/insight?artist=Radiohead")
    assert resp.status_code == 402
    body = resp.get_json()
    assert body["accepts"][0]["payTo"] == PAY_TO
    assert body["x402Version"] == 2
    assert body == json.loads(base64.b64decode(resp.headers["PAYMENT-REQUIRED"]))
    assert resp.headers.getlist("Content-Type") == ["application/json"]
    assert int(resp.headers["Content-Length"]) == len(resp.data)


def test_body_fill_leaves_paid_200_untouched(monkeypatch):
    facilitator = FakeFacilitator()
    app, _ = _make_app(monkeypatch, facilitator=facilitator)
    monkeypatch.setattr(agent_api, "_latest_insight",
                        lambda a: ("Radiohead", "An insight.", datetime.datetime(2026, 9, 1)))
    client = app.test_client()
    resp = client.get("/api/insight?artist=Radiohead", headers={"PAYMENT-SIGNATURE": _payment_header(client)})
    assert resp.status_code == 200
    assert "accepts" not in resp.get_json()


def test_resource_url_is_https_public_host_behind_railway_proxy(monkeypatch, no_db):
    """Mirrors dashboard.py's order: init_x402 first, ProxyFix wrapped
    outside it, so x402 builds resource.url from the forwarded https host."""
    from werkzeug.middleware.proxy_fix import ProxyFix
    app, _ = _make_app(monkeypatch)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    resp = app.test_client().get("/api/insight?artist=Radiohead", headers={
        "X-Forwarded-Proto": "https", "X-Forwarded-Host": "wearewaveline.com", "X-Forwarded-For": "1.2.3.4",
    })
    assert resp.get_json()["resource"]["url"] == "https://wearewaveline.com/api/insight?artist=Radiohead"


def test_dashboard_installs_x402_inside_proxyfix():
    import pathlib
    source = pathlib.Path(agent_api.__file__).with_name("dashboard.py").read_text()
    assert source.index("init_x402(app)") < source.index("app.wsgi_app = ProxyFix(")


def test_free_preview_needs_no_payment(monkeypatch):
    facilitator = FakeFacilitator()
    app, _ = _make_app(monkeypatch, facilitator=facilitator)
    asked = []

    def lookup(artist):
        asked.append(artist)
        return ("Massive Attack", "An insight.", datetime.datetime(2026, 9, 1))
    monkeypatch.setattr(agent_api, "_latest_insight", lookup)
    resp = app.test_client().get("/api/insight/preview")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["preview"] is True
    assert body["artist"] == "Massive Attack" and body["insight"] == "An insight."
    assert body["generated_at"] == "2026-09-01T00:00:00Z"
    assert body["paid_endpoint"] == "/api/insight?artist=<name>"
    assert body["price"] == "$0.005" and body["network"] == "eip155:84532"
    assert asked == [agent_api.PREVIEW_ARTIST]
    assert facilitator.verified == [] and facilitator.settled == []


def test_preview_is_off_when_x402_is_off(monkeypatch, no_db):
    app, _ = _make_app(monkeypatch, pay_to=None)
    assert app.test_client().get("/api/insight/preview").status_code == 404


def test_402_description_advertises_free_preview(monkeypatch, no_db):
    app, _ = _make_app(monkeypatch)
    body = app.test_client().get("/api/insight?artist=Radiohead").get_json()
    assert "Free preview: GET /api/insight/preview" in body["resource"]["description"]


def test_latest_insight_query_skips_empty_insights(monkeypatch):
    import contextlib
    seen = {}

    class Cur:
        def execute(self, sql, params=None):
            seen["sql"], seen["params"] = sql, params

        def fetchone(self):
            return None

    @contextlib.contextmanager
    def fake_cursor(commit=False):
        yield Cur()

    monkeypatch.setattr(agent_api, "db_cursor", fake_cursor)
    assert agent_api._latest_insight("Radiohead") is None
    assert "claude_insight <> ''" in seen["sql"]
    assert "LOWER(artist_name) = LOWER(%s)" in seen["sql"]
    assert seen["params"] == ("Radiohead",)

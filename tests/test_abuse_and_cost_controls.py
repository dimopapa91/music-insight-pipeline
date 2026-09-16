"""Tests for the abuse-and-cost-controls phase.

Production measurement that motivated this phase: 10,867 total searches,
10,857 of them anonymous, driven by unauthenticated GET requests to
/compare and /artist/<name> with attacker-controlled query params/path
segments — each one capable of triggering a real Last.fm + Anthropic Claude
API call (up to three Claude calls for a single /compare hit). This file
proves the fix: anonymous visitors can browse anything already analysed,
but can never trigger a new pipeline run or a new Claude call; logged-in
members keep the original behaviour; robots.txt and rate limiting cover
the crawlers that don't even read a "no auto-fetch for you" response;
analytics stops counting obvious bot traffic; and the compare verdict is
cached so repeating the same comparison doesn't re-call Claude.

No real network or database calls anywhere in this file. Rate limiting is
disabled by default for the whole test session (see conftest.py); the one
enforcement test flips `limiter.enabled` directly and resets storage first
so it isn't order-dependent on other tests.
"""

import contextlib
import datetime as dt
from types import SimpleNamespace

import anthropic

import analytics
import dashboard
import rate_limit
import views_artist
import views_main
from flask import Response
from models import User

FAKE_USER = User(id=1, username="dimos", email="d@e.com", password_hash="x")


def _login(client, monkeypatch, user=FAKE_USER):
    monkeypatch.setattr(User, "get", classmethod(lambda cls, i: user))
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def _never_found_cursor(commit=False):
    @contextlib.contextmanager
    def _cm():
        class FakeCur:
            def execute(self, sql, params=None):
                pass

            def fetchone(self):
                return None

        yield FakeCur()

    return _cm()


def _found_cursor_factory(row, count_result=(1,)):
    def fake(commit=False):
        @contextlib.contextmanager
        def _cm():
            class FakeCur:
                def __init__(self):
                    self._sql = ""

                def execute(self, sql, params=None):
                    self._sql = sql

                def fetchone(self):
                    if "COUNT(*)" in self._sql:
                        return count_result
                    return row

            yield FakeCur()

        return _cm()

    return fake


# ── 1/2. /artist/<name>: anonymous never triggers the pipeline, members do ──

def test_anonymous_unknown_artist_does_not_call_run_pipeline(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(views_artist, "db_cursor", _never_found_cursor)
    monkeypatch.setattr(views_artist, "run_pipeline", lambda *a, **k: calls.update(n=calls["n"] + 1))
    client = dashboard.app.test_client()
    resp = client.get("/artist/Some Brand New Artist")
    # A real 404: this URL genuinely doesn't resolve to a resource right
    # now, and we can't tell a legitimate not-yet-searched artist apart from
    # crawler garbage without an external API call (which would defeat the
    # point) — same calm, on-brand page content, correct status code.
    assert resp.status_code == 404
    assert calls["n"] == 0
    html = resp.data.decode()
    assert "hasn't been analysed on Waveline yet" in html
    assert "Create free account" in html
    assert "Log in" in html


def test_logged_in_unknown_artist_still_calls_run_pipeline(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(views_artist, "db_cursor", _never_found_cursor)
    monkeypatch.setattr(views_artist, "run_pipeline", lambda *a, **k: calls.update(n=calls["n"] + 1))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.get("/artist/Some Brand New Artist")
    assert calls["n"] == 1
    assert resp.status_code == 302  # redirects to reload after the auto-fetch


def test_anonymous_existing_artist_renders_normally_no_pipeline_call(monkeypatch):
    # Sharing/SEO must keep working: an anonymous visitor hitting an
    # ALREADY-analysed artist still gets the full page, no gate at all.
    row = ("Radiohead", "A real insight.", dt.datetime(2026, 1, 1), '[{"name": "Creep", "playcount": 100}]')
    calls = {"n": 0}
    monkeypatch.setattr(views_artist, "db_cursor", _found_cursor_factory(row))
    monkeypatch.setattr(views_artist, "run_pipeline", lambda *a, **k: calls.update(n=calls["n"] + 1))
    client = dashboard.app.test_client()
    resp = client.get("/artist/Radiohead")
    assert resp.status_code == 200
    assert calls["n"] == 0
    assert "Radiohead" in resp.data.decode()


# ── 3. /compare: anonymous never triggers a pipeline run or a Claude call ──

def test_anonymous_compare_never_calls_run_pipeline_or_claude(monkeypatch):
    pipeline_calls = {"n": 0}
    claude_calls = {"n": 0}

    def fake_anthropic_cls(api_key=None):
        claude_calls["n"] += 1
        raise AssertionError("Claude must never be constructed for anonymous /compare")

    monkeypatch.setattr(anthropic, "Anthropic", fake_anthropic_cls)
    monkeypatch.setattr(views_artist, "run_pipeline", lambda *a, **k: pipeline_calls.update(n=pipeline_calls["n"] + 1))
    monkeypatch.setattr(views_artist, "get_artist_db", lambda name: None)  # neither artist exists yet

    client = dashboard.app.test_client()
    resp = client.get("/compare?a=UnknownArtistA&b=UnknownArtistB")
    assert resp.status_code == 200
    assert pipeline_calls["n"] == 0
    assert claude_calls["n"] == 0
    html = resp.data.decode()
    assert "Create a free account to compare new artists" in html


def test_logged_in_compare_still_auto_fetches(monkeypatch):
    pipeline_calls = {"n": 0}

    def fake_run_pipeline(name):
        pipeline_calls["n"] += 1

    monkeypatch.setattr(views_artist, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(views_artist, "get_artist_db", lambda name: None)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    client.get("/compare?a=UnknownArtistA&b=UnknownArtistB")
    assert pipeline_calls["n"] == 2  # both a and b attempted


def test_anonymous_compare_with_both_existing_artists_still_renders_results(monkeypatch):
    # Anonymous visitors CAN see a comparison — just never a newly-triggered
    # one — and get a cached verdict if one already exists.
    def fake_get_artist_db(name):
        return {"name": name, "insight": "an insight", "tracks": ["Track A"], "spotify_url": "", "image": ""}

    monkeypatch.setattr(views_artist, "get_artist_db", fake_get_artist_db)
    monkeypatch.setattr(views_artist, "_cached_compare_verdict", lambda a, b: "A cached verdict.")
    client = dashboard.app.test_client()
    resp = client.get("/compare?a=Radiohead&b=Muse")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "A cached verdict." in html
    assert "Create a free account to compare new artists" not in html


# ── 4. robots.txt ─────────────────────────────────────────────────────

def test_robots_txt_served_with_expected_disallows_and_allows():
    client = dashboard.app.test_client()
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert resp.content_type.startswith("text/plain")
    body = resp.data.decode()
    for path in ["/compare", "/artist/", "/search", "/api/", "/admin/", "/messages", "/settings", "/notifications", "/u/"]:
        assert f"Disallow: {path}" in body, f"missing Disallow: {path}"
    for path in ["/", "/about", "/news", "/discover", "/feed"]:
        assert f"Allow: {path}" in body, f"missing Allow: {path}"


def test_robots_txt_not_recorded_in_analytics(monkeypatch):
    calls = []

    class FakeCur:
        def execute(self, sql, params=None):
            calls.append(params)

    @contextlib.contextmanager
    def fake_cm(commit=False):
        yield FakeCur()

    monkeypatch.setattr(analytics, "db_cursor", fake_cm)
    client = dashboard.app.test_client()
    client.get("/robots.txt")
    assert calls == []


# ── 5. rate limiting ──────────────────────────────────────────────────

def test_search_rate_limit_returns_429_once_exceeded(monkeypatch):
    monkeypatch.setattr(rate_limit.limiter, "enabled", True)
    rate_limit.limiter.storage.reset()
    monkeypatch.setattr(views_main, "run_pipeline", lambda artist, user_id=None: "an insight")
    client = dashboard.app.test_client()

    statuses = [client.post("/search", data={"artist": "Radiohead"}).status_code for _ in range(11)]

    assert statuses[:10] == [302] * 10  # the configured limit: 10 per hour
    assert statuses[10] == 429


def test_rate_limited_response_is_calm_not_a_raw_error(monkeypatch):
    monkeypatch.setattr(rate_limit.limiter, "enabled", True)
    rate_limit.limiter.storage.reset()
    monkeypatch.setattr(views_main, "run_pipeline", lambda artist, user_id=None: "an insight")
    client = dashboard.app.test_client()
    for _ in range(10):
        client.post("/search", data={"artist": "Radiohead"})
    resp = client.post("/search", data={"artist": "Radiohead"})
    assert resp.status_code == 429
    html = resp.data.decode()
    assert "wv-dock" in html  # real shell, not werkzeug's default error page
    assert "Slow down" in html


def test_rate_limits_disabled_by_default_in_tests():
    # conftest.py sets RATELIMIT_ENABLED=false before dashboard.py is first
    # imported — confirms that actually took effect, so the rest of this
    # (large) suite isn't at the mercy of shared in-memory counters.
    assert rate_limit.limiter.enabled is False


# ── ProxyFix: rate limiting must key on the real client, not the proxy ──
#
# dashboard.py wraps app.wsgi_app in Werkzeug's ProxyFix(x_for=1). Without
# it, on Railway (which sits its own reverse proxy in front of every app),
# request.remote_addr is the PROXY's address — identical for every visitor
# — so flask_limiter.util.get_remote_address would put all traffic in one
# shared bucket: real users get 429'd by bot traffic within minutes. These
# tests go through the real WSGI stack (test_client(), not
# test_request_context(), which bypasses app.wsgi_app and therefore
# ProxyFix entirely) so ProxyFix is genuinely exercised, not assumed.

def test_rate_limit_keys_on_the_real_client_address_from_x_forwarded_for(monkeypatch):
    monkeypatch.setattr(rate_limit.limiter, "enabled", True)
    rate_limit.limiter.storage.reset()
    monkeypatch.setattr(views_main, "run_pipeline", lambda artist, user_id=None: "an insight")
    client = dashboard.app.test_client()

    # Simulates Railway's single-hop edge: exactly one X-Forwarded-For
    # entry, the real client, however the test client's own default
    # REMOTE_ADDR (standing in for "the proxy's IP" if ProxyFix weren't
    # applied) never changes across requests.
    client_a = {"X-Forwarded-For": "203.0.113.9"}
    for _ in range(10):
        resp = client.post("/search", data={"artist": "Radiohead"}, headers=client_a)
        assert resp.status_code == 302
    blocked = client.post("/search", data={"artist": "Radiohead"}, headers=client_a)
    assert blocked.status_code == 429

    # A DIFFERENT real client must get its OWN quota. If the limiter were
    # still keying on the proxy's constant address (the pre-fix bug), this
    # request would share client_a's already-exhausted bucket and also 429.
    client_b = {"X-Forwarded-For": "198.51.100.7"}
    fresh = client.post("/search", data={"artist": "Radiohead"}, headers=client_b)
    assert fresh.status_code == 302


def test_spoofed_leading_x_forwarded_for_does_not_grant_a_fresh_quota(monkeypatch):
    monkeypatch.setattr(rate_limit.limiter, "enabled", True)
    rate_limit.limiter.storage.reset()
    monkeypatch.setattr(views_main, "run_pipeline", lambda artist, user_id=None: "an insight")
    client = dashboard.app.test_client()

    real_client_ip = "203.0.113.9"
    for _ in range(10):
        client.post("/search", data={"artist": "Radiohead"}, headers={"X-Forwarded-For": real_client_ip})
    exhausted = client.post("/search", data={"artist": "Radiohead"}, headers={"X-Forwarded-For": real_client_ip})
    assert exhausted.status_code == 429

    # The abuser now prepends a fake leading entry to their own request —
    # X-Forwarded-For: <attacker-chosen>, <real>. A well-behaved proxy
    # (Railway's edge) appends the address it actually received the
    # connection from as the RIGHTMOST entry regardless of what the client
    # tries to inject first, so with x_for=1 ProxyFix still resolves this to
    # real_client_ip — two different spoofed leading values must still
    # share the same, already-exhausted bucket, not get a fresh one each.
    spoofed_1 = client.post("/search", data={"artist": "Radiohead"},
                             headers={"X-Forwarded-For": f"6.6.6.6, {real_client_ip}"})
    assert spoofed_1.status_code == 429

    spoofed_2 = client.post("/search", data={"artist": "Radiohead"},
                             headers={"X-Forwarded-For": f"9.9.9.9, {real_client_ip}"})
    assert spoofed_2.status_code == 429


def test_proxyfix_does_not_mutate_the_raw_x_forwarded_for_header(monkeypatch):
    # ProxyFix only overwrites environ["REMOTE_ADDR"] — it never touches the
    # X-Forwarded-For header itself. analytics._client_ip() reads that raw
    # header directly (taking the leftmost/client-supplied entry, by
    # design, for its own best-effort daily-rotating visitor hash) and must
    # see exactly what was sent, unaffected by ProxyFix being installed.
    calls = []

    class FakeCur:
        def execute(self, sql, params=None):
            calls.append(params)

    @contextlib.contextmanager
    def fake_cm(commit=False):
        yield FakeCur()

    monkeypatch.setattr(analytics, "db_cursor", fake_cm)
    client = dashboard.app.test_client()
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "X-Forwarded-For": "6.6.6.6, 203.0.113.9",
    }
    client.get("/about", headers=headers)
    assert calls, "expected an analytics INSERT to have been attempted"
    # visitor_hash is deterministic from (ip, day, ua, secret) — recompute
    # it using the LEFTMOST entry (6.6.6.6), exactly as analytics._client_ip()
    # is documented to do, to prove ProxyFix didn't change which IP feeds it.
    visitor_hash = calls[0][-1]
    expected = analytics._visitor_hash("6.6.6.6", headers["User-Agent"], dashboard.app.secret_key)
    assert visitor_hash == expected


# ── 6. compare verdict cache ────────────────────────────────────────────

def test_compare_verdict_cache_prevents_a_second_claude_call(monkeypatch):
    calls = {"n": 0}

    class FakeMessages:
        def create(self, **kwargs):
            calls["n"] += 1
            return SimpleNamespace(content=[SimpleNamespace(text="A verdict.")])

    class FakeClient:
        def __init__(self, api_key=None):
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    views_artist._compare_cache.clear()

    a_data = {"name": "Radiohead", "insight": "x", "tracks": ["Creep"]}
    b_data = {"name": "Muse", "insight": "y", "tracks": ["Supermassive"]}

    first = views_artist._cached_compare_verdict(a_data, b_data)
    second = views_artist._cached_compare_verdict(a_data, b_data)

    assert first == "A verdict."
    assert second == "A verdict."
    assert calls["n"] == 1


def test_compare_verdict_cache_is_order_independent():
    views_artist._compare_cache.clear()
    key_ab = views_artist._compare_cache_key("Radiohead", "Muse")
    key_ba = views_artist._compare_cache_key("muse", "RADIOHEAD")
    assert key_ab == key_ba


def test_compare_verdict_failure_is_never_cached(monkeypatch):
    def fake_anthropic_cls(api_key=None):
        raise RuntimeError("provider down")

    monkeypatch.setattr(anthropic, "Anthropic", fake_anthropic_cls)
    views_artist._compare_cache.clear()
    a_data = {"name": "Radiohead", "insight": "x", "tracks": ["Creep"]}
    b_data = {"name": "Muse", "insight": "y", "tracks": ["Supermassive"]}

    result = views_artist._cached_compare_verdict(a_data, b_data)
    assert result is None
    key = views_artist._compare_cache_key("Radiohead", "Muse")
    assert key not in views_artist._compare_cache


# ── 7. analytics: crawler user-agents never recorded ───────────────────

def _record_with_ua(monkeypatch, user_agent, path="/about"):
    calls = []

    class FakeCur:
        def execute(self, sql, params=None):
            calls.append(params)

    @contextlib.contextmanager
    def fake_cm(commit=False):
        yield FakeCur()

    monkeypatch.setattr(analytics, "db_cursor", fake_cm)
    response = Response("<html></html>", content_type="text/html")
    headers = {"User-Agent": user_agent} if user_agent is not None else {}
    with dashboard.app.test_request_context(path, method="GET", headers=headers):
        analytics.record_pageview(response)
    return calls


def test_known_crawler_user_agent_not_recorded(monkeypatch):
    ua = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
    assert _record_with_ua(monkeypatch, ua) == []


def test_generic_scraper_user_agent_not_recorded(monkeypatch):
    assert _record_with_ua(monkeypatch, "python-requests/2.31.0") == []
    assert _record_with_ua(monkeypatch, "curl/8.4.0") == []
    assert _record_with_ua(monkeypatch, "Scrapy/2.11.0 (+https://scrapy.org)") == []


def test_empty_user_agent_not_recorded(monkeypatch):
    assert _record_with_ua(monkeypatch, None) == []


def test_real_browser_user_agent_still_recorded(monkeypatch):
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15"
    calls = _record_with_ua(monkeypatch, ua)
    assert len(calls) == 1


# ── homepage counter honesty ────────────────────────────────────────────

def test_homepage_counter_no_longer_claims_bot_inflated_search_count(monkeypatch):
    monkeypatch.setattr(views_main, "get_dashboard_data", lambda: (10867, 10838, 5, [], [], []))
    monkeypatch.setattr(views_main, "get_feed", lambda *a, **k: [])
    client = dashboard.app.test_client()
    html = client.get("/").data.decode()
    start = html.index('class="wv-hero-status"')
    status_line = html[start:html.index("</p>", start)]
    assert "10838 artist" in status_line
    assert "10867" not in status_line  # the bot-inflated search count is gone
    assert "search" not in status_line  # and so is any "across N searches" framing
    assert "today" not in status_line

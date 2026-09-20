"""Tests for Ticketmaster-backed upcoming events on the artist page.

Verified live: a keyword search for "Coldplay" returns tribute acts
("Ultimate Coldplay", "Talk tribute Coldplay") ABOVE the real attraction,
so taking the top hit would advertise a tribute band's dates as the
artist's own. get_artist_events() anchors on the attraction whose name
actually matches — the same artist_names_match() guard used for artwork.

No real network or database calls anywhere in this file.
"""

import pytest

import services


@pytest.fixture(autouse=True)
def _clear_events_cache():
    """Module-level cache, cleared so it can't leak between tests (or into
    the rest of the suite) — same pattern as the other services tests."""
    services._events_cache.clear()
    yield
    services._events_cache.clear()


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    """Most tests need the feature switched on; the disabled case sets its
    own. Patched on the module attribute because it's read at import time."""
    monkeypatch.setattr(services, "TICKETMASTER_API_KEY", "test-tm-key")


class _FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def _attractions(*entries):
    return {"_embedded": {"attractions": list(entries)}}


def _attraction(name, att_id, upcoming=3):
    return {"name": name, "id": att_id, "upcomingEvents": {"_total": upcoming}}


def _event(name="Coldplay: Music of the Spheres", date="2026-12-05",
           venue="Wembley Stadium", city="London", country="United Kingdom",
           url="https://ticketmaster.com/event/123"):
    return {
        "name": name,
        "url": url,
        "dates": {"start": {"localDate": date}},
        "_embedded": {"venues": [{
            "name": venue,
            "city": {"name": city} if city is not None else None,
            "country": {"name": country} if country is not None else None,
        }]},
    }


def _two_step(attractions_payload, events_payload, calls=None):
    """Route the attractions call and the events call to their payloads."""
    def fake_get(url, params=None, timeout=None):
        if calls is not None:
            calls.append(url)
        if "attractions" in url:
            return _FakeResponse(attractions_payload)
        return _FakeResponse(events_payload)
    return fake_get


# ── feature disabled ─────────────────────────────────────────────────

def test_missing_api_key_returns_empty_and_makes_no_request(monkeypatch):
    monkeypatch.setattr(services, "TICKETMASTER_API_KEY", None)
    calls = []
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: calls.append(1) or _FakeResponse({}))

    assert services.get_artist_events("Coldplay") == []
    assert calls == []


# ── the tribute-band case ────────────────────────────────────────────

def test_tribute_acts_ranked_above_the_real_artist_are_skipped(monkeypatch):
    # Exactly the live response shape: tributes first, real artist lower.
    attractions = _attractions(
        _attraction("Ultimate Coldplay", "TRIBUTE1"),
        _attraction("Talk tribute Coldplay", "TRIBUTE2"),
        _attraction("Coldplay", "REAL-ID"),
        _attraction("Coldplay Tribute Band", "TRIBUTE3"),
    )
    captured = {}

    def fake_get(url, params=None, timeout=None):
        if "attractions" in url:
            return _FakeResponse(attractions)
        captured["attractionId"] = params["attractionId"]
        return _FakeResponse({"_embedded": {"events": [_event()]}})

    monkeypatch.setattr(services.http_requests, "get", fake_get)

    events = services.get_artist_events("Coldplay")

    # The real attraction's id is what got queried — no tribute dates.
    assert captured["attractionId"] == "REAL-ID"
    assert len(events) == 1
    assert events[0]["venue"] == "Wembley Stadium"


def test_no_matching_attraction_returns_empty(monkeypatch):
    attractions = _attractions(
        _attraction("Ultimate Coldplay", "TRIBUTE1"),
        _attraction("Coldplay Tribute Band", "TRIBUTE2"),
    )
    calls = []
    monkeypatch.setattr(services.http_requests, "get",
                        _two_step(attractions, {"_embedded": {"events": [_event()]}}, calls))

    # Fail safe: better no events than a tribute act's.
    assert services.get_artist_events("Coldplay") == []
    assert not any("events.json" in u for u in calls)


def test_missing_embedded_attractions_returns_empty(monkeypatch):
    monkeypatch.setattr(services.http_requests, "get", lambda *a, **k: _FakeResponse({}))
    assert services.get_artist_events("Coldplay") == []


def test_attraction_match_is_case_and_punctuation_insensitive(monkeypatch):
    attractions = _attractions(_attraction("BEYONCÉ", "BEY-ID"))
    monkeypatch.setattr(services.http_requests, "get",
                        _two_step(attractions, {"_embedded": {"events": [_event()]}}))
    assert len(services.get_artist_events("Beyonce")) == 1


# ── the zero-upcoming shortcut ───────────────────────────────────────

def test_zero_upcoming_events_skips_the_second_request(monkeypatch):
    attractions = _attractions(_attraction("Coldplay", "REAL-ID", upcoming=0))
    calls = []
    monkeypatch.setattr(services.http_requests, "get",
                        _two_step(attractions, {"_embedded": {"events": [_event()]}}, calls))

    assert services.get_artist_events("Coldplay") == []
    assert len(calls) == 1
    assert "attractions" in calls[0]


def test_missing_upcoming_events_block_still_queries_events(monkeypatch):
    # Only an explicit zero short-circuits; an absent count must not.
    attractions = _attractions({"name": "Coldplay", "id": "REAL-ID"})
    calls = []
    monkeypatch.setattr(services.http_requests, "get",
                        _two_step(attractions, {"_embedded": {"events": [_event()]}}, calls))

    assert len(services.get_artist_events("Coldplay")) == 1
    assert len(calls) == 2


# ── field mapping ────────────────────────────────────────────────────

def test_event_fields_map_and_the_date_is_reformatted(monkeypatch):
    attractions = _attractions(_attraction("Coldplay", "REAL-ID"))
    monkeypatch.setattr(services.http_requests, "get",
                        _two_step(attractions, {"_embedded": {"events": [_event()]}}))

    assert services.get_artist_events("Coldplay") == [{
        "date": "05 Dec 2026",          # reformatted from "2026-12-05"
        "venue": "Wembley Stadium",
        "city": "London",
        "country": "United Kingdom",
        "url": "https://ticketmaster.com/event/123",
        "title": "Coldplay: Music of the Spheres",
    }]


def test_unparseable_date_falls_back_to_the_raw_value(monkeypatch):
    attractions = _attractions(_attraction("Coldplay", "REAL-ID"))
    monkeypatch.setattr(services.http_requests, "get", _two_step(
        attractions, {"_embedded": {"events": [_event(date="TBA")]}}))
    assert services.get_artist_events("Coldplay")[0]["date"] == "TBA"


def test_missing_date_is_empty_not_an_error(monkeypatch):
    attractions = _attractions(_attraction("Coldplay", "REAL-ID"))
    ev = _event()
    del ev["dates"]
    monkeypatch.setattr(services.http_requests, "get",
                        _two_step(attractions, {"_embedded": {"events": [ev]}}))
    assert services.get_artist_events("Coldplay")[0]["date"] == ""


def test_missing_venue_block_does_not_raise(monkeypatch):
    attractions = _attractions(_attraction("Coldplay", "REAL-ID"))
    ev = _event()
    del ev["_embedded"]
    monkeypatch.setattr(services.http_requests, "get",
                        _two_step(attractions, {"_embedded": {"events": [ev]}}))

    result = services.get_artist_events("Coldplay")[0]
    assert result["venue"] == "" and result["city"] == "" and result["country"] == ""


def test_null_city_and_country_objects_are_tolerated(monkeypatch):
    # Ticketmaster returns city/country as null for some listings, which is
    # why the mapping uses (venue.get("city") or {}).
    attractions = _attractions(_attraction("Coldplay", "REAL-ID"))
    ev = _event(city=None, country=None)
    monkeypatch.setattr(services.http_requests, "get",
                        _two_step(attractions, {"_embedded": {"events": [ev]}}))

    result = services.get_artist_events("Coldplay")[0]
    assert result["city"] == "" and result["country"] == ""
    assert result["venue"] == "Wembley Stadium"


def test_empty_venues_list_is_tolerated(monkeypatch):
    attractions = _attractions(_attraction("Coldplay", "REAL-ID"))
    ev = _event()
    ev["_embedded"]["venues"] = []
    monkeypatch.setattr(services.http_requests, "get",
                        _two_step(attractions, {"_embedded": {"events": [ev]}}))
    assert services.get_artist_events("Coldplay")[0]["venue"] == ""


def test_limit_is_passed_through_as_size(monkeypatch):
    attractions = _attractions(_attraction("Coldplay", "REAL-ID"))
    captured = {}

    def fake_get(url, params=None, timeout=None):
        if "attractions" in url:
            return _FakeResponse(attractions)
        captured.update(params)
        return _FakeResponse({"_embedded": {"events": []}})

    monkeypatch.setattr(services.http_requests, "get", fake_get)
    services.get_artist_events("Coldplay", limit=3)
    assert captured["size"] == 3
    assert captured["sort"] == "date,asc"


# ── failure paths ────────────────────────────────────────────────────

def test_attractions_non_200_returns_empty(monkeypatch):
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: _FakeResponse({}, status_code=401))
    assert services.get_artist_events("Coldplay") == []


def test_events_non_200_returns_empty(monkeypatch):
    attractions = _attractions(_attraction("Coldplay", "REAL-ID"))

    def fake_get(url, params=None, timeout=None):
        if "attractions" in url:
            return _FakeResponse(attractions)
        return _FakeResponse({}, status_code=503)

    monkeypatch.setattr(services.http_requests, "get", fake_get)
    assert services.get_artist_events("Coldplay") == []


def test_exception_returns_empty(monkeypatch):
    def _boom(*a, **k):
        raise ConnectionError("ticketmaster down")

    monkeypatch.setattr(services.http_requests, "get", _boom)
    assert services.get_artist_events("Coldplay") == []


def test_api_key_never_reaches_the_logs(monkeypatch, caplog):
    monkeypatch.setattr(services, "TICKETMASTER_API_KEY", "super-secret-tm-key")
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: _FakeResponse({}, status_code=401))
    with caplog.at_level("WARNING"):
        services.get_artist_events("Coldplay")
    assert "super-secret-tm-key" not in caplog.text


# ── caching ──────────────────────────────────────────────────────────

def test_results_are_cached_within_the_ttl(monkeypatch):
    attractions = _attractions(_attraction("Coldplay", "REAL-ID"))
    calls = []
    monkeypatch.setattr(services.http_requests, "get",
                        _two_step(attractions, {"_embedded": {"events": [_event()]}}, calls))

    first = services.get_artist_events("Coldplay")
    second = services.get_artist_events("Coldplay")

    assert first == second
    assert len(calls) == 2          # one attractions + one events, not four

    services._events_cache["coldplay"]["at"] -= services._EVENTS_TTL + 1
    services.get_artist_events("Coldplay")
    assert len(calls) == 4


def test_empty_result_is_retried_on_the_shorter_neg_ttl(monkeypatch):
    calls = []
    monkeypatch.setattr(services.http_requests, "get",
                        _two_step(_attractions(), {}, calls))

    assert services.get_artist_events("Coldplay") == []
    assert len(calls) == 1

    # An artist with no dates today may have some next week — this must not
    # be held for the full 6h success TTL.
    services._events_cache["coldplay"]["at"] -= services._EVENTS_NEG_TTL + 1
    services.get_artist_events("Coldplay")
    assert len(calls) == 2


def test_cache_key_is_normalised(monkeypatch):
    attractions = _attractions(_attraction("Coldplay", "REAL-ID"))
    calls = []
    monkeypatch.setattr(services.http_requests, "get",
                        _two_step(attractions, {"_embedded": {"events": [_event()]}}, calls))

    services.get_artist_events("Coldplay")
    services.get_artist_events("  coldplay  ")
    assert len(calls) == 2


# ── hero badge (template) ────────────────────────────────────────────

def _render_artist_page(monkeypatch, events):
    """Render /artist/<name> with everything external stubbed, so only the
    template's handling of `events` is under test."""
    import contextlib
    import datetime
    import json

    import dashboard
    import views_artist

    row = ("Coldplay", "An insight.", datetime.datetime(2026, 9, 1),
           json.dumps([{"name": "Yellow", "playcount": "500"}]))

    class FakeCur:
        def __init__(self):
            self.n = 0

        def execute(self, sql, params=None):
            self.n += 1

        def fetchone(self):
            return row if self.n == 1 else (3,)

    @contextlib.contextmanager
    def fake_cm(commit=False):
        yield FakeCur()

    monkeypatch.setattr(views_artist, "db_cursor", fake_cm)
    monkeypatch.setattr(views_artist, "resolve_insight", lambda n, i: (i, False))
    monkeypatch.setattr(views_artist, "get_similar_artists", lambda n: [])
    monkeypatch.setattr(views_artist, "get_artist_media",
                         lambda n, m=None: {"spotify": {}, "deezer_image": "", "deezer_fans": 0})
    monkeypatch.setattr(views_artist, "get_artist_events", lambda n: events)

    class _LastfmResponse:
        status_code = 200

        def json(self):
            return {"artist": {"stats": {}, "tags": {"tag": []}, "mbid": ""}}

    monkeypatch.setattr(views_artist.http_requests, "get", lambda *a, **k: _LastfmResponse())

    return dashboard.app.test_client().get("/artist/Coldplay").data.decode()


def _badge_event(**over):
    base = {"date": "05 Dec 2026", "venue": "Wembley Stadium", "city": "London",
            "country": "United Kingdom", "url": "https://tm.com/e/1", "title": "X"}
    base.update(over)
    return base


def test_hero_badge_renders_when_there_are_events(monkeypatch):
    html = _render_artist_page(monkeypatch, [_badge_event(), _badge_event()])
    # Match the markup, not the bare class name: the CSS rule for
    # .ar-events-badge sits in the page's <style> block either way.
    assert 'class="ar-events-badge"' in html
    assert "2 upcoming events" in html
    # It's a jump link to the section further down the page.
    assert 'href="#events"' in html


def test_hero_badge_is_absent_without_events(monkeypatch):
    html = _render_artist_page(monkeypatch, [])
    assert 'class="ar-events-badge"' not in html
    assert "upcoming event" not in html


def test_hero_badge_singular_for_one_event(monkeypatch):
    html = _render_artist_page(monkeypatch, [_badge_event()])
    assert "1 upcoming event" in html
    assert "1 upcoming events" not in html

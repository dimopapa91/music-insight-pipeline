"""Tests for the external-call caches behind the homepage/artist-page TTFB fix.

Measured live: the homepage sat at ~5s TTFB because get_dashboard_data()
fired ~17 sequential external calls per render — a Last.fm getSimilar per
latest-insight row, more inside discovery, then a Deezer lookup per
discovered artist (4s timeout each) — all rebuilding identical data every
load. Three caches now sit in front of that, following the TTL pattern
_spotify_artist_cache established: successes held long, failures held
briefly so an outage isn't pinned in for a day.

No real network or database calls anywhere in this file.
"""

import contextlib

import pytest

import dashboard
import services
import views_main


@pytest.fixture(autouse=True)
def _clear_perf_caches():
    """These caches are module-level and would otherwise leak between tests
    (and into the rest of the suite, which shares one imported services)."""
    services._similar_cache.clear()
    services._deezer_card_cache.clear()
    services.clear_dashboard_cache()
    yield
    services._similar_cache.clear()
    services._deezer_card_cache.clear()
    services.clear_dashboard_cache()


class _FakeResponse:
    def __init__(self, payload=None):
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def _similar_payload(*names):
    return {"similarartists": {"artist": [{"name": n} for n in names]}}


# ── get_similar_artists (Last.fm) ────────────────────────────────────

def test_similar_artists_second_call_makes_no_second_request(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params["artist"])
        return _FakeResponse(_similar_payload("Portishead", "Massive Attack"))

    monkeypatch.setattr(services.http_requests, "get", fake_get)

    first = services.get_similar_artists("Radiohead")
    second = services.get_similar_artists("Radiohead")

    assert first == ["Portishead", "Massive Attack"]
    assert second == first
    assert len(calls) == 1


def test_similar_artists_cache_key_is_normalised(monkeypatch):
    calls = []
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: calls.append(1) or _FakeResponse(_similar_payload("X")))

    services.get_similar_artists("Radiohead")
    services.get_similar_artists("  radiohead  ")
    assert len(calls) == 1


def test_similar_artists_success_is_held_for_the_long_ttl(monkeypatch):
    calls = []
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: calls.append(1) or _FakeResponse(_similar_payload("X")))

    services.get_similar_artists("Radiohead")
    # Just inside the 24h success TTL: still no refetch.
    services._similar_cache["radiohead"]["at"] -= services._SIMILAR_TTL - 10
    services.get_similar_artists("Radiohead")
    assert len(calls) == 1

    # Past it: refetched.
    services._similar_cache["radiohead"]["at"] -= 20
    services.get_similar_artists("Radiohead")
    assert len(calls) == 2


def test_similar_artists_empty_result_is_cached_under_the_short_neg_ttl(monkeypatch):
    calls = []
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: calls.append(1) or _FakeResponse({}))

    assert services.get_similar_artists("Nobody") == []
    assert services.get_similar_artists("Nobody") == []
    assert len(calls) == 1

    # An empty answer must NOT be held for the full success TTL — aging it
    # just past the (much shorter) negative TTL retries Last.fm.
    services._similar_cache["nobody"]["at"] -= services._SIMILAR_NEG_TTL + 1
    services.get_similar_artists("Nobody")
    assert len(calls) == 2


def test_similar_artists_exception_is_cached_as_empty(monkeypatch):
    calls = []

    def _boom(*a, **k):
        calls.append(1)
        raise ConnectionError("last.fm down")

    monkeypatch.setattr(services.http_requests, "get", _boom)

    assert services.get_similar_artists("Radiohead") == []
    assert services.get_similar_artists("Radiohead") == []
    assert len(calls) == 1


# ── _deezer_artist_card ──────────────────────────────────────────────

def _deezer_hit(name="Portishead", picture="https://cdn.deezer.com/p.jpg", fans=4200):
    return {"total": 1, "data": [{"name": name, "picture_medium": picture, "nb_fan": fans}]}


def test_deezer_card_maps_a_hit_and_caches_it(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params["q"])
        return _FakeResponse(_deezer_hit())

    monkeypatch.setattr(services.http_requests, "get", fake_get)

    card = services._deezer_artist_card("Portishead")
    assert card == {"name": "Portishead", "image": "https://cdn.deezer.com/p.jpg", "nb_fan": 4200}
    assert set(card) == {"name", "image", "nb_fan"}

    assert services._deezer_artist_card("Portishead") == card
    assert len(calls) == 1


def test_deezer_card_applies_the_blank_image_placeholder_rule(monkeypatch):
    placeholder = f"https://cdn.deezer.com/{services.DEEZER_BLANK_IMAGE_HASH}.jpg"
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: _FakeResponse(_deezer_hit(picture=placeholder)))
    assert services._deezer_artist_card("Portishead")["image"] == ""


def test_deezer_card_unknown_artist_returns_empty_card_on_long_ttl(monkeypatch):
    calls = []
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: calls.append(1) or _FakeResponse({"total": 0}))

    assert services._deezer_artist_card("Nobody") == {"name": "Nobody", "image": "", "nb_fan": 0}
    # "Deezer has never heard of them" is a real answer, not a failure, so it
    # keeps the long TTL rather than being retried every hour.
    services._deezer_card_cache["nobody"]["at"] -= services._DEEZER_CARD_NEG_TTL + 1
    services._deezer_artist_card("Nobody")
    assert len(calls) == 1


def test_deezer_card_exception_returns_fallback_and_retries_sooner(monkeypatch):
    calls = []

    def _boom(*a, **k):
        calls.append(1)
        raise ConnectionError("deezer down")

    monkeypatch.setattr(services.http_requests, "get", _boom)

    assert services._deezer_artist_card("Portishead") == {"name": "Portishead", "image": "", "nb_fan": 0}
    assert services._deezer_artist_card("Portishead")["name"] == "Portishead"
    assert len(calls) == 1

    # A transport failure IS negative: it must recover on the short TTL.
    services._deezer_card_cache["portishead"]["at"] -= services._DEEZER_CARD_NEG_TTL + 1
    services._deezer_artist_card("Portishead")
    assert len(calls) == 2


def test_discovery_artists_output_shape_is_unchanged(monkeypatch):
    monkeypatch.setattr(services, "get_similar_artists",
                        lambda name: ["Portishead"] if name == "Radiohead" else [])
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: _FakeResponse(_deezer_hit()))

    assert services.get_discovery_artists(["Radiohead"]) == [
        {"name": "Portishead", "image": "https://cdn.deezer.com/p.jpg", "nb_fan": 4200}
    ]


def test_discovery_excludes_already_searched_artists(monkeypatch):
    # Behaviour predating this change; pinned so the rewrite to
    # _deezer_artist_card can't quietly alter filtering.
    monkeypatch.setattr(services, "get_similar_artists", lambda name: ["Radiohead", "Portishead"])
    queried = []

    def fake_get(url, params=None, timeout=None):
        queried.append(params["q"])
        return _FakeResponse(_deezer_hit(name=params["q"]))

    monkeypatch.setattr(services.http_requests, "get", fake_get)

    cards = services.get_discovery_artists(["Radiohead"])

    # Radiohead was already searched, so it is never looked up or returned.
    assert queried == ["Portishead"]
    assert [c["name"] for c in cards] == ["Portishead"]


# ── get_dashboard_data ───────────────────────────────────────────────

def _stub_dashboard_db(monkeypatch):
    """Minimal fake cursor covering get_dashboard_data's six queries in order."""
    results = [
        [(7,)],                                        # COUNT(*)
        [(3,)],                                        # COUNT(DISTINCT artist_name)
        [(1,)],                                        # COUNT today
        [("Radiohead", [{"playcount": "100"}])],       # artist_name, top_tracks
        [("Radiohead", "an insight", None, [{"name": "Karma Police"}])],  # latest insights
        [("Radiohead",)],                              # DISTINCT artist_name
    ]
    remaining = list(results)

    class FakeCur:
        def execute(self, sql, params=None):
            self._rows = remaining.pop(0) if remaining else []

        def fetchone(self):
            return self._rows[0] if self._rows else None

        def fetchall(self):
            return self._rows

    @contextlib.contextmanager
    def fake_cm(commit=False):
        yield FakeCur()

    monkeypatch.setattr(services, "db_cursor", fake_cm)


def test_dashboard_data_is_cached_within_the_ttl(monkeypatch):
    _stub_dashboard_db(monkeypatch)
    monkeypatch.setattr(services, "resolve_insight", lambda a, i: (i, False))
    discovery_calls = []
    monkeypatch.setattr(services, "get_discovery_artists",
                        lambda artists: discovery_calls.append(1) or [])
    monkeypatch.setattr(services, "get_similar_artists", lambda name: [])

    first = services.get_dashboard_data()
    second = services.get_dashboard_data()

    # Built once: the expensive external leg didn't run a second time.
    assert len(discovery_calls) == 1
    assert second is first
    assert first[0] == 7 and first[1] == 3


def test_clear_dashboard_cache_forces_a_rebuild(monkeypatch):
    _stub_dashboard_db(monkeypatch)
    monkeypatch.setattr(services, "resolve_insight", lambda a, i: (i, False))
    discovery_calls = []
    monkeypatch.setattr(services, "get_discovery_artists",
                        lambda artists: discovery_calls.append(1) or [])
    monkeypatch.setattr(services, "get_similar_artists", lambda name: [])

    services.get_dashboard_data()
    services.clear_dashboard_cache()

    _stub_dashboard_db(monkeypatch)  # fresh canned rows for the rebuild
    services.get_dashboard_data()
    assert len(discovery_calls) == 2


def test_dashboard_cache_expires_after_its_ttl(monkeypatch):
    _stub_dashboard_db(monkeypatch)
    monkeypatch.setattr(services, "resolve_insight", lambda a, i: (i, False))
    discovery_calls = []
    monkeypatch.setattr(services, "get_discovery_artists",
                        lambda artists: discovery_calls.append(1) or [])
    monkeypatch.setattr(services, "get_similar_artists", lambda name: [])

    services.get_dashboard_data()
    services._dashboard_cache["at"] -= services._DASHBOARD_TTL + 1

    _stub_dashboard_db(monkeypatch)
    services.get_dashboard_data()
    assert len(discovery_calls) == 2


def test_successful_search_invalidates_the_dashboard_cache(monkeypatch):
    # Without this the artist a user just added wouldn't show on the
    # homepage until the 180s TTL lapsed. The failure path must NOT clear:
    # nothing was saved, so there's nothing new to show.
    cleared = []
    monkeypatch.setattr(views_main, "clear_dashboard_cache", lambda: cleared.append(1))
    monkeypatch.setattr(views_main, "run_pipeline", lambda artist, user_id=None: "ok")
    client = dashboard.app.test_client()

    resp = client.post("/search", data={"artist": "Radiohead"})
    assert resp.status_code == 302
    assert len(cleared) == 1

    def _boom(artist, user_id=None):
        raise RuntimeError("core pipeline failure")

    monkeypatch.setattr(views_main, "run_pipeline", _boom)
    failed = client.post("/search", data={"artist": "Nobody"})
    assert failed.status_code == 302
    assert len(cleared) == 1

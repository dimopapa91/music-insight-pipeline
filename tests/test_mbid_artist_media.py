"""Tests for MBID-anchored artist media resolution.

The name-match guard stops a provider handing back a *differently named*
artist, but it can't survive a name COLLISION: two different artists
genuinely share a name and a search returns whichever is more popular.
get_artist_media() anchors on the MusicBrainz id Last.fm gives us, resolves
the artist's own official Spotify/Deezer URLs through MusicBrainz, and
fetches those exact ids — falling back to name search only for artists with
no MBID or no linked URLs.

No real network or database calls anywhere in this file.
"""

import pytest

import services


@pytest.fixture(autouse=True)
def _clear_media_caches():
    """Module-level caches, cleared so they can't leak between tests (or
    into the rest of the suite) — same pattern as the other services tests."""
    caches = (
        services._mb_links_cache,
        services._spotify_by_id_cache,
        services._deezer_by_id_cache,
        services._spotify_artist_cache,
        services._spotify_token_cache,
    )
    for c in caches:
        c.clear()
    yield
    for c in caches:
        c.clear()


class _FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def _relations_payload():
    return {"relations": [
        {"type": "wikidata", "url": {"resource": "https://www.wikidata.org/wiki/Q11649"}},
        {"type": "free streaming",
         "url": {"resource": "https://open.spotify.com/artist/4Z8W4fKeB5YxbusRsdQVPb"}},
        {"type": "streaming",
         "url": {"resource": "https://www.deezer.com/en/artist/399"}},
    ]}


MBID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"


# ── get_musicbrainz_links ────────────────────────────────────────────

def test_musicbrainz_links_extracts_both_ids(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _FakeResponse(_relations_payload())

    monkeypatch.setattr(services.http_requests, "get", fake_get)

    links = services.get_musicbrainz_links(MBID)

    assert links == {"spotify_id": "4Z8W4fKeB5YxbusRsdQVPb", "deezer_id": "399"}
    assert captured["url"] == f"https://musicbrainz.org/ws/2/artist/{MBID}"
    assert captured["params"] == {"inc": "url-rels", "fmt": "json"}
    # MusicBrainz 403s requests without a descriptive UA, so this is required.
    assert captured["headers"]["User-Agent"] == services.MUSICBRAINZ_USER_AGENT


def test_musicbrainz_links_scans_urls_regardless_of_relation_type(monkeypatch):
    # The same streaming link appears under different relation-type labels
    # depending on the artist, so the URL itself is what we match on.
    payload = {"relations": [
        {"type": "purchase for download",
         "url": {"resource": "https://open.spotify.com/artist/ABC123"}},
        {"type": "some unexpected label",
         "url": {"resource": "https://deezer.com/artist/7"}},
    ]}
    monkeypatch.setattr(services.http_requests, "get", lambda *a, **k: _FakeResponse(payload))
    assert services.get_musicbrainz_links(MBID) == {"spotify_id": "ABC123", "deezer_id": "7"}


def test_musicbrainz_links_handles_locale_free_deezer_urls(monkeypatch):
    payload = {"relations": [{"url": {"resource": "https://www.deezer.com/artist/12345"}}]}
    monkeypatch.setattr(services.http_requests, "get", lambda *a, **k: _FakeResponse(payload))
    assert services.get_musicbrainz_links(MBID)["deezer_id"] == "12345"


def test_musicbrainz_links_no_music_links_is_a_real_answer(monkeypatch):
    payload = {"relations": [
        {"type": "wikidata", "url": {"resource": "https://www.wikidata.org/wiki/Q11649"}},
        {"type": "official homepage", "url": {"resource": "https://example.com"}},
    ]}
    monkeypatch.setattr(services.http_requests, "get", lambda *a, **k: _FakeResponse(payload))
    assert services.get_musicbrainz_links(MBID) == {"spotify_id": None, "deezer_id": None}


def test_musicbrainz_links_tolerates_a_null_url_object(monkeypatch):
    # MusicBrainz can return a relation with url: null; (r.get("url") or {})
    # is what keeps that from raising.
    payload = {"relations": [{"type": "free streaming", "url": None}]}
    monkeypatch.setattr(services.http_requests, "get", lambda *a, **k: _FakeResponse(payload))
    assert services.get_musicbrainz_links(MBID) == {"spotify_id": None, "deezer_id": None}


def test_musicbrainz_links_non_200_returns_empty(monkeypatch):
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: _FakeResponse({}, status_code=503))
    assert services.get_musicbrainz_links(MBID) == {}


def test_musicbrainz_links_exception_returns_empty(monkeypatch):
    def _boom(*a, **k):
        raise ConnectionError("musicbrainz down")

    monkeypatch.setattr(services.http_requests, "get", _boom)
    assert services.get_musicbrainz_links(MBID) == {}


def test_musicbrainz_links_empty_mbid_never_makes_a_request(monkeypatch):
    calls = []
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: calls.append(1) or _FakeResponse({}))
    assert services.get_musicbrainz_links("") == {}
    assert services.get_musicbrainz_links(None) == {}
    assert calls == []


def test_musicbrainz_links_are_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: calls.append(1) or _FakeResponse(_relations_payload()))

    first = services.get_musicbrainz_links(MBID)
    second = services.get_musicbrainz_links(MBID)

    assert first == second
    assert len(calls) == 1

    # Links are held for the long TTL; past it they're refetched.
    services._mb_links_cache[MBID]["at"] -= services._MB_LINKS_TTL + 1
    services.get_musicbrainz_links(MBID)
    assert len(calls) == 2


def test_musicbrainz_failure_is_retried_on_the_short_ttl(monkeypatch):
    calls = []
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: calls.append(1) or _FakeResponse({}, status_code=503))

    assert services.get_musicbrainz_links(MBID) == {}
    assert len(calls) == 1

    services._mb_links_cache[MBID]["at"] -= services._MB_LINKS_NEG_TTL + 1
    services.get_musicbrainz_links(MBID)
    assert len(calls) == 2


# ── get_spotify_artist_by_id ─────────────────────────────────────────

def _spotify_artist_payload():
    return {
        "name": "Radiohead",
        "popularity": 82,
        "followers": {"total": 1234},
        "genres": ["art rock", "alternative", "indie", "britpop", "extra"],
        "external_urls": {"spotify": "https://open.spotify.com/artist/abc"},
        "images": [{"url": "big.jpg"}, {"url": "medium.jpg"}],
    }


def test_spotify_by_id_maps_the_exact_dict_shape(monkeypatch):
    monkeypatch.setattr(services, "get_spotify_token", lambda: "fake-token")
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        return _FakeResponse(_spotify_artist_payload())

    monkeypatch.setattr(services.http_requests, "get", fake_get)

    assert services.get_spotify_artist_by_id("abc123") == {
        "popularity": 82,
        "followers": 1234,
        "genres": ["art rock", "alternative", "indie", "britpop"],
        "spotify_url": "https://open.spotify.com/artist/abc",
        "image": "medium.jpg",
    }
    assert captured["url"] == "https://api.spotify.com/v1/artists/abc123"


def test_spotify_by_id_needs_no_name_guard(monkeypatch):
    # The whole point of the id path: the payload's name is irrelevant
    # because MusicBrainz already told us this is the right artist. A
    # name-collision twin would be rejected by the search path.
    monkeypatch.setattr(services, "get_spotify_token", lambda: "fake-token")
    payload = dict(_spotify_artist_payload(), name="Some Other Spelling")
    monkeypatch.setattr(services.http_requests, "get", lambda *a, **k: _FakeResponse(payload))
    assert services.get_spotify_artist_by_id("abc123")["popularity"] == 82


def test_spotify_by_id_non_200_returns_empty(monkeypatch):
    monkeypatch.setattr(services, "get_spotify_token", lambda: "fake-token")
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: _FakeResponse({}, status_code=404))
    assert services.get_spotify_artist_by_id("abc123") == {}


def test_spotify_by_id_never_logs_the_token(monkeypatch, caplog):
    monkeypatch.setattr(services, "get_spotify_token", lambda: "super-secret-token")
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: _FakeResponse({}, status_code=429))
    with caplog.at_level("WARNING"):
        services.get_spotify_artist_by_id("abc123")
    assert "super-secret-token" not in caplog.text
    assert "quota exceeded (429)" in caplog.text


def test_spotify_by_id_is_cached(monkeypatch):
    monkeypatch.setattr(services, "get_spotify_token", lambda: "fake-token")
    calls = []
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: calls.append(1) or _FakeResponse(_spotify_artist_payload()))

    services.get_spotify_artist_by_id("abc123")
    services.get_spotify_artist_by_id("abc123")
    assert len(calls) == 1


def test_spotify_by_id_empty_id_makes_no_request(monkeypatch):
    calls = []
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: calls.append(1) or _FakeResponse({}))
    assert services.get_spotify_artist_by_id("") == {}
    assert calls == []


# ── get_deezer_artist_by_id ──────────────────────────────────────────

def test_deezer_by_id_maps_image_and_fans(monkeypatch):
    captured = {}

    def fake_get(url, timeout=None):
        captured["url"] = url
        return _FakeResponse({"picture_medium": "https://cdn.deezer.com/p.jpg", "nb_fan": 4200})

    monkeypatch.setattr(services.http_requests, "get", fake_get)

    assert services.get_deezer_artist_by_id("399") == {
        "image": "https://cdn.deezer.com/p.jpg", "nb_fan": 4200,
    }
    assert captured["url"] == "https://api.deezer.com/artist/399"


def test_deezer_by_id_error_body_with_http_200_is_a_failure(monkeypatch):
    # Deezer reports a bad id in the body, not the status code.
    monkeypatch.setattr(services.http_requests, "get", lambda *a, **k: _FakeResponse(
        {"error": {"type": "DataException", "message": "no data"}}))
    assert services.get_deezer_artist_by_id("999999") == {"image": "", "nb_fan": 0}


def test_deezer_by_id_applies_the_blank_image_placeholder_rule(monkeypatch):
    placeholder = f"https://cdn.deezer.com/{services.DEEZER_BLANK_IMAGE_HASH}.jpg"
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: _FakeResponse({"picture_medium": placeholder, "nb_fan": 5}))
    assert services.get_deezer_artist_by_id("399")["image"] == ""


def test_deezer_by_id_non_200_returns_empty(monkeypatch):
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: _FakeResponse({}, status_code=503))
    assert services.get_deezer_artist_by_id("399") == {"image": "", "nb_fan": 0}


def test_deezer_by_id_is_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(services.http_requests, "get", lambda *a, **k: calls.append(1) or _FakeResponse(
        {"picture_medium": "https://cdn.deezer.com/p.jpg", "nb_fan": 1}))

    services.get_deezer_artist_by_id("399")
    services.get_deezer_artist_by_id("399")
    assert len(calls) == 1


# ── get_artist_media orchestration ───────────────────────────────────

def _explode(*a, **k):
    raise AssertionError("name search must not be used when MBID links resolved")


def test_media_with_both_links_uses_ids_and_never_name_searches(monkeypatch):
    monkeypatch.setattr(services, "get_musicbrainz_links",
                        lambda mbid: {"spotify_id": "SP1", "deezer_id": "DZ1"})
    monkeypatch.setattr(services, "get_spotify_artist_by_id",
                        lambda sid: {"popularity": 50, "image": "sp.jpg"} if sid == "SP1" else {})
    monkeypatch.setattr(services, "get_deezer_artist_by_id",
                        lambda did: {"image": "dz.jpg", "nb_fan": 7} if did == "DZ1" else {})
    # If the linked ids resolved, a name search would only risk the
    # collision this whole feature exists to prevent.
    monkeypatch.setattr(services, "get_spotify_artist", _explode)
    monkeypatch.setattr(services, "get_deezer_artist_by_name", _explode)

    assert services.get_artist_media("Radiohead", MBID) == {
        "spotify": {"popularity": 50, "image": "sp.jpg"},
        "deezer_image": "dz.jpg",
        "deezer_fans": 7,
    }


def test_media_falls_back_to_name_search_when_no_links_found(monkeypatch):
    monkeypatch.setattr(services, "get_musicbrainz_links",
                        lambda mbid: {"spotify_id": None, "deezer_id": None})
    monkeypatch.setattr(services, "get_spotify_artist", lambda name: {"popularity": 11})
    monkeypatch.setattr(services, "get_deezer_artist_by_name",
                        lambda name: {"image": "name.jpg", "nb_fan": 3})

    assert services.get_artist_media("Radiohead", MBID) == {
        "spotify": {"popularity": 11},
        "deezer_image": "name.jpg",
        "deezer_fans": 3,
    }


def test_media_without_an_mbid_uses_name_search_and_skips_musicbrainz(monkeypatch):
    mb_calls = []
    monkeypatch.setattr(services, "get_musicbrainz_links",
                        lambda mbid: mb_calls.append(1) or {})
    monkeypatch.setattr(services, "get_spotify_artist", lambda name: {"popularity": 11})
    monkeypatch.setattr(services, "get_deezer_artist_by_name",
                        lambda name: {"image": "name.jpg", "nb_fan": 3})

    media = services.get_artist_media("Radiohead")

    assert media["spotify"] == {"popularity": 11}
    assert media["deezer_image"] == "name.jpg"
    assert mb_calls == []


def test_media_falls_back_per_provider_not_all_or_nothing(monkeypatch):
    # Spotify linked and resolving, Deezer link missing: the Spotify result
    # must still come from the trusted id while only Deezer falls back.
    monkeypatch.setattr(services, "get_musicbrainz_links",
                        lambda mbid: {"spotify_id": "SP1", "deezer_id": None})
    monkeypatch.setattr(services, "get_spotify_artist_by_id", lambda sid: {"popularity": 50})
    monkeypatch.setattr(services, "get_spotify_artist", _explode)
    monkeypatch.setattr(services, "get_deezer_artist_by_name",
                        lambda name: {"image": "name.jpg", "nb_fan": 3})

    media = services.get_artist_media("Radiohead", MBID)
    assert media["spotify"] == {"popularity": 50}
    assert media["deezer_image"] == "name.jpg"


def test_media_falls_back_when_the_linked_id_fetch_itself_fails(monkeypatch):
    # A linked id that 404s/errors shouldn't leave the page with nothing —
    # name search is still better than the letter-avatar.
    monkeypatch.setattr(services, "get_musicbrainz_links",
                        lambda mbid: {"spotify_id": "SP1", "deezer_id": "DZ1"})
    monkeypatch.setattr(services, "get_spotify_artist_by_id", lambda sid: {})
    monkeypatch.setattr(services, "get_deezer_artist_by_id",
                        lambda did: {"image": "", "nb_fan": 0})
    monkeypatch.setattr(services, "get_spotify_artist", lambda name: {"popularity": 11})
    monkeypatch.setattr(services, "get_deezer_artist_by_name",
                        lambda name: {"image": "name.jpg", "nb_fan": 3})

    assert services.get_artist_media("Radiohead", MBID) == {
        "spotify": {"popularity": 11},
        "deezer_image": "name.jpg",
        "deezer_fans": 3,
    }


# ── get_deezer_artist_by_name (extracted from views_artist) ──────────

def test_deezer_by_name_keeps_the_name_guard(monkeypatch):
    monkeypatch.setattr(services.http_requests, "get", lambda *a, **k: _FakeResponse(
        {"total": 1, "data": [{"name": "Blake Shelton",
                               "picture_medium": "wrong.jpg", "nb_fan": 9}]}))
    assert services.get_deezer_artist_by_name("BlakeNor") == {"image": "", "nb_fan": 0}


def test_deezer_by_name_accepts_a_matching_result(monkeypatch):
    monkeypatch.setattr(services.http_requests, "get", lambda *a, **k: _FakeResponse(
        {"total": 1, "data": [{"name": "Radiohead",
                               "picture_medium": "right.jpg", "nb_fan": 9}]}))
    assert services.get_deezer_artist_by_name("Radiohead") == {"image": "right.jpg", "nb_fan": 9}


def test_deezer_by_name_no_results_returns_empty(monkeypatch):
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: _FakeResponse({"total": 0}))
    assert services.get_deezer_artist_by_name("Nobody") == {"image": "", "nb_fan": 0}
